"""Validated-layer CLI: stage raw RIF files into typed Parquet.

Usage:
    python -m src.validation.run                 # stage all downloaded RIF files
    python -m src.validation.run --only inpatient

Reconciles staged Parquet row counts against the ingestion manifest and fails
loudly on any mismatch.
"""

from __future__ import annotations

import argparse

from src.ingestion.logging_utils import get_logger, log_event
from src.ingestion.manifest import Manifest
from src.ingestion.paths import DATA_VALIDATED, REPO_ROOT

from .stage_parquet import stage_file

_LOGGER = get_logger("validation.run")

# RIF SOURCE artifacts we stage, keyed by manifest key -> output parquet stem.
_STAGE_TARGETS = {
    "cms_synthetic_claims:beneficiary_2024": "beneficiary_2024",
    "cms_synthetic_claims:inpatient": "inpatient",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage raw RIF files to Parquet.")
    parser.add_argument(
        "--only",
        action="append",
        help="Restrict to output stems (e.g. inpatient). Default: all staged.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = Manifest.load()
    wanted = set(args.only) if args.only else None

    failures = 0
    staged = 0
    for key, stem in _STAGE_TARGETS.items():
        if wanted is not None and stem not in wanted:
            continue
        entry = manifest.get(key)
        if entry is None:
            log_event(_LOGGER, "stage.skip_missing", key=key, hint="run ingestion first")
            continue
        raw_path = REPO_ROOT / entry.filename
        out_path = DATA_VALIDATED / f"{stem}.parquet"
        result = stage_file(stem, raw_path, out_path)
        staged += 1

        # Reconcile against the manifest's independently measured row count.
        if entry.row_count is not None and result.rows_out != entry.row_count:
            failures += 1
            log_event(
                _LOGGER,
                "stage.reconcile_mismatch",
                name=stem,
                parquet_rows=result.rows_out,
                manifest_rows=entry.row_count,
            )
        else:
            log_event(
                _LOGGER,
                "stage.reconcile_ok",
                name=stem,
                rows=result.rows_out,
                manifest_rows=entry.row_count,
            )

    log_event(_LOGGER, "stage.summary", staged=staged, failures=failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
