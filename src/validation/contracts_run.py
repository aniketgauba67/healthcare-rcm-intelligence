"""Run data contracts over the validated layer; emit quarantine + report.

`make contracts`:
  * checks every staged table against its contracts,
  * writes the union of failing rows to data/validated/quarantine/quarantine.parquet,
  * writes a reconciliation report (staged rows vs the ingestion manifest, plus
    contract results and quarantine counts) to
    data/validated/reconciliation_report.json,
  * exits non-zero if any table-level contract fails.
"""

from __future__ import annotations

import json

import pandas as pd

from src.ingestion.logging_utils import get_logger, log_event
from src.ingestion.manifest import Manifest
from src.ingestion.paths import DATA_VALIDATED

from .contracts import check_contracts

_LOGGER = get_logger("validation.contracts")

# staged parquet stem -> manifest key for source-row reconciliation.
_TABLES = {
    "beneficiary_2024": "cms_synthetic_claims:beneficiary_2024",
    "inpatient": "cms_synthetic_claims:inpatient",
}


def run() -> int:
    manifest = Manifest.load()
    quarantine_dir = DATA_VALIDATED / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {"tables": {}, "reconciliation": {}}
    quarantines: list[pd.DataFrame] = []
    failures = 0

    for stem, manifest_key in _TABLES.items():
        path = DATA_VALIDATED / f"{stem}.parquet"
        if not path.exists():
            log_event(_LOGGER, "contracts.skip_missing", table=stem)
            continue
        df = pd.read_parquet(path)
        result = check_contracts(stem, df)
        if not result.quarantine.empty:
            quarantines.append(result.quarantine)
        if not result.passed:
            failures += 1

        report["tables"][stem] = {
            "rows": result.rows,
            "table_checks": result.table_checks,
            "row_violations": result.row_violations,
            "quarantined": int(len(result.quarantine)),
        }
        # Source reconciliation: staged rows == the manifest's measured source rows.
        entry = manifest.get(manifest_key)
        if entry is not None and entry.row_count is not None:
            report["reconciliation"][stem] = {
                "staged_rows": result.rows,
                "source_rows": entry.row_count,
                "reconciles": result.rows == entry.row_count,
            }
            if result.rows != entry.row_count:
                failures += 1
        log_event(
            _LOGGER,
            "contracts.table",
            table=stem,
            passed=result.passed,
            quarantined=int(len(result.quarantine)),
            row_violations=result.row_violations,
        )

    quarantine = pd.concat(quarantines, ignore_index=True) if quarantines else None
    q_path = quarantine_dir / "quarantine.parquet"
    if quarantine is not None:
        quarantine.to_parquet(q_path, index=False)
    else:
        # Write an empty, well-typed quarantine so downstream loads never break.
        pd.DataFrame(columns=["table_name", "contract", "entity_key", "reason"]).to_parquet(
            q_path, index=False
        )
    report["quarantine_total"] = 0 if quarantine is None else int(len(quarantine))

    report_path = DATA_VALIDATED / "reconciliation_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    log_event(
        _LOGGER,
        "contracts.done",
        failures=failures,
        quarantine_total=report["quarantine_total"],
        report=str(report_path),
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
