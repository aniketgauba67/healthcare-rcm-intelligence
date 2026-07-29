"""Persisting the Model A training matrix, so the leakage guard can find it.

CLAUDE.md §4.1 requires an automated test that fails the build if a forbidden
column — or a column derived from one — enters a training matrix. That test is
`tests/leakage/test_training_matrix_guard.py`, and it cannot check a matrix it
cannot see. Its discovery contract accepts any one of three routes: the path in
`RCM_FEATURE_MATRIX`, a `*.parquet` / `*.csv` under `artifacts/features/`, or a
no-argument `src.features.build_training_matrix()`.

Before this module existed, `src/features/` held six modules and satisfied none
of them: the only builder took an `Engine`, and nothing was written to disk. The
consequence was not a red test — it was worse. The value probes (the ones that
catch a *renamed* or *rescaled* forbidden column, which is the only kind that
matters) SKIPPED, and a skip reads like a pass. This module closes that by
serving all three routes at once.

**The persisted matrix is checked in.** That is a deliberate choice and the
reason for the `!artifacts/features/` exception in `.gitignore`. The alternative
— regenerate it from Postgres whenever the guard runs — means the guard is live
only on a machine with a loaded warehouse, which is exactly the "green suite
over a warehouse nobody checked" failure this project has already hit once. A
committed matrix makes the §4.1 probes run on a clean clone, in CI, on real
feature values, every time. It is 20,867 rows of CMS *synthetic* claim facts and
`sim_`-prefixed SIMULATED adjudication inputs; no real patient, provider or
payer data exists anywhere in this project (CLAUDE.md §3).

**Only Model A's matrix belongs here.** The guard's forbidden set is Model A's,
so a Model C matrix dropped into `artifacts/features/` would fail it correctly
and for the wrong reason — Model C is *permitted* to see the denial. Model C
writes its frame to `models_artifacts/model_c/`, which the guard does not scan.

**Staleness is a real risk and is handled by measurement, not by trust.** The
sidecar manifest records the row count, the column list, the per-column null
rates, the split boundary and a digest of the leakage configuration that produced
it. `tests/leakage/test_persisted_matrix_is_current.py` rebuilds from Postgres and
compares (integration marker); the unit-level check verifies the manifest
describes the file sitting next to it.

**And the write itself is guarded, against git.** A training run against a
transiently degraded warehouse once persisted a matrix whose `diagnosis_count`
was entirely null, straight over the committed artifact, and the write landed
BEFORE the run failed loudly. So the file that exists specifically so the §4.1
probes can run without a warehouse was rewritable by the exact condition it
guards against. `persist_training_matrix` now measures what it is about to write
against the manifest **read from `git show HEAD:`** and refuses if the row count,
the column set or any column's null rate has moved.

Against git, and never against the copy on disk. Comparing to the previous run
reproduces the defect this project has now hit three times — a check that repairs
the condition it exists to detect, after which "was never bad" and "was bad and
got quietly rewritten" are indistinguishable. The on-disk manifest is written by
the same call that writes the bad matrix; only the committed one is evidence.

Refusal is loud and raises `ArtifactRewriteRefused`. There is no silent skip:
a skip is what let the original through. A genuine change — a new feature, a
reloaded warehouse — is made through `--allow-change` / `RCM_ALLOW_MATRIX_CHANGE`,
which still prints every deviation before proceeding. Overriding is cheap; doing
it by accident is not possible.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from src.features.build import LABEL, MODEL_A_FEATURES, TIME_COLUMN, build_model_a_frame
from src.features.leakage import load_model_config
from src.features.splits import split_from_config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "features"
MATRIX_PATH = ARTIFACT_DIR / "model_a_training_matrix.parquet"
MANIFEST_PATH = ARTIFACT_DIR / "model_a_training_matrix.json"

# The column the guard's temporal check reads. One split column only: the guard
# picks the first member of {is_train, split, fold} present in the frame and a
# frozenset has no order, so two of them would make which one it reads a
# coin toss.
SPLIT_COLUMN = "split"

# Keys under config/model.yaml whose contents decide what may enter the matrix.
# Their digest goes in the manifest so a config edit that widens the blacklist
# without a rebuild is visible rather than silent.
_LEAKAGE_KEYS = (
    "forbidden_features",
    "forbidden_tables",
    "forbidden_table_columns",
    "forbidden_derived_features",
    "forbidden_source_features",
    "forbidden_features_defensive",
    "forbidden_crosswalk_tables",
    "forbidden_crosswalk_display_features",
)


# How far a null rate may move before the write is refused. Small and absolute
# rather than relative: the failure this exists for took `diagnosis_count` from
# 0.0 to 1.0, and anything that survives a threshold this tight is a change
# somebody should be looking at anyway.
NULL_RATE_TOLERANCE = 0.005

# The deliberate override. Named on the command line or in the environment, never
# inferred, and it prints the deviations it is overriding.
ALLOW_CHANGE_ENV = "RCM_ALLOW_MATRIX_CHANGE"


class ArtifactRewriteRefused(RuntimeError):
    """The matrix about to be written disagrees with the committed one."""


def leakage_config_digest(config: dict) -> str:
    """A stable digest of every forbidden-column list in the model config."""
    payload = json.dumps({key: config.get(key) for key in _LEAKAGE_KEYS}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _file_digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def label_and_split(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add the `split` column the temporal-split probe reads.

    The split is recomputed from the config rather than passed in, so the
    persisted matrix always carries the split the config prescribes and cannot
    disagree with the one `train.py` fits on.
    """
    split = split_from_config(frame, config)
    out = frame.copy()
    out[SPLIT_COLUMN] = pd.Series(["test"] * len(out), index=out.index)
    out.loc[split.train, SPLIT_COLUMN] = "train"
    return out


