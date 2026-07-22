"""Engine-agnostic warehouse integrity + reconciliation checks.

Runs the star-schema's foreign-key, uniqueness, non-negativity, date-ordering,
and source-reconciliation checks against the in-memory frames using DuckDB.
This lets Phase-1's "FK integrity passes / counts reconcile" definition-of-done
be verified WITHOUT a live Postgres — the same logical checks the Postgres load
runs post-COPY.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from .star_transform import StarFrames

# (fact table, fk column) -> (dim table, dim key column)
_FOREIGN_KEYS = [
    ("fact_inpatient_claim", "bene_key", "dim_beneficiary", "bene_key"),
    ("fact_inpatient_claim", "provider_key", "dim_provider", "provider_key"),
    ("fact_inpatient_claim", "drg_key", "dim_drg", "drg_key"),
    (
        "fact_inpatient_claim",
        "discharge_status_key",
        "dim_discharge_status",
        "discharge_status_key",
    ),
    ("fact_inpatient_claim", "from_date_key", "dim_date", "date_key"),
    ("fact_inpatient_claim", "thru_date_key", "dim_date", "date_key"),
    ("fact_inpatient_claim", "admission_date_key", "dim_date", "date_key"),
    ("fact_inpatient_claim", "discharge_date_key", "dim_date", "date_key"),
    ("fact_claim_revenue_line", "claim_sk", "fact_inpatient_claim", "claim_sk"),
    ("fact_claim_diagnosis", "claim_sk", "fact_inpatient_claim", "claim_sk"),
]

_MONEY_COLS = [
    "clm_pmt_amt",
    "clm_tot_chrg_amt",
    "nch_ip_ncvrd_chrg_amt",
    "nch_bene_ip_ddctbl_amt",
]

_UNIQUE_KEYS = [
    ("dim_beneficiary", "bene_key"),
    ("dim_provider", "provider_key"),
    ("dim_drg", "drg_key"),
    ("fact_inpatient_claim", "claim_sk"),
    ("fact_inpatient_claim", "clm_id"),
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _register(con: duckdb.DuckDBPyConnection, frames: StarFrames) -> None:
    for name, df in {**frames.dims, **frames.facts}.items():
        con.register(name, df)


def run_integrity_checks(frames: StarFrames) -> list[CheckResult]:
    """Return one CheckResult per integrity/reconciliation rule."""
    con = duckdb.connect()
    _register(con, frames)
    results: list[CheckResult] = []

    def scalar(sql: str) -> object:
        return con.execute(sql).fetchone()[0]

    # Foreign keys resolve (no orphan fact rows).
    for fact, fk, dim, dim_key in _FOREIGN_KEYS:
        orphans = scalar(
            f"select count(*) from {fact} f "
            f"left join {dim} d on f.{fk} = d.{dim_key} where d.{dim_key} is null"
        )
        results.append(CheckResult(f"fk:{fact}.{fk}->{dim}", orphans == 0, f"orphans={orphans}"))

    # Primary/natural key uniqueness.
    for table, col in _UNIQUE_KEYS:
        dups = scalar(
            f"select count(*) from (select {col} from {table} group by {col} having count(*) > 1)"
        )
        results.append(CheckResult(f"unique:{table}.{col}", dups == 0, f"dups={dups}"))

    # Non-negative money and counts.
    for col in _MONEY_COLS:
        neg = scalar(f"select count(*) from fact_inpatient_claim where {col} < 0")
        results.append(CheckResult(f"nonneg:{col}", neg == 0, f"negatives={neg}"))
    neg_days = scalar(
        "select count(*) from fact_inpatient_claim "
        "where clm_utlztn_day_cnt < 0 or length_of_stay_days < 0"
    )
    results.append(CheckResult("nonneg:day_counts", neg_days == 0, f"negatives={neg_days}"))

    # Service-date ordering (Unknown key 0 exempt).
    bad_order = scalar(
        "select count(*) from fact_inpatient_claim "
        "where from_date_key <> 0 and thru_date_key <> 0 and from_date_key > thru_date_key"
    )
    results.append(CheckResult("date_order:from<=thru", bad_order == 0, f"violations={bad_order}"))

    # Unknown members present.
    for dim, key in [
        ("dim_beneficiary", "bene_key"),
        ("dim_provider", "provider_key"),
        ("dim_drg", "drg_key"),
        ("dim_discharge_status", "discharge_status_key"),
        ("dim_date", "date_key"),
    ]:
        has_unknown = scalar(f"select count(*) from {dim} where {key} = 0") == 1
        results.append(CheckResult(f"unknown_member:{dim}", has_unknown))

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
