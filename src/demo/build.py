"""Build the bundled demo extract: `make demo-extract`.

    uv run python -m src.demo.build [--output PATH] [--skip-models]

docs/project_rules.md §2 locks the hosted demo to a bundled Parquet/DuckDB extract rather
than a live database. This module is the build step that produces it: it reads
the curated PostgreSQL views and the Phase 4 model artifacts and writes one
DuckDB file plus a provenance note beside it.

FOUR DECISIONS WORTH THE READING TIME
-------------------------------------
**The views are COPIED, never recomputed.** Every dataset that exists as a
`vw_` view is `select * from` that view. A second implementation of an executive
KPI in Python would be a second definition of it, and the dashboard's figures
would then reconcile to the SQL control queries only by coincidence. §7 requires
"dashboard totals reconcile to SQL control queries", and copying is the only way
to make that true by construction rather than by vigilance.

**Model A is REFITTED from the committed matrix, not read from a pickle.**
`artifacts/features/model_a_training_matrix.parquet` is committed, so the
champion — regularized logistic on the fit fold, isotonic on the calibrate fold —
reproduces exactly with no warehouse: ROC-AUC 0.6254 uncalibrated and 0.6185
calibrated on the forward test fold, the figures docs/model_card.md reports. That
makes the bundle rebuildable by anyone who clones the repo, and it avoids
shipping a serialized estimator whose validity depends on the reader's scikit-learn
version matching ours. The refit is asserted against the model card's numbers
below, so a silent divergence fails the build rather than shipping.

**In-sample scores are shipped, and LABELLED.** `model_a_scores` covers all
20,867 claims because the dashboard's queue and claim lookup need a score for any
claim a user picks, but 16,694 of those rows were used to fit or calibrate the
model and their scores are in-sample. Every row therefore carries its `fold`, and
every page reporting model PERFORMANCE restricts to `fold = 'test'` and says so.
Dropping the in-sample rows would have been the other honest option; hiding which
is which would not.

**The Model C queue is COPIED from the run's own CSVs, and its headers are
checked.** The queue depends on an appeal model fitted on warehouse data, so it
cannot be reproduced from a committed artifact the way Model A can. The build
therefore reads what `make train-appeal` wrote — and refuses artifacts whose
column names predate the [QUEUE-PREFIX] rename, because a demo that ships
`recoverable_amt` instead of `sim_recoverable_amt` reintroduces exactly the
defect that rename closed, at the one boundary where a reader sees the name and
nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.demo import spec
from src.demo.bundle import BUILD_INFO_TABLE, DEFAULT_BUNDLE_PATH, MANIFEST_TABLE

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG_PATH = REPO_ROOT / "config" / "model.yaml"
SOURCES_CONFIG_PATH = REPO_ROOT / "config" / "sources.yaml"
COMMITTED_MATRIX = REPO_ROOT / "artifacts" / "features" / "model_a_training_matrix.parquet"
MODEL_A_ARTIFACTS = REPO_ROOT / "models_artifacts" / "model_a"
MODEL_C_ARTIFACTS = REPO_ROOT / "models_artifacts" / "model_c"
BUILD_OUTPUT_PATHS = (
    DEFAULT_BUNDLE_PATH.relative_to(REPO_ROOT).as_posix(),
    (DEFAULT_BUNDLE_PATH.parent / "README.md").relative_to(REPO_ROOT).as_posix(),
)

#: The two headline figures the refit must reproduce, from docs/model_card.md §1.
#: Checked to 4 decimal places, which is the precision the card publishes.
MODEL_CARD_ROC_UNCALIBRATED = 0.6254
MODEL_CARD_ROC_CALIBRATED = 0.6185
_ROC_TOLERANCE = 5e-5


class BuildError(RuntimeError):
    """A build that cannot produce an honest bundle stops, loudly."""


# ---------------------------------------------------------------------------
# Provenance of the build itself
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def build_stamp() -> dict[str, Any]:
    """Who built this bundle, from what, and whether the tree was clean.

    A bundle with no commit stamp cannot be pinned by inspection, which is the
    [SHA-STAMP] finding applied to this artifact. `git_tree_dirty` is recorded
    rather than suppressed: a bundle built from uncommitted work is legitimate
    during development and misleading if it ships without saying so.
    """
    sha = _git("rev-parse", "HEAD")
    commit_time = _git("show", "-s", "--format=%cI", "HEAD")
    dirty = bool(
        _git(
            "status",
            "--porcelain",
            "--",
            ".",
            *(f":(exclude){path}" for path in BUILD_OUTPUT_PATHS),
        )
    )
    built_at_utc = (
        pd.Timestamp(commit_time).tz_convert("UTC").isoformat()
        if commit_time
        else pd.Timestamp.utcnow().isoformat()
    )
    return {
        "git_commit": sha or "unknown",
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "git_tree_dirty": dirty,
        # A source-derived timestamp makes clean builds byte-reproducible. The
        # wall-clock capture time belongs in release evidence, not artifact identity.
        "built_at_utc": built_at_utc,
    }


def source_vintages() -> dict[str, str]:
    """The vintages behind the data, for the skew disclosure on the app.

    Read from config/sources.yaml rather than restated, so a re-pull at a new
    vintage changes what the app says without anyone remembering to edit it.
    """
    config = yaml.safe_load(SOURCES_CONFIG_PATH.read_text())["sources"]
    wanted = {
        "claims": "cms_synthetic_claims",
        "facilities": "hospital_general_information",
        "providers": "medicare_providers",
        "icd10": "icd10",
        "hcpcs": "hcpcs",
        "ms_drg": "ms_drg",
    }
    return {label: str(config[key]["vintage"]) for label, key in wanted.items() if key in config}


# ---------------------------------------------------------------------------
# The warehouse half
# ---------------------------------------------------------------------------


def read_warehouse_datasets(engine) -> dict[str, pd.DataFrame]:  # noqa: ANN001
    """Copy every declared curated view out of PostgreSQL, unmodified."""
    frames: dict[str, pd.DataFrame] = {}
    for dataset in spec.WAREHOUSE_DATASETS:
        frame = pd.read_sql(dataset.query, engine)
        if frame.empty:
            raise BuildError(
                f"{dataset.name} came back empty. The views are applied by "
                "`make views`; an empty contract view means the warehouse is not "
                "loaded, not that the bundle should ship without it."
            )
        frames[dataset.name] = frame
    return frames


# ---------------------------------------------------------------------------
# The Model A half — refitted from the committed matrix
# ---------------------------------------------------------------------------


def _fold_labels(folds, n_rows: int) -> np.ndarray:  # noqa: ANN001
    labels = np.array(["unassigned"] * n_rows, dtype=object)
    labels[folds.fit] = "fit"
    labels[folds.calibrate] = "calibrate"
    labels[folds.test] = "test"
    return labels


def build_model_a_datasets(explain: bool = True) -> dict[str, pd.DataFrame]:
    """Refit Model A's champion from the committed matrix and score every claim.

    Returns `model_a_scores`, and — when `explain` — `model_a_reason_codes` and
    `model_a_shap_global`. The SHAP pass runs on the tree model, matching
    docs/model_card.md §1: the published explanations are tree SHAP, and the
    champion being logistic does not change which model the attributions came
    from.
    """
    from src.features.build import MODEL_A_FEATURES
    from src.models.baselines import logistic_baseline
    from src.models.calibrate import calibrate, method_from_config
    from src.models.preprocess import prepare_matrix
    from src.models.train import make_folds

    if not COMMITTED_MATRIX.is_file():
        raise BuildError(
            f"{COMMITTED_MATRIX.relative_to(REPO_ROOT)} is missing. It is committed on purpose "
            "(it is how the leakage probes run on a clean clone); regenerate with `make features`."
        )

    config = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    feature_set = MODEL_A_FEATURES
    frame = pd.read_parquet(COMMITTED_MATRIX)
    frame[feature_set.time_column] = pd.to_datetime(frame[feature_set.time_column])

    folds = make_folds(frame, config)
    matrix = prepare_matrix(frame, feature_set)
    label = frame[feature_set.label].astype(int).to_numpy()

    champion = logistic_baseline(feature_set, config)
    champion.fit(matrix[folds.fit], label[folds.fit])
    calibrated = calibrate(
        champion,
        matrix[folds.calibrate],
        label[folds.calibrate],
        method_from_config(config),
    )

    raw = np.asarray(champion.predict_proba(matrix))[:, 1]
    cal = np.asarray(calibrated.predict_proba(matrix))[:, 1]

    _assert_matches_model_card(label[folds.test], raw[folds.test], cal[folds.test])

    scores = pd.DataFrame(
        {
            "claim_sk": frame["claim_sk"].to_numpy(),
            "fold": _fold_labels(folds, len(frame)),
            "sim_denial_risk_uncalibrated": raw,
            "sim_denial_risk": cal,
            "sim_denial_flag": label.astype(bool),
            "sim_submission_date": frame[feature_set.time_column].to_numpy(),
        }
    )
    # The capacity-constrained operating point, applied to the test fold only —
    # a decile cut computed over in-sample rows would be a different cut.
    test_scores = scores.loc[scores["fold"] == "test", "sim_denial_risk"]
    threshold = float(test_scores.quantile(0.90))
    scores["sim_top_decile_flag"] = scores["sim_denial_risk"] >= threshold

    datasets = {"model_a_scores": scores}
    if not explain:
        return datasets

    datasets.update(_model_a_explanations(frame, folds, feature_set, config))
    return datasets


def _assert_matches_model_card(label: np.ndarray, raw: np.ndarray, calibrated: np.ndarray) -> None:
    """The refit must land on the published numbers, or nothing here is quotable.

    A demo that refits the champion and reports figures a few thousandths from
    docs/model_card.md is worse than one that does not refit at all: the page and
    the card would disagree and the reader would have no way to tell which is the
    model. Checked in both directions, at the precision the card publishes.
    """
    from sklearn.metrics import roc_auc_score

    observed = {
        "uncalibrated": (float(roc_auc_score(label, raw)), MODEL_CARD_ROC_UNCALIBRATED),
        "calibrated": (float(roc_auc_score(label, calibrated)), MODEL_CARD_ROC_CALIBRATED),
    }
    drifted = {
        name: (round(got, 6), expected)
        for name, (got, expected) in observed.items()
        if abs(round(got, 4) - expected) > _ROC_TOLERANCE
    }
    if drifted:
        raise BuildError(
            "the Model A refit does not reproduce docs/model_card.md: "
            + "; ".join(
                f"{name} ROC-AUC {got} against a published {expected}"
                for name, (got, expected) in drifted.items()
            )
            + ". Either the committed matrix, config/model.yaml or a library version has "
            "moved. Do NOT relax this check to ship — find what changed, and update the "
            "model card and this constant together if the new number is the correct one."
        )


def _model_a_explanations(
    frame: pd.DataFrame,
    folds,
    feature_set,
    config: dict,  # noqa: ANN001
) -> dict[str, pd.DataFrame]:
    """Per-claim SHAP drivers for the forward test fold, as reason codes.

    Explanations are produced for the TEST fold only. A waterfall over a claim
    the model was fitted on describes the fit, not a forward prediction, and the
    dashboard would have no way to say which kind of claim a user had clicked.
    """
    from src.models.advanced import gradient_boosted_model
    from src.models.explain import REASON_CODES, _owner_of, explain_tree_model
    from src.models.preprocess import prepare_matrix

    test_frame = frame.loc[folds.test].reset_index(drop=True)
    tree = gradient_boosted_model(feature_set, config)
    tree.fit(
        prepare_matrix(frame.loc[folds.fit], feature_set),
        frame.loc[folds.fit, feature_set.label].astype(int).to_numpy(),
    )

    result = explain_tree_model(
        tree.named_steps["preprocess"],
        tree.named_steps["estimator"],
        test_frame,
        feature_set,
        max_rows=None,
        seed=int(config["seed"]),
    )

    declared = list(feature_set.names)
    owners = np.array([_owner_of(name, declared) for name in result.encoded_names])
    # Sum the encoded columns back onto their declared feature, for every row at
    # once: a one-hot payer must appear as one net contribution, not forty.
    unique_owners, inverse = np.unique(owners, return_inverse=True)
    rolled = np.zeros((result.values.shape[0], len(unique_owners)))
    np.add.at(rolled.T, inverse, result.values.T)

    top_n = 5
    order = np.argsort(-np.abs(rolled), axis=1)[:, :top_n]
    claim_sk = test_frame.loc[result.row_index, "claim_sk"].to_numpy()

    records: list[dict[str, Any]] = []
    for row in range(rolled.shape[0]):
        for rank, column in enumerate(order[row], start=1):
            feature = str(unique_owners[column])
            contribution = float(rolled[row, column])
            code, action = REASON_CODES.get(feature, ("UNMAPPED", "-- no mapped action --"))
            records.append(
                {
                    "claim_sk": int(claim_sk[row]),
                    "sim_driver_rank": rank,
                    "feature": feature,
                    "sim_shap_contribution": contribution,
                    "sim_direction": "raises risk" if contribution > 0 else "lowers risk",
                    "sim_reason_code": code,
                    "sim_analyst_action": action,
                }
            )

    # Both value columns carry the marker: a SHAP importance is an attribution of
    # a model fitted on a SIMULATED label, so it moves when the simulation moves.
    # sim_reason_code and sim_analyst_action carry it too: the mapping table is
    # ours, but what they say is a statement about a predicted SIMULATED denial.
    # `feature` stays bare — it names a feature column rather than a value.
    global_importance = result.global_importance.rename(
        columns={"mean_abs_shap": "sim_mean_abs_shap", "share": "sim_share_of_importance"}
    )
    return {
        "model_a_reason_codes": pd.DataFrame.from_records(records),
        "model_a_shap_global": global_importance,
    }


# ---------------------------------------------------------------------------
# The Model C half — copied from the run, with its headers checked
# ---------------------------------------------------------------------------


def _check_queue_columns(columns: list[str], path: pathlib.Path) -> None:
    stripped = spec.stripped_marker_columns(columns)
    if stripped:
        raise BuildError(
            f"{path.relative_to(REPO_ROOT)} carries pre-rename column names {stripped}. "
            + spec.QUEUE_RENAME_ADVICE
            + " Expected: "
            + ", ".join(
                f"{old} -> {new}" for old, new in sorted(spec.QUEUE_SIM_MARKER_RENAMES.items())
            )
            + "."
        )


def build_model_c_datasets() -> dict[str, pd.DataFrame]:
    """The Expected Net Recovery worklist, both modes, as the run wrote it."""
    modes = {
        "backtest": MODEL_C_ARTIFACTS / "work_queue_backtest.csv",
        "live_snapshot": MODEL_C_ARTIFACTS / "work_queue_live_snapshot.csv",
    }
    missing = [str(path.relative_to(REPO_ROOT)) for path in modes.values() if not path.is_file()]
    if missing:
        raise BuildError(
            f"missing Model C artifacts: {missing}. Run `make train-appeal` first — the appeal "
            "model is fitted on warehouse data and, unlike Model A, cannot be reproduced from a "
            "committed matrix."
        )

    frames = []
    for mode, path in modes.items():
        frame = pd.read_csv(path)
        _check_queue_columns(list(frame.columns), path)
        frame.insert(0, "queue_mode", mode)
        frames.append(frame)
    return {"model_c_work_queue": pd.concat(frames, ignore_index=True)}


def build_metrics_dataset() -> dict[str, pd.DataFrame]:
    """Both metrics.json files, verbatim, so the app reports what the run reported."""
    records = []
    for model, directory in (("A", MODEL_A_ARTIFACTS), ("C", MODEL_C_ARTIFACTS)):
        path = directory / "metrics.json"
        if not path.is_file():
            raise BuildError(
                f"missing {path.relative_to(REPO_ROOT)}. Run `make train` and "
                "`make train-appeal`; the dashboard reports the run's own numbers rather "
                "than recomputing them, so there is nothing honest to show without it."
            )
        payload = json.loads(path.read_text())
        records.append(
            {
                "model": model,
                "generated_at_utc": str(payload.get("generated_at_utc", "")),
                "provenance": str(payload.get("provenance", "")),
                "sim_metrics_json": json.dumps(payload),
            }
        )
    return {"model_metrics": pd.DataFrame.from_records(records)}


# ---------------------------------------------------------------------------
# Manifest and write
# ---------------------------------------------------------------------------


def build_manifest(
    frames: dict[str, pd.DataFrame], expected: set[str] | None = None
) -> pd.DataFrame:
    """The bundle's own provenance register, one row per dataset.

    Declared classification from `src/demo/spec.py`, measured shape and measured
    simulated columns from the frames actually being written. If the two ever
    disagree — a dataset in the file that nothing declares, or the reverse — the
    write refuses.

    `expected` narrows what a build is required to produce, and exists for one
    caller: `--skip-models`, which builds a warehouse-only bundle for iterating
    on the extract without a training run. It is never a way to ship a short
    bundle quietly — `main()` prints the incompleteness to stderr and the
    manifest written into the file records only what is actually there, so a
    partial bundle announces itself to anyone who opens it.
    """
    declared = set(spec.DATASETS_BY_NAME)
    present = set(frames)
    required = (expected if expected is not None else declared) - spec.SELF_DESCRIBING_TABLES
    if undeclared := sorted(present - declared):
        raise BuildError(
            f"{undeclared} would be written into the bundle and are not declared in "
            "src/demo/spec.py. docs/project_rules.md §3.3: a committed data file whose contents nothing "
            "classifies is a provenance hole. Declare them with a classification first."
        )
    if unwritten := sorted(required - present):
        raise BuildError(
            f"{unwritten} are declared in src/demo/spec.py and were not produced. Either the "
            "build skipped a step or the declaration is stale; both need a decision, and "
            "shipping a bundle that silently lacks a declared dataset is neither."
        )

    rows = []
    for name in sorted(frames):
        frame = frames[name]
        dataset = spec.dataset(name)
        simulated = spec.simulated_columns(list(frame.columns))
        rows.append(
            {
                "dataset": name,
                "provenance": dataset.provenance,
                "contains_simulated": dataset.contains_simulated,
                "grain": dataset.grain,
                "rows": int(len(frame)),
                "columns": int(frame.shape[1]),
                "simulated_columns": ", ".join(simulated),
                "note": dataset.note,
            }
        )
    return pd.DataFrame.from_records(rows)


def write_bundle(
    frames: dict[str, pd.DataFrame],
    output: pathlib.Path,
    expected: set[str] | None = None,
    stamp: dict[str, Any] | None = None,
) -> pathlib.Path:
    """Write every frame plus the manifest and build stamp into one DuckDB file."""
    import duckdb

    manifest = build_manifest(frames, expected=expected)
    stamp = stamp or build_stamp()
    vintages = source_vintages()
    build_info = pd.DataFrame(
        [
            {
                **stamp,
                "source_vintages": json.dumps(vintages),
                "dataset_names": json.dumps(sorted(set(frames) | spec.SELF_DESCRIBING_TABLES)),
                "contains_simulated": True,
                "notice": (
                    "Every sim_-prefixed column in this bundle is SIMULATED by this "
                    "project's adjudication layer (docs/project_rules.md §3). The underlying claims are "
                    "official CMS synthetic Medicare records. The multi-payer dimension is "
                    "100% simulated - Medicare fee-for-service has one payer."
                ),
            }
        ]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    candidate = output.with_name(f".{output.name}.candidate")
    candidate.unlink(missing_ok=True)

    try:
        connection = duckdb.connect(str(candidate))
        try:
            writable = {**frames, MANIFEST_TABLE: manifest, BUILD_INFO_TABLE: build_info}
            for name in sorted(writable):
                # Registered explicitly rather than relying on DuckDB's replacement
                # scan of local variables: the scan resolves a bare name out of the
                # calling frame, which works until someone renames the loop variable.
                connection.register("_source", writable[name])
                connection.execute(f'create table "{name}" as select * from _source')
                connection.unregister("_source")
        finally:
            connection.close()

        if not _bundles_logically_equal(output, candidate):
            os.replace(candidate, output)
    finally:
        candidate.unlink(missing_ok=True)
    return output


def _bundles_logically_equal(existing: pathlib.Path, candidate: pathlib.Path) -> bool:
    """Whether two DuckDB artifacts have the same schemas and row multisets.

    DuckDB checkpoints may carry different unused padding bytes across fresh
    writes. The committed artifact is content-addressed, so an equivalent rebuild
    keeps its existing bytes instead of changing its SHA for storage-only noise.
    """
    import duckdb

    if not existing.is_file() or not candidate.is_file():
        return False

    def sql_path(path: pathlib.Path) -> str:
        return path.resolve().as_posix().replace("'", "''")

    connection = duckdb.connect()
    try:
        connection.execute(f"attach '{sql_path(existing)}' as existing (read_only)")
        connection.execute(f"attach '{sql_path(candidate)}' as candidate (read_only)")
        columns = connection.execute(
            """
            select database_name, schema_name, table_name, column_name, data_type,
                   column_index
            from duckdb_columns()
            where database_name in ('existing', 'candidate')
            order by schema_name, table_name, column_index
            """
        ).fetchall()
        existing_columns = [row[1:] for row in columns if row[0] == "existing"]
        candidate_columns = [row[1:] for row in columns if row[0] == "candidate"]
        if existing_columns != candidate_columns:
            return False

        tables = connection.execute(
            """
            select schema_name, table_name
            from duckdb_tables()
            where database_name = 'existing'
            order by schema_name, table_name
            """
        ).fetchall()
        for schema_name, table_name in tables:
            schema = schema_name.replace('"', '""')
            table = table_name.replace('"', '""')
            existing_table = f'existing."{schema}"."{table}"'
            candidate_table = f'candidate."{schema}"."{table}"'
            has_delta = connection.execute(
                f"""
                select exists(
                    (select * from {existing_table} except all select * from {candidate_table})
                    union all
                    (select * from {candidate_table} except all select * from {existing_table})
                )
                """
            ).fetchone()[0]
            if has_delta:
                return False
        return True
    except duckdb.Error:
        return False
    finally:
        connection.close()


PROVENANCE_NOTE_NAME = "README.md"


def write_provenance_note(
    output: pathlib.Path,
    manifest: pd.DataFrame,
    stamp: dict[str, Any] | None = None,
) -> pathlib.Path:
    """A note beside the bundle, for a reader who finds the file and not the repo.

    Same reasoning as the artifact READMEs the training runs write: a data file is
    read alone as often as it is read in context, and `rcm_demo.duckdb` opened in
    a DuckDB shell shows five payers with different denial rates and nothing
    saying the payer dimension does not exist.
    """
    stamp = stamp or build_stamp()
    lines = [
        "# Demo data bundle — SIMULATED OUTCOMES",
        "",
        "`rcm_demo.duckdb` is the bundled extract the Streamlit dashboard and the FastAPI",
        "service read when no PostgreSQL warehouse is configured. It is committed on purpose:",
        "docs/project_rules.md §2 locks the hosted demo to a bundled extract, so this file is the only way",
        "a deployed app has data at all.",
        "",
        "**Every outcome in this bundle is SIMULATED.** The claims underneath are official CMS",
        "*synthetic* Medicare records (SOURCE, never modified). The denials, appeals, payments,",
        "workflow events, operating costs and the entire multi-payer dimension are generated by",
        "this project's own simulation layer and are prefixed `sim_` wherever they appear.",
        "Medicare fee-for-service has exactly ONE payer; the five payer archetypes in",
        "`vw_payer_performance` are invented and are named after no real insurer. Real facility",
        "names reached this bundle through a seeded random crosswalk and are display-only —",
        "4,876 synthetic providers map onto 2,857 real CCNs (worst case 8:1) and onto only",
        "2,816 distinct display names (worst case 15:1, because real CMS facilities share names",
        "across sites). Neither a CCN nor a facility name is a stable entity key here, and every",
        "provider figure is grouped on the synthetic `prvdr_num`.",
        "",
        "The reference files are also NOT contemporaneous with the claims. The claims are",
        "vintage 2023-04 and their code sets match (ICD-10-CM/PCS FY2023, HCPCS 2023, MS-DRG v40",
        "FY2023), but Hospital General Information is vintage 2026-04 and the Medicare Physician",
        "& Other Practitioners file is data year 2024 — roughly three years newer. Facility type,",
        "facility state and provider specialty are therefore what the reference file recorded",
        "about three years AFTER the claim was filed.",
        "",
        f"Built from commit `{stamp['git_commit']}`"
        + (" (tree dirty)" if stamp["git_tree_dirty"] else "")
        + f" at {stamp['built_at_utc']} by `make demo-extract`.",
        "",
        "Regenerate with `make demo-extract`. Do not edit this file by hand; the dashboard",
        "reconciles its figures to the SQL control queries in `sql/quality/view_reconciliation.py`",
        "and a hand-edited bundle would break that silently.",
        "",
        "## Datasets",
        "",
        "| dataset | provenance | rows | grain |",
        "|---|---|---:|---|",
    ]
    for row in manifest.itertuples():
        lines.append(f"| `{row.dataset}` | {row.provenance} | {row.rows:,} | {row.grain} |")
    lines += [
        "",
        "The bundle carries its own machine-readable register in the `demo_manifest` table,",
        "including the simulated columns of every dataset, and its build stamp in",
        "`demo_build_info`. `src/demo/spec.py` is the declaration both are generated from.",
        "",
    ]
    path = output.parent / PROVENANCE_NOTE_NAME
    path.write_text("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build(output: pathlib.Path, skip_models: bool = False) -> dict[str, Any]:
    """Read the warehouse and the artifacts, write the bundle. Returns a summary."""
    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    started = time.perf_counter()
    url = database_url()
    if not url:
        raise BuildError(
            "no database URL. The demo extract is BUILT from PostgreSQL even though it is "
            "READ without one; set POSTGRES_* or DATABASE_URL in .env and run `make views` "
            "first."
        )

    engine = create_engine(url)
    frames = read_warehouse_datasets(engine)
    engine.dispose()

    expected = {dataset.name for dataset in spec.WAREHOUSE_DATASETS}
    if not skip_models:
        frames.update(build_model_a_datasets())
        frames.update(build_model_c_datasets())
        frames.update(build_metrics_dataset())
        expected = set(spec.DATASETS_BY_NAME)

    manifest = build_manifest(frames, expected=expected)
    stamp = build_stamp()
    write_bundle(frames, output, expected=expected, stamp=stamp)
    note = write_provenance_note(output, manifest, stamp=stamp)

    return {
        "output": str(output),
        "note": str(note),
        "datasets": len(frames),
        "rows": int(sum(len(frame) for frame in frames.values())),
        "bytes": output.stat().st_size,
        "seconds": round(time.perf_counter() - started, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="warehouse views only; for iterating on the extract without a training run",
    )
    args = parser.parse_args(argv)

    try:
        summary = build(args.output, skip_models=args.skip_models)
    except BuildError as error:
        print(f"demo-extract FAILED: {error}", file=sys.stderr)
        return 1

    print(
        f"wrote {summary['output']} — {summary['datasets']} datasets, "
        f"{summary['rows']:,} rows, {summary['bytes'] / 1e6:.1f} MB, {summary['seconds']}s"
    )
    print(f"wrote {summary['note']}")
    if args.skip_models:
        print(
            "NOTE: --skip-models was used, so this bundle has no model datasets and is not "
            "shippable. Rebuild without it before committing.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