def null_rates(frame: pd.DataFrame) -> dict[str, float]:
    """Share of nulls per column, rounded so the manifest stays byte-stable."""
    return {str(name): round(float(frame[name].isna().mean()), 6) for name in frame.columns}


def _build_manifest(stamped: pd.DataFrame, config: dict, parquet_digest: str) -> dict[str, Any]:
    """Everything the manifest records, computed from the frame and the config.

    Split out from the write so the content guard can build the manifest of the
    matrix it is ABOUT to write and compare that, rather than writing first and
    asking questions afterwards. Writing first is exactly how the degraded matrix
    landed.
    """
    split = split_from_config(stamped, config)
    # NO WALL CLOCK IN THIS MANIFEST. Every field below is a function of the
    # matrix content or the config that produced it, so any writer — `make
    # features`, `make train`, or a test that happens to train against live
    # Postgres — emits byte-identical bytes. A committed artifact that changed on
    # every test run trained reviewers to ignore its diff, which is exactly how a
    # real content change slips through; qa reverted a spurious timestamp diff
    # twice before this was removed. The build time of record is the git commit
    # date; `make features` also prints it to stdout for the operator.
    # NOR A GIT SHA, for the same reason — see src/models/run_stamp.py, which
    # stamps the gitignored model artifacts precisely because they carry no
    # commit of their own. This file is committed; its commit IS its stamp.
    return {
        "purpose": "Model A training matrix, discoverable by tests/leakage/ "
        "(CLAUDE.md §4.1). Regenerate with `make features`.",
        "provenance": "CMS synthetic claim facts (SOURCE) + sim_-prefixed SIMULATED "
        "adjudication inputs. No real patient, provider or payer data.",
        "rows": int(len(stamped)),
        "feature_count": len(MODEL_A_FEATURES.names),
        "features": list(MODEL_A_FEATURES.names),
        "passthrough": list(MODEL_A_FEATURES.passthrough),
        "label": LABEL,
        "time_column": TIME_COLUMN,
        "split_column": SPLIT_COLUMN,
        "split": {
            "cut_date": str(split.cut_date.date()),
            "train_rows": int(split.train.sum()),
            "test_rows": int(split.test.sum()),
        },
        # Recorded so the write guard has something to compare. The failure it
        # exists for was a column that went entirely null while the row count and
        # the column set stayed exactly right, so neither of those would have
        # caught it on its own.
        "null_rates": null_rates(stamped),
        "leakage_config_digest": leakage_config_digest(config),
        "parquet_sha256_16": parquet_digest,
    }


