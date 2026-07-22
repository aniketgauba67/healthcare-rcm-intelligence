"""Single source of warehouse acceptance-check SQL (engine-portable).

Every check is a violation-count query returning one integer where 0 == pass.
The SQL is written to run IDENTICALLY against live PostgreSQL and the DuckDB CI
mirror (anti-joins rather than relying on engine constraint semantics), so the
two engines cannot drift. `{s}` is the schema prefix (`rcm.` for both).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


# (name, sql template) — each returns a single integer: count of violations.
VIOLATION_CHECKS: list[tuple[str, str]] = [
    # ---- Foreign keys resolve (anti-join: orphan fact rows) ----
    (
        "fk:fact_inpatient_claim.bene_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_beneficiary d on f.bene_key = d.bene_key where d.bene_key is null",
    ),
    (
        "fk:fact_inpatient_claim.provider_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_provider d on f.provider_key = d.provider_key where d.provider_key is null",
    ),
    (
        "fk:fact_inpatient_claim.drg_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_drg d on f.drg_key = d.drg_key where d.drg_key is null",
    ),
    (
        "fk:fact_inpatient_claim.discharge_status_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_discharge_status d on f.discharge_status_key = d.discharge_status_key "
        "where d.discharge_status_key is null",
    ),
    (
        "fk:fact_inpatient_claim.from_date_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_date d on f.from_date_key = d.date_key where d.date_key is null",
    ),
    (
        "fk:fact_inpatient_claim.thru_date_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_date d on f.thru_date_key = d.date_key where d.date_key is null",
    ),
    (
        "fk:fact_inpatient_claim.admission_date_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_date d on f.admission_date_key = d.date_key where d.date_key is null",
    ),
    (
        "fk:fact_inpatient_claim.discharge_date_key",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}dim_date d on f.discharge_date_key = d.date_key where d.date_key is null",
    ),
    (
        "fk:fact_claim_revenue_line.claim_sk",
        "select count(*) from {s}fact_claim_revenue_line f "
        "left join {s}fact_inpatient_claim c on f.claim_sk = c.claim_sk where c.claim_sk is null",
    ),
    (
        "fk:fact_claim_diagnosis.claim_sk",
        "select count(*) from {s}fact_claim_diagnosis f "
        "left join {s}fact_inpatient_claim c on f.claim_sk = c.claim_sk where c.claim_sk is null",
    ),
    # ---- Key uniqueness (dups = rows minus distinct) ----
    (
        "unique:dim_beneficiary.bene_key",
        "select count(*) - count(distinct bene_key) from {s}dim_beneficiary",
    ),
    (
        "unique:dim_beneficiary.bene_id",
        "select count(*) - count(distinct bene_id) from {s}dim_beneficiary",
    ),
    (
        "unique:dim_provider.prvdr_num",
        "select count(*) - count(distinct prvdr_num) from {s}dim_provider",
    ),
    (
        "unique:fact_inpatient_claim.claim_sk",
        "select count(*) - count(distinct claim_sk) from {s}fact_inpatient_claim",
    ),
    (
        "unique:fact_inpatient_claim.clm_id",
        "select count(*) - count(distinct clm_id) from {s}fact_inpatient_claim",
    ),
    (
        "unique:fact_claim_revenue_line.grain",
        "select coalesce(sum(c - 1), 0) from "
        "(select count(*) c from {s}fact_claim_revenue_line group by clm_id, clm_line_num) t",
    ),
    # ---- Service-date ordering (Unknown key 0 exempt) ----
    (
        "date_order:from<=thru",
        "select count(*) from {s}fact_inpatient_claim "
        "where from_date_key <> 0 and thru_date_key <> 0 and from_date_key > thru_date_key",
    ),
    # ---- Non-negative money and counts ----
    ("nonneg:clm_pmt_amt", "select count(*) from {s}fact_inpatient_claim where clm_pmt_amt < 0"),
    (
        "nonneg:clm_tot_chrg_amt",
        "select count(*) from {s}fact_inpatient_claim where clm_tot_chrg_amt < 0",
    ),
    (
        "nonneg:nch_ip_ncvrd_chrg_amt",
        "select count(*) from {s}fact_inpatient_claim where nch_ip_ncvrd_chrg_amt < 0",
    ),
    (
        "nonneg:nch_bene_ip_ddctbl_amt",
        "select count(*) from {s}fact_inpatient_claim where nch_bene_ip_ddctbl_amt < 0",
    ),
    (
        "nonneg:day_counts",
        "select count(*) from {s}fact_inpatient_claim "
        "where clm_utlztn_day_cnt < 0 or length_of_stay_days < 0",
    ),
    # ---- Unknown member present exactly once (abs(1-count)) ----
    (
        "unknown_member:dim_beneficiary",
        "select abs(1 - count(*)) from {s}dim_beneficiary where bene_key = 0",
    ),
    (
        "unknown_member:dim_provider",
        "select abs(1 - count(*)) from {s}dim_provider where provider_key = 0",
    ),
    ("unknown_member:dim_drg", "select abs(1 - count(*)) from {s}dim_drg where drg_key = 0"),
    (
        "unknown_member:dim_discharge_status",
        "select abs(1 - count(*)) from {s}dim_discharge_status where discharge_status_key = 0",
    ),
    ("unknown_member:dim_date", "select abs(1 - count(*)) from {s}dim_date where date_key = 0"),
]

Scalar = Callable[[str], int]


def run_violation_checks(scalar: Scalar, schema_prefix: str = "rcm.") -> list[CheckResult]:
    """Run every violation check via `scalar(sql) -> int`; 0 violations == pass."""
    results = []
    for name, template in VIOLATION_CHECKS:
        violations = int(scalar(template.format(s=schema_prefix)))
        results.append(CheckResult(name, violations == 0, f"violations={violations}"))
    return results


def run_count_reconciliation(
    scalar: Scalar, expected_counts: dict[str, int], schema_prefix: str = "rcm."
) -> list[CheckResult]:
    """Confirm each table's row count equals its expected (transformed) count."""
    results = []
    for table, expected in expected_counts.items():
        actual = int(scalar(f"select count(*) from {schema_prefix}{table}"))
        results.append(CheckResult(f"count:{table}", actual == expected, f"{actual} vs {expected}"))
    return results
