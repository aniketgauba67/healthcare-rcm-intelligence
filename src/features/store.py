"""Persisting the Model A training matrix, so the leakage guard can find it.

docs/project_rules.md §4.1 requires an automated test that fails the build if a forbidden
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
payer data exists anywhere in this project (docs/project_rules.md §3).

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

**"Nothing to protect" and "cannot tell" are different answers.** The first
version of this guard collapsed four conditions into a single `None` — no git,
the path is outside the repository, `git show` found nothing, and *the committed
manifest did not parse* — and then read `None` as "there is no good artifact
here, write away". None of those conditions proves absence once an artifact
already exists. A corrupt manifest at HEAD, a manifest that stopped being tracked
while the parquet is still committed, or Git becoming unavailable means the
artifact this guard exists to protect is sitting right there and only the evidence
about it is unreadable. Measured by qa on these paths: a matrix with
`diagnosis_count` entirely null went straight over the committed parquet with no
exception raised — the exact failure above, reached through the guard rather than
around it. A guard that disarms when its own reference is damaged is worse than no
guard, because the artifact still looks protected.

So the baseline lookup is now three-valued (`committed_baseline`): ABSENT,
READABLE, UNREADABLE. Only ABSENT is quiet. UNREADABLE refuses, and says which
condition prevented the comparison. When repository state cannot be established,
ABSENT is available only for a genuine first write with no artifact at the target
path. This is nothing more than the rule `manifest_deviations` already follows one
level down, where a missing `null_rates` block reports "NULL RATES NOT COMPARED"
instead of passing: **a check that did not run must never read like a check that
passed.**
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

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


class _GitMetadataUnavailable(RuntimeError):
    """Git cannot establish the committed baseline for an existing artifact."""


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
        "(docs/project_rules.md §4.1). Regenerate with `make features`.",
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
    except subprocess.CalledProcessError:
        return None
    except (FileNotFoundError, OSError, NotADirectoryError) as exc:
        raise _GitMetadataUnavailable(
            f"git could not discover the repository containing {path} ({exc})"
        ) from exc
    return pathlib.Path(top) if top else None


BaselineState = Literal["absent", "readable", "unreadable"]


@dataclass(frozen=True)
class CommittedBaseline:
    """What git can say about the artifact a write is about to land on.

    Three states, and the third is the whole reason this type exists instead of
    a bare `dict | None`:

    * `absent` — Git confirms neither file is committed, or repository state is
      unavailable and no artifact exists at the target. There is nothing to
      overwrite, so genuine first writes land here.
    * `readable` — the committed manifest was read from HEAD and `manifest`
      holds it. The guard compares against it.
    * `unreadable` — something IS committed here and the baseline could not be
      read anyway. The guard refuses, because this is the one case where being
      quiet means overwriting a good artifact while claiming it was protected.

    `reason` is written for the operator who has to act on it, and is carried on
    all three states so a surprising `absent` can be explained too.
    """

    state: BaselineState
    manifest: dict[str, Any] | None = None
    reason: str = ""


def _unknown_or_absent(artifact: pathlib.Path, reason: str) -> CommittedBaseline:
    """Refuse an unverifiable overwrite while preserving a genuinely new write."""
    if artifact.exists():
        return CommittedBaseline(
            "unreadable",
            reason=(
                f"{reason}; {artifact} already exists, so the guard cannot establish "
                "whether it is the committed artifact and will not overwrite it"
            ),
        )
    return CommittedBaseline(
        "absent",
        reason=f"{reason}; no artifact exists yet at {artifact}",
    )


def _verify_head(root: pathlib.Path) -> None:
    """Raise when Git cannot identify the commit that supplies the baseline."""
    try:
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        raise _GitMetadataUnavailable(
            f"git could not read HEAD metadata in {root} ({exc})"
        ) from exc


def _tracked_at_head(root: pathlib.Path, path: pathlib.Path) -> bool:
    """Whether `path` exists in the verified HEAD commit."""
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise _GitMetadataUnavailable(
            f"{path} is outside the expected git repository at {root}"
        ) from exc
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "--name-only", "HEAD", "--", relative],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        raise _GitMetadataUnavailable(
            f"git could not inspect HEAD:{relative} in {root} ({exc})"
        ) from exc
    return bool(result.stdout.strip())


def committed_baseline(
    path: pathlib.Path = MANIFEST_PATH,
    artifact_path: pathlib.Path | None = None,
    repo_root: pathlib.Path | None = None,
) -> CommittedBaseline:
    """Look up the manifest at HEAD, distinguishing "absent" from "unreadable".

    Read with `git show HEAD:<path>` and NOT from the working tree. The working
    copy is written by the same call that would write a bad matrix, so it is not
    independent evidence of anything.

    When the manifest is not at HEAD, the question of whether there is anything
    to protect is answered by the ARTIFACT, not by the manifest — the manifest is
    only the evidence. So an untracked manifest beside a tracked parquet is
    `unreadable`, not `absent`; that asymmetry is the fix for [GUARD-DISARM].
    """
    artifact = artifact_path if artifact_path is not None else path.with_suffix(".parquet")
    try:
        root = repo_root or _repo_root_for(path)
    except _GitMetadataUnavailable as exc:
        return _unknown_or_absent(artifact, str(exc))
    if root is None:
        return _unknown_or_absent(artifact, f"git did not identify a repository containing {path}")
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        artifact.resolve().relative_to(root.resolve())
    except ValueError:
        return _unknown_or_absent(
            artifact, f"{path} or {artifact} is outside the expected git repository at {root}"
        )

    try:
        _verify_head(root)
        manifest_tracked = _tracked_at_head(root, path)
        artifact_tracked = _tracked_at_head(root, artifact)
    except _GitMetadataUnavailable as exc:
        return _unknown_or_absent(artifact, str(exc))

    if not manifest_tracked:
        if artifact_tracked:
            return CommittedBaseline(
                "unreadable",
                reason=(
                    f"{artifact.name} IS committed at HEAD but its manifest "
                    f"({relative}) is not tracked there, so there is a good artifact "
                    "to protect and no baseline to check it against"
                ),
            )
        return CommittedBaseline(
            "absent",
            reason=f"neither {relative} nor {artifact.name} is committed at HEAD",
        )

    try:
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        return CommittedBaseline(
            "unreadable",
            reason=f"git could not read the committed manifest at HEAD:{relative} ({exc})",
        )
    try:
        manifest = json.loads(blob)
    except json.JSONDecodeError as exc:
        return CommittedBaseline(
            "unreadable",
            reason=(
                f"the manifest committed at HEAD:{relative} does not parse as JSON "
                f"({exc}), so the artifact it describes cannot be checked"
            ),
        )
    if not isinstance(manifest, dict):
        return CommittedBaseline(
            "unreadable",
            reason=(
                f"the manifest committed at HEAD:{relative} is "
                f"{type(manifest).__name__}, not a JSON object"
            ),
        )
    return CommittedBaseline("readable", manifest=manifest)


def committed_manifest(
    path: pathlib.Path = MANIFEST_PATH, repo_root: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """The manifest as committed at HEAD, or None if it could not be read.

    A convenience view over `committed_baseline` for callers that only want the
    content. **None here means "no readable manifest" and NOT "nothing to
    protect"** — reading it as the latter is precisely the [GUARD-DISARM] defect.
    Any caller deciding whether to guard must ask `committed_baseline` and branch
    on `state`.
    """
    return committed_baseline(path, repo_root=repo_root).manifest


def manifest_deviations(candidate: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    """How the matrix about to be written differs from the committed one.

    Row count, column set, per-column null rates — the three properties qa fixed
    the shape of. Returns human-readable lines because the operator reading them
    is the one who has to decide whether the change is real.
    """
    problems: list[str] = []

    try:
        candidate_rows = int(candidate["rows"])
        committed_rows = int(committed["rows"])
    except (KeyError, TypeError, ValueError):
        problems.append(
            "REQUIRED MANIFEST INFORMATION UNAVAILABLE — the committed and candidate "
            "manifests must both carry an integer `rows` value."
        )
    else:
        if candidate_rows != committed_rows:
            problems.append(f"row count {committed_rows} -> {candidate_rows}")

    def column_set(manifest: dict[str, Any]) -> set[str]:
        """The columns actually IN the file, which is not the same as the declared ones.

        `null_rates` is keyed on the frame's real columns. The `features` list is
        copied from MODEL_A_FEATURES and is therefore identical no matter what
        the frame contains — comparing that to itself would have made this check
        incapable of ever firing, which is worse than not having it.
        """
        rates = manifest.get("null_rates")
        if isinstance(rates, dict) and rates:
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
    candidate_nulls = candidate.get("null_rates")
    if not isinstance(baseline_nulls, dict) or not baseline_nulls:
        # Genuinely not comparable: the committed manifest predates this field.
        # Said out loud rather than passed over — the whole point of this guard is
        # that a check which did not run must never read like a check that passed.
        problems.append(
            "NULL RATES NOT COMPARED — the committed manifest carries no non-empty "
            "`null_rates` object. Re-commit the manifest to close this gap."
        )
    elif not isinstance(candidate_nulls, dict) or not candidate_nulls:
        problems.append(
            "NULL RATES NOT COMPARED — the candidate manifest carries no non-empty "
            "`null_rates` object."
        )
    else:
        for name, rate in candidate_nulls.items():
            before = baseline_nulls.get(name)
            if before is None:
                continue  # a new column; already reported as an addition
            try:
                before_rate = float(before)
                candidate_rate = float(rate)
            except (TypeError, ValueError):
                problems.append(
                    f"REQUIRED MANIFEST INFORMATION UNAVAILABLE — null rate for {name} "
                    "is not numeric."
                )
                continue
            if not (
                math.isfinite(before_rate)
                and math.isfinite(candidate_rate)
                and 0.0 <= before_rate <= 1.0
                and 0.0 <= candidate_rate <= 1.0
            ):
                problems.append(
                    f"REQUIRED MANIFEST INFORMATION UNAVAILABLE — null rate for {name} "
                    "must be finite and between 0 and 1."
                )
                continue
            if abs(candidate_rate - before_rate) > NULL_RATE_TOLERANCE:
                problems.append(f"null rate for {name}: {before_rate:.4f} -> {candidate_rate:.4f}")
    return problems


_OVERRIDE_HINT = (
    "If the change is intended, say so explicitly — `make features ALLOW_CHANGE=1`, "
    f"`python -m src.features --allow-change`, or {ALLOW_CHANGE_ENV}=1 — and commit "
    "the new matrix and manifest together."
)


def _refuse_or_report(candidate: dict[str, Any], path: pathlib.Path, allow_change: bool) -> None:
    """The guard. Raises before anything is written, or explains why it did not."""
    baseline = committed_baseline(path.parent / f"{path.stem}.json", artifact_path=path)

    if baseline.state == "absent":
        # Nothing committed at this path, so there is no good artifact to
        # destroy. Writes to a tmp_path land here, which is why this is quiet.
        return

    if baseline.state == "unreadable":
        # There IS something committed here and it cannot be checked. Silence
        # would overwrite a good artifact while looking like a passed check.
        if allow_change:
            print(
                f"OVERRIDE: rewriting {path} with NO baseline check — {baseline.reason}",
                file=sys.stderr,
            )
            return
        raise ArtifactRewriteRefused(
            f"REFUSING to overwrite the committed training matrix at {path}.\n"
            f"The baseline could not be read, so nothing was checked:\n"
            f"  - {baseline.reason}\n\n"
            "An unread check is not a passed one. This guard refuses here rather than "
            "standing down, because standing down when its own reference is damaged is "
            "how a guard silently stops guarding while the artifact still looks "
            "protected.\n"
            "Fix the baseline: restore the manifest at HEAD (`git checkout HEAD -- "
            f"{path.parent}`), or regenerate it on a tree where the baseline IS readable "
            "and commit it beside the parquet.\n" + _OVERRIDE_HINT
        )

    assert baseline.manifest is not None  # narrowed by state == "readable"
    problems = manifest_deviations(candidate, baseline.manifest)
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
        "warehouse-check`, and the view reconciliation.\n" + _OVERRIDE_HINT
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
