"""Single source of acceptance-check SQL for the SIMULATED adjudication layer.

Same contract as `src/ingestion/warehouse_sql_checks.py`, deliberately: every
check is a violation-count query returning one integer where 0 == pass, written
to run IDENTICALLY against live PostgreSQL and the DuckDB CI mirror. Anti-joins
and explicit predicates rather than engine constraint semantics, so the two
engines cannot drift. `{s}` is the schema prefix (`rcm.` for both).

These are the checks that need the warehouse — they cover the joins to the
SOURCE fact table and the constraints as actually persisted. The statistical
side of validation (directional lifts, marginals, class balance) lives in
`validate.py`, where it runs on the frames.
"""

from __future__ import annotations

from src.ingestion.warehouse_sql_checks import CheckResult, Scalar

SIM_TABLES: tuple[str, ...] = (
    "sim_payer",
    "sim_service_line",
    "sim_authorization_eligibility",
    "sim_documentation_coding",
    "sim_claim_adjudication",
    "sim_appeals",
    "sim_workflow_events",
    "sim_operating_costs",
)

# Half a cent — rounding to 2dp cannot move a total by more than this.
_CENT = "0.005"

SIM_VIOLATION_CHECKS: list[tuple[str, str]] = [
    # ---- Foreign keys resolve (anti-joins) ----
    (
        "sim_fk:sim_claim_adjudication.claim_sk->fact_inpatient_claim",
        "select count(*) from {s}sim_claim_adjudication a "
        "left join {s}fact_inpatient_claim f on a.claim_sk = f.claim_sk "
        "where f.claim_sk is null",
    ),
    (
        "sim_fk:sim_claim_adjudication.sim_payer_id->sim_payer",
        "select count(*) from {s}sim_claim_adjudication a "
        "left join {s}sim_payer p on a.sim_payer_id = p.sim_payer_id "
        "where p.sim_payer_id is null",
    ),
    (
        "sim_fk:sim_claim_adjudication.sim_service_line_id->sim_service_line",
        "select count(*) from {s}sim_claim_adjudication a "
        "left join {s}sim_service_line s on a.sim_service_line_id = s.sim_service_line_id "
        "where s.sim_service_line_id is null",
    ),
    (
        "sim_fk:sim_authorization_eligibility.claim_sk->fact_inpatient_claim",
        "select count(*) from {s}sim_authorization_eligibility x "
        "left join {s}fact_inpatient_claim f on x.claim_sk = f.claim_sk "
        "where f.claim_sk is null",
    ),
    (
        "sim_fk:sim_documentation_coding.claim_sk->fact_inpatient_claim",
        "select count(*) from {s}sim_documentation_coding x "
        "left join {s}fact_inpatient_claim f on x.claim_sk = f.claim_sk "
        "where f.claim_sk is null",
    ),
    (
        "sim_fk:sim_appeals.claim_sk->sim_claim_adjudication",
        "select count(*) from {s}sim_appeals x "
        "left join {s}sim_claim_adjudication a on x.claim_sk = a.claim_sk "
        "where a.claim_sk is null",
    ),
    (
        "sim_fk:sim_workflow_events.claim_sk->fact_inpatient_claim",
        "select count(*) from {s}sim_workflow_events x "
        "left join {s}fact_inpatient_claim f on x.claim_sk = f.claim_sk "
        "where f.claim_sk is null",
    ),
    (
        "sim_fk:sim_operating_costs.claim_sk->sim_claim_adjudication",
        "select count(*) from {s}sim_operating_costs x "
        "left join {s}sim_claim_adjudication a on x.claim_sk = a.claim_sk "
        "where a.claim_sk is null",
    ),
    # ---- Grain / uniqueness ----
    (
        "sim_unique:sim_claim_adjudication.claim_sk",
        "select count(*) - count(distinct claim_sk) from {s}sim_claim_adjudication",
    ),
    (
        "sim_unique:sim_appeals.grain",
        "select coalesce(sum(c - 1), 0) from (select count(*) c from {s}sim_appeals "
        "group by claim_sk, sim_appeal_level) t",
    ),
    (
        "sim_unique:sim_workflow_events.grain",
        "select coalesce(sum(c - 1), 0) from (select count(*) c from {s}sim_workflow_events "
        "group by claim_sk, sim_event_seq) t",
    ),
    # ---- Coverage: one row per claim where the grain says so ----
    (
        "sim_coverage:every_claim_adjudicated",
        "select count(*) from {s}fact_inpatient_claim f "
        "left join {s}sim_claim_adjudication a on f.claim_sk = a.claim_sk "
        "where a.claim_sk is null",
    ),
    (
        "sim_coverage:every_claim_has_auth_row",
        "select count(*) from {s}sim_claim_adjudication a "
        "left join {s}sim_authorization_eligibility x on a.claim_sk = x.claim_sk "
        "where x.claim_sk is null",
    ),
    (
        "sim_coverage:every_claim_has_cost_row",
        "select count(*) from {s}sim_claim_adjudication a "
        "left join {s}sim_operating_costs c on a.claim_sk = c.claim_sk "
        "where c.claim_sk is null",
    ),
    # ---- Money invariants. paid <= allowed <= billed, nothing negative. ----
    (
        "sim_money:paid_le_allowed",
        "select count(*) from {s}sim_claim_adjudication "
        f"where sim_paid_amount > sim_allowed_amount + {_CENT}",
    ),
    (
        # The one invariant that CANNOT be a table constraint: billed charges are
        # a SOURCE value in another table, so it takes a join to check.
        "sim_money:allowed_le_billed_source_charge",
        "select count(*) from {s}sim_claim_adjudication a "
        "join {s}fact_inpatient_claim f on a.claim_sk = f.claim_sk "
        f"where a.sim_allowed_amount > coalesce(f.clm_tot_chrg_amt, 0) + {_CENT}",
    ),
    (
        "sim_money:no_negative_amounts",
        "select count(*) from {s}sim_claim_adjudication where sim_allowed_amount < 0 "
        "or sim_paid_amount < 0 or sim_patient_responsibility_amount < 0 "
        "or sim_contractual_adjustment_amount < 0 or sim_denied_amount < 0",
    ),
    (
        "sim_money:allowed_accounting_identity",
        "select count(*) from {s}sim_claim_adjudication where abs(sim_allowed_amount "
        "- sim_paid_amount - sim_patient_responsibility_amount - sim_denied_amount) "
        f"> 2 * {_CENT}",
    ),
    (
        "sim_money:recovered_le_disputed",
        "select count(*) from {s}sim_appeals "
        f"where sim_appeal_recovered_amount > sim_appeal_disputed_amount + {_CENT}",
    ),
    (
        "sim_money:no_recovery_when_upheld",
        "select count(*) from {s}sim_appeals "
        "where sim_appeal_outcome = 'UPHELD' and sim_appeal_recovered_amount > 0",
    ),
    # ---- Temporal ordering ----
    (
        "sim_temporal:coded<=submission<=ack<=adjudication",
        "select count(*) from {s}sim_claim_adjudication where sim_coded_date > sim_submission_date "
        "or sim_submission_date > sim_ack_date or sim_ack_date > sim_adjudication_date",
    ),
    (
        "sim_temporal:payment_after_adjudication",
        "select count(*) from {s}sim_claim_adjudication "
        "where sim_payment_date is not null and sim_payment_date < sim_adjudication_date",
    ),
    (
        "sim_temporal:appeal_filed<=decision",
        "select count(*) from {s}sim_appeals where sim_appeal_filed_date > sim_appeal_decision_date",
    ),
    (
        "sim_temporal:appeal_after_denial_review",
        "select count(*) from {s}sim_appeals x "
        "join {s}sim_claim_adjudication a on x.claim_sk = a.claim_sk "
        "where a.sim_denial_review_date is null or x.sim_appeal_filed_date < a.sim_denial_review_date",
    ),
    (
        # The event log is what process mining reads, so its ordering has to be
        # exact: timestamps must strictly increase along the sequence.
        "sim_temporal:event_ts_strictly_increasing",
        "select count(*) from (select sim_event_ts, lag(sim_event_ts) over "
        "(partition by claim_sk order by sim_event_seq) as prev_ts "
        "from {s}sim_workflow_events) t where prev_ts is not null and sim_event_ts <= prev_ts",
    ),
    (
        "sim_temporal:event_seq_starts_at_1",
        "select count(*) from (select claim_sk, min(sim_event_seq) as lo "
        "from {s}sim_workflow_events group by claim_sk) t where lo <> 1",
    ),
    (
        # A claim can never be submitted before the service happened. Compared as
        # yyyymmdd integers because date_key already is one, and because string
        # -> date casting is exactly the kind of thing PG and DuckDB disagree on.
        "sim_temporal:submission_after_service_from_date",
        "select count(*) from {s}sim_claim_adjudication a "
        "join {s}fact_inpatient_claim f on a.claim_sk = f.claim_sk "
        "where f.from_date_key <> 0 and (extract(year from a.sim_submission_date) * 10000 "
        "+ extract(month from a.sim_submission_date) * 100 "
        "+ extract(day from a.sim_submission_date)) < f.from_date_key",
    ),
    # ---- Outcome consistency ----
    (
        "sim_outcome:category_iff_denied",
        "select count(*) from {s}sim_claim_adjudication "
        "where (sim_denial_flag and sim_denial_category is null) "
        "or (not sim_denial_flag and sim_denial_category is not null)",
    ),
    (
        "sim_outcome:denial_type_matches_flag",
        "select count(*) from {s}sim_claim_adjudication "
        "where (sim_denial_flag and sim_denial_type = 'NONE') "
        "or (not sim_denial_flag and sim_denial_type <> 'NONE')",
    ),
    (
        "sim_outcome:appeals_only_on_denied_claims",
        "select count(*) from {s}sim_appeals x "
        "join {s}sim_claim_adjudication a on x.claim_sk = a.claim_sk "
        "where not a.sim_denial_flag",
    ),
    (
        "sim_outcome:denial_review_date_iff_denied",
        "select count(*) from {s}sim_claim_adjudication "
        "where (sim_denial_flag and sim_denial_review_date is null) "
        "or (not sim_denial_flag and sim_denial_review_date is not null)",
    ),
    (
        "sim_outcome:latent_p_in_unit_interval",
        "select count(*) from {s}sim_claim_adjudication "
        "where sim_latent_p <= 0 or sim_latent_p >= 1",
    ),
    # ---- Authorization logical consistency ----
    (
        "sim_auth:not_both_obtained_and_missing",
        "select count(*) from {s}sim_authorization_eligibility "
        "where sim_auth_obtained and sim_auth_missing",
    ),
    (
        "sim_auth:no_auth_state_when_not_required",
        "select count(*) from {s}sim_authorization_eligibility "
        "where not sim_auth_required and (sim_auth_obtained or sim_auth_missing)",
    ),
    # ---- Costs reconcile to the event log they were built from ----
    (
        "sim_cost:touch_minutes_reconcile_to_event_log",
        "select count(*) from {s}sim_operating_costs c join "
        "(select claim_sk, sum(sim_touch_minutes) as minutes from {s}sim_workflow_events "
        "group by claim_sk) e on c.claim_sk = e.claim_sk "
        "where abs(c.sim_touch_minutes_total - e.minutes) > 0.05",
    ),
    (
        "sim_cost:total_equals_component_sum",
        "select count(*) from {s}sim_operating_costs where abs(sim_total_cost_to_collect "
        "- sim_coding_cost - sim_submission_cost - sim_payment_posting_cost "
        f"- sim_denial_rework_cost - sim_appeal_cost) > 2 * {_CENT}",
    ),
]

# Every sim_ table must declare itself SIMULATED in its own rows (CLAUDE.md §3.1).
SIM_PROVENANCE_CHECKS: list[tuple[str, str]] = [
    (
        f"sim_provenance:{table}",
        f"select count(*) from {{s}}{table} where sim_provenance <> 'SIMULATED'",
    )
    for table in SIM_TABLES
]


def run_sim_violation_checks(scalar: Scalar, schema_prefix: str = "rcm.") -> list[CheckResult]:
    """Run every simulation violation check via `scalar(sql) -> int`; 0 == pass."""
    results = []
    for name, template in SIM_VIOLATION_CHECKS + SIM_PROVENANCE_CHECKS:
        violations = int(scalar(template.format(s=schema_prefix)))
        results.append(CheckResult(name, violations == 0, f"violations={violations}"))
    return results
