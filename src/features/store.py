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
sidecar manifest records the row count, the column list, the split boundary and
a digest of the leakage configuration that produced it. `tests/leakage/
test_persisted_matrix_is_current.py` rebuilds from Postgres and compares
(integration marker); the unit-level check verifies the manifest describes the
file sitting next to it.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
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


def persist_training_matrix(
    frame: pd.DataFrame,
    config: dict | None = None,
    path: pathlib.Path = MATRIX_PATH,
) -> pathlib.Path:
    """Write the Model A matrix and its manifest. Returns the parquet path."""
    cfg = config or load_model_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = label_and_split(frame, cfg)
    stamped.to_parquet(path, index=False)

    split = split_from_config(frame, cfg)
    manifest: dict[str, Any] = {
        "written_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
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
        "leakage_config_digest": leakage_config_digest(cfg),
        "parquet_sha256_16": _file_digest(path),
    }
    (path.parent / f"{path.stem}.json").write_text(json.dumps(manifest, indent=2) + "\n")
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
    persist_training_matrix(frame, cfg)
    return label_and_split(frame, cfg)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    """`make features` — rebuild the committed matrix from the warehouse."""
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild the persisted Model A feature matrix.")
    parser.parse_args(argv)
    frame = build_training_matrix(refresh=True)
    print(f"wrote {len(frame):,} rows x {len(frame.columns)} columns -> {MATRIX_PATH}")
    print(f"manifest -> {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
