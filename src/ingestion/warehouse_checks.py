"""DuckDB CI mirror of the warehouse acceptance checks.

Loads the star-schema frames into a DuckDB `rcm` schema and runs the SAME
engine-portable check SQL that validates the live Postgres warehouse
(`warehouse_sql_checks`). This is the fast, DB-free CI suite — the live
Postgres run remains the Phase-1 acceptance authority.
"""

from __future__ import annotations

import duckdb

import pandas as pd

from .star_transform import StarFrames
from .warehouse_sql_checks import (
    CheckResult,
    run_count_reconciliation,
    run_crosswalk_checks,
    run_violation_checks,
)

_ALL_TABLES = (
    "dim_date",
    "dim_beneficiary",
    "dim_provider",
    "dim_drg",
    "dim_discharge_status",
    "fact_inpatient_claim",
    "fact_claim_revenue_line",
    "fact_claim_diagnosis",
)


def expected_counts(frames: StarFrames) -> dict[str, int]:
    """Row count each table should have after load (dimensions include Unknown)."""
    merged = {**frames.dims, **frames.facts}
    return {name: int(len(merged[name])) for name in _ALL_TABLES}


def duckdb_star_connection(
    frames: StarFrames, crosswalk_frames: dict[str, pd.DataFrame] | None = None
) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with the star schema (+ optional crosswalk) under `rcm`."""
    con = duckdb.connect()
    con.execute("create schema rcm")
    tables = {**frames.dims, **frames.facts, **(crosswalk_frames or {})}
    for name, df in tables.items():
        con.register("_stage", df)
        con.execute(f"create table rcm.{name} as select * from _stage")
        con.unregister("_stage")
    return con


def run_integrity_checks(
    frames: StarFrames, crosswalk_frames: dict[str, pd.DataFrame] | None = None
) -> list[CheckResult]:
    """Run the shared violation-check SQL against the DuckDB mirror.

    When `crosswalk_frames` are supplied, the SIMULATED crosswalk FK/provenance/
    count checks run here too (CI parity with the live Postgres path).
    """
    con = duckdb_star_connection(frames, crosswalk_frames)

    def scalar(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    results = run_violation_checks(scalar, "rcm.")
    results += run_count_reconciliation(scalar, expected_counts(frames), "rcm.")
    if crosswalk_frames:
        results += run_crosswalk_checks(scalar, "rcm.")
        results += run_count_reconciliation(
            scalar, {t: len(df) for t, df in crosswalk_frames.items()}, "rcm."
        )
    con.close()
    return results


def reconcile_to_source(
    frames: StarFrames, raw_inpatient_lines: int, source_distinct_claims: int
) -> list[CheckResult]:
    """Reconcile warehouse row counts back to the raw source (hard invariants).

    Unknown-member resolution (null provider/DRG routed to key 0) is a data
    quality metric, not a failure — it lives in `frames.reconciliation` and is
    reported, not asserted, since the FK itself resolves to a valid member.
    """
    rec = frames.reconciliation
    return [
        CheckResult(
            "reconcile:revenue_lines==raw_lines",
            rec["revenue_lines"] == raw_inpatient_lines,
            f"{rec['revenue_lines']} vs {raw_inpatient_lines}",
        ),
        CheckResult(
            "reconcile:claims==source_distinct_clm_id",
            rec["claims"] == source_distinct_claims,
            f"{rec['claims']} vs {source_distinct_claims}",
        ),
    ]