def _repo_root_for(path: pathlib.Path) -> pathlib.Path | None:
    """The git root the file lives under, asked of git rather than assumed.

    Derived from the path instead of hardcoded to REPO_ROOT because this project
    runs several agents in separate worktrees against one repository, and a
    hardcoded root silently disarms the guard for any path outside it — which is
    the failure mode a guard must not have.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, NotADirectoryError):
        return None
    return pathlib.Path(top) if top else None


def committed_manifest(
    path: pathlib.Path = MANIFEST_PATH, repo_root: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """The manifest as committed at HEAD, or None if git is not tracking it.

    Read with `git show HEAD:<path>` and NOT from the working tree. The working
    copy is written by the same call that would write a bad matrix, so it is not
    independent evidence of anything. None means there is no committed artifact
    to protect, which is a different condition from "the committed artifact
    agrees" and is reported as such.
    """
    root = repo_root or _repo_root_for(path)
    if root is None:
        return None  # not inside a git repository at all
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    try:
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def manifest_deviations(candidate: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    """How the matrix about to be written differs from the committed one.

    Row count, column set, per-column null rates — the three properties qa fixed
    the shape of. Returns human-readable lines because the operator reading them
    is the one who has to decide whether the change is real.
    """
    problems: list[str] = []

    if int(candidate["rows"]) != int(committed.get("rows", -1)):
        problems.append(f"row count {committed.get('rows')} -> {candidate['rows']}")

    def column_set(manifest: dict[str, Any]) -> set[str]:
        """The columns actually IN the file, which is not the same as the declared ones.

        `null_rates` is keyed on the frame's real columns. The `features` list is
        copied from MODEL_A_FEATURES and is therefore identical no matter what
        the frame contains — comparing that to itself would have made this check
        incapable of ever firing, which is worse than not having it.
        """
        rates = manifest.get("null_rates")
        if rates:
            return set(rates)
        return (
            set(manifest.get("features", ()))
            | set(manifest.get("passthrough", ()))
            | {manifest.get("split_column", SPLIT_COLUMN)}
        )

    added = sorted(column_set(candidate) - column_set(committed))
    removed = sorted(column_set(committed) - column_set(candidate))
    if added:
        problems.append(f"columns added: {added}")
    if removed:
        problems.append(f"columns removed: {removed}")

    baseline_nulls = committed.get("null_rates")
    if baseline_nulls is None:
        # Genuinely not comparable: the committed manifest predates this field.
        # Said out loud rather than passed over — the whole point of this guard is
        # that a check which did not run must never read like a check that passed.
        problems.append(
            "NULL RATES NOT COMPARED — the committed manifest carries no `null_rates` "
            "block. Re-commit the manifest to close this gap."
        )
    else:
        for name, rate in candidate.get("null_rates", {}).items():
            before = baseline_nulls.get(name)
            if before is None:
                continue  # a new column; already reported as an addition
            if abs(float(rate) - float(before)) > NULL_RATE_TOLERANCE:
                problems.append(f"null rate for {name}: {before:.4f} -> {float(rate):.4f}")
    return problems


def _refuse_or_report(candidate: dict[str, Any], path: pathlib.Path, allow_change: bool) -> None:
    """The guard. Raises before anything is written, or explains why it did not."""
    committed = committed_manifest(path.parent / f"{path.stem}.json")
    if committed is None:
        # Nothing committed at this path, so there is no good artifact to
        # destroy. Writes to a tmp_path land here, which is why this is quiet.
        return

    problems = manifest_deviations(candidate, committed)
    if not problems:
        return

    detail = "\n".join(f"  - {line}" for line in problems)
    if allow_change:
        print(
            f"OVERRIDE: rewriting {path} despite {len(problems)} deviation(s) from the "
            f"committed manifest:\n{detail}",
            file=sys.stderr,
        )
        return

    raise ArtifactRewriteRefused(
        f"REFUSING to overwrite the committed training matrix at {path}.\n"
        f"What is about to be written disagrees with the manifest at HEAD:\n{detail}\n\n"
        "This guard exists because a run against a transiently degraded warehouse once "
        "persisted a matrix with diagnosis_count entirely null over this file, and the "
        "write landed before the run failed. Check the warehouse first: `make "
        "warehouse-check`, and the view reconciliation.\n"
        "If the change is intended, say so explicitly — `make features ALLOW_CHANGE=1`, "
        f"`python -m src.features --allow-change`, or {ALLOW_CHANGE_ENV}=1 — and commit "
        "the new matrix and manifest together."
    )


def persist_training_matrix(
    frame: pd.DataFrame,
    config: dict | None = None,
    path: pathlib.Path = MATRIX_PATH,
    allow_change: bool | None = None,
) -> pathlib.Path:
    """Write the Model A matrix and its manifest. Returns the parquet path.

    Refuses when the content deviates from the COMMITTED manifest; see the module
    docstring. `allow_change=None` reads the environment, so the override is
    available to `make` without threading a flag through every caller.
    """
    cfg = config or load_model_config()
    if allow_change is None:
        allow_change = os.environ.get(ALLOW_CHANGE_ENV, "") not in ("", "0", "false", "False")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = label_and_split(frame, cfg)

    # Everything above is in memory. The guard runs HERE, before the first byte
    # of either file is touched, because the original failure was a bad write
    # that completed successfully and only then raised.
    candidate = _build_manifest(stamped, cfg, parquet_digest="")
    _refuse_or_report(candidate, path, allow_change)

    stamped.to_parquet(path, index=False)
    candidate["parquet_sha256_16"] = _file_digest(path)
    (path.parent / f"{path.stem}.json").write_text(json.dumps(candidate, indent=2) + "\n")
    return path


def read_persisted_matrix(path: pathlib.Path = MATRIX_PATH) -> pd.DataFrame | None:
    """The committed matrix, or None if it has not been written yet."""
    return pd.read_parquet(path) if path.exists() else None


def read_manifest(path: pathlib.Path = MANIFEST_PATH) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def build_training_matrix(
    engine: Engine | None = None,
    config: dict | None = None,
    refresh: bool = False,
    allow_change: bool | None = None,
) -> pd.DataFrame:
    """The Model A training matrix — the discovery route with no required arguments.

    Called with nothing (which is how the leakage guard calls it) this returns
    the committed matrix if it is there, and otherwise builds one from Postgres
    and writes it. It deliberately does NOT fall back to an empty frame when
    neither is available: the guard reports a builder that raises, and a guard
    handed an empty matrix would pass every probe while checking nothing.
    """
    if not refresh:
        existing = read_persisted_matrix()
        if existing is not None:
            return existing

    cfg = config or load_model_config()
    if engine is None:
        from sqlalchemy import create_engine

        from src.ingestion.load_postgres import database_url

        url = database_url()
        if not url:
            raise RuntimeError(
                f"no persisted matrix at {MATRIX_PATH} and no Postgres configured. "
                "Run `make features` against a loaded warehouse, or set POSTGRES_* in .env."
            )
        engine = create_engine(url)

    frame = build_model_a_frame(engine, cfg)
    persist_training_matrix(frame, cfg, allow_change=allow_change)
    return label_and_split(frame, cfg)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    """`make features` — rebuild the committed matrix from the warehouse."""
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild the persisted Model A feature matrix.")
    parser.add_argument(
        "--allow-change",
        action="store_true",
        help="write even when the content deviates from the committed manifest, printing "
        "every deviation. For an intended change — a new feature, a reloaded warehouse.",
    )
    args = parser.parse_args(argv)
    frame = build_training_matrix(refresh=True, allow_change=args.allow_change or None)
    print(f"wrote {len(frame):,} rows x {len(frame.columns)} columns -> {MATRIX_PATH}")
    print(f"manifest -> {MANIFEST_PATH}")
    # Printed, not persisted: the manifest is content-addressed on purpose.
    print(f"built at {datetime.now(UTC).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
