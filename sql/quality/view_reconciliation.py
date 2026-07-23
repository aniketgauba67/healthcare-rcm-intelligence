"""Reconciliation checks for the analytics KPI views (analytics-engineer, sql/quality/).

Each view header names a control query it must reconcile to (CLAUDE.md §6, §7
"views reconcile to control queries"). This module makes those control queries an
automated gate, mirroring the src/ingestion/warehouse_sql_checks.py pattern:
every check is a VIOLATION-COUNT query returning a single integer where 0 == pass.
`{s}` is the schema prefix (`rcm.`).

The views must be applied first (`uv run python sql/views/apply_views.py`). These
checks run against live PostgreSQL. Unlike the warehouse checks they are NOT part
of the DuckDB CI mirror, because the views are created only in the live warehouse
(the views use PostgreSQL-specific constructs such as percentile_cont and ntile).

Run:
    uv run python sql/quality/view_reconciliation.py        # exits non-zero on any failure
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


# (name, sql) — each SQL returns ONE integer: count of violations (0 == pass).
# Equality controls are written as CASE ... 0/1 so the value is a violation count.
VIEW_RECONCILIATION_CHECKS: list[tuple[str, str]] = [
    # ---- base view: grain integrity vs the claim fact ----
    (
        "enriched:rowcount==fact",
        "select case when (select count(*) from {s}vw_claim_enriched) "
        "= (select count(*) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    (
        "enriched:distinct_claim_sk==rowcount",
        "select case when (select count(distinct claim_sk) from {s}vw_claim_enriched) "
        "= (select count(*) from {s}vw_claim_enriched) then 0 else 1 end",
    ),
    (
        "enriched:denied==adjudication_denied",
        "select case when (select count(*) from {s}vw_claim_enriched where sim_denial_flag) "
        "= (select count(*) from {s}sim_claim_adjudication where sim_denial_flag) then 0 else 1 end",
    ),
    # ---- executive summary reconciles to the book ----
    (
        "executive:sum_claims==fact",
        "select case when (select sum(claims_submitted) from {s}vw_executive_rcm_summary) "
        "= (select count(*) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    (
        "executive:sum_denied==adjudication_denied",
        "select case when (select sum(denied_claims) from {s}vw_executive_rcm_summary) "
        "= (select count(*) from {s}sim_claim_adjudication where sim_denial_flag) then 0 else 1 end",
    ),
    (
        "executive:sum_billed==fact_billed",
        "select case when (select round(sum(billed_charge_amt)) from {s}vw_executive_rcm_summary) "
        "= (select round(sum(clm_tot_chrg_amt)) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    # ---- denial root cause ----
    (
        "denial_root_cause:sum_denials==denied",
        "select case when (select sum(denial_count) from {s}vw_denial_root_cause) "
        "= (select count(*) from {s}sim_claim_adjudication where sim_denial_flag) then 0 else 1 end",
    ),
    (
        "denial_root_cause:full+partial==denials",
        "select case when (select sum(full_denials + partial_denials) from {s}vw_denial_root_cause) "
        "= (select sum(denial_count) from {s}vw_denial_root_cause) then 0 else 1 end",
    ),
    # ---- AR aging ----
    (
        "ar_aging:sum_open==unpaid",
        "select case when (select sum(open_claims) from {s}vw_ar_aging) "
        "= (select count(*) from {s}vw_claim_enriched where ar_open_flag) then 0 else 1 end",
    ),
    (
        "ar_aging:five_buckets",
        "select case when (select count(*) from {s}vw_ar_aging) = 5 then 0 else 1 end",
    ),
    (
        "ar_aging:denied+nondenied==open",
        "select coalesce(sum(case when open_claims "
        "<> denied_open_claims + nondenied_open_claims then 1 else 0 end), 0) "
        "from {s}vw_ar_aging",
    ),
    # ---- payer performance ----
    (
        "payer:sum_claims==fact",
        "select case when (select sum(claims) from {s}vw_payer_performance) "
        "= (select count(*) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    (
        "payer:rowcount==distinct_payers",
        "select case when (select count(*) from {s}vw_payer_performance) "
        "= (select count(*) from {s}sim_payer) then 0 else 1 end",
    ),
    # ---- clean claim performance (grain = synthetic provider; keying ruling) ----
    (
        "clean_claim:sum_provider_claims==fact",
        "select case when (select sum(provider_claims) from {s}vw_clean_claim_performance) "
        "= (select count(*) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    (
        "clean_claim:grain_is_synthetic_prvdr_num",
        "select case when (select count(distinct prvdr_num) from {s}vw_clean_claim_performance) "
        "= (select count(*) from {s}vw_clean_claim_performance) then 0 else 1 end",
    ),
    # ---- work queue (heuristic placeholder) ----
    (
        "work_queue:rowcount==actionable",
        "select case when (select count(*) from {s}vw_work_queue_priority) "
        "= (select count(*) from {s}vw_claim_enriched where sim_denial_flag or ar_open_flag) "
        "then 0 else 1 end",
    ),
    (
        "work_queue:all_labeled_heuristic",
        "select count(*) from {s}vw_work_queue_priority where is_heuristic_placeholder is not true",
    ),
    # ---- model monitoring (drift scaffold) ----
    (
        "model_monitoring:denial_rate_n==fact",
        "select case when (select sum(n_claims) from {s}vw_model_monitoring "
        "where feature_name = 'denial_rate') "
        "= (select count(*) from {s}fact_inpatient_claim) then 0 else 1 end",
    ),
    (
        "model_monitoring:all_labeled_scaffold",
        "select count(*) from {s}vw_model_monitoring where is_drift_scaffold is not true",
    ),
    (
        "model_monitoring:outcome_kind_is_only_denial_rate",
        "select count(*) from {s}vw_model_monitoring "
        "where metric_kind = 'outcome_rate' and feature_name <> 'denial_rate'",
    ),
    # ---- data-quality scorecard: no critical check may fail ----
    (
        "scorecard:no_failing_critical_check",
        "select count(*) from {s}vw_data_quality_scorecard "
        "where severity = 'critical' and pass_flag is not true",
    ),
]


def run_checks(conn, schema_prefix: str = "rcm.") -> list[CheckResult]:
    """Execute every reconciliation check against an open DBAPI/SQLAlchemy connection."""
    from sqlalchemy import text

    results: list[CheckResult] = []
    for name, sql in VIEW_RECONCILIATION_CHECKS:
        violations = conn.execute(text(sql.format(s=schema_prefix))).scalar()
        violations = int(violations or 0)
        results.append(
            CheckResult(name=name, passed=(violations == 0), detail=f"{violations} violation(s)")
        )
    return results


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        print("ERROR: no database_url() — set POSTGRES_* / DATABASE_URL in .env", file=sys.stderr)
        return 2

    engine = create_engine(url)
    with engine.connect() as conn:
        results = run_checks(conn)

    failed = [r for r in results if not r.passed]
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name}  ({r.detail})")
    print(f"\n{len(results) - len(failed)}/{len(results)} reconciliation checks passed")
    if failed:
        print(f"FAILED: {', '.join(r.name for r in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
