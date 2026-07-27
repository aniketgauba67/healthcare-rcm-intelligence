"""Pulling the raw ingredients out of the warehouse.

The queries here name every column explicitly. `select *` from
`vw_claim_enriched` would be shorter and would hand the label, the money, the
adjudication dates and the latent probability straight to the feature builder,
leaving nothing but a drop-list between them and the model. An explicit
allowlist means a forbidden column is never read in the first place, and the
leakage guard becomes a second line of defence rather than the only one.

Reads are strictly read-only: `select` only, no DDL, no writes.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

SCHEMA = "rcm"

# One row per claim. Everything a biller knows at the moment before submission,
# and nothing else. The label rides along in its own column and is separated
# from the features by `build_model_a_frame`.
CLAIM_QUERY = f"""
select
    a.claim_sk,
    a.sim_submission_date,
    a.sim_coded_date,
    a.sim_payer_id,
    a.sim_service_line_id,
    a.sim_filing_limit_days,
    a.sim_days_service_to_submission,
    a.sim_late_filing_flag,
    a.sim_denial_flag,
    ae.sim_auth_required,
    ae.sim_auth_obtained,
    ae.sim_auth_missing,
    ae.sim_auth_obtained_late,
    ae.sim_eligibility_checked,
    ae.sim_eligibility_failed,
    ae.sim_secondary_payer_present,
    ae.sim_auth_decision_date,
    dc.sim_documentation_complete,
    dc.sim_documentation_score,
    dc.sim_coder_query_outstanding,
    dc.sim_coding_specificity_deficit,
    dc.sim_coding_complexity_score,
    dc.sim_duplicate_submission_flag,
    f.clm_tot_chrg_amt as billed_charge_amt,
    f.length_of_stay_days,
    pv.prvdr_num,
    pv.provider_state_cd,
    drg.drg_cd,
    b.birth_date,
    adm.full_date as admission_date
from {SCHEMA}.sim_claim_adjudication a
join {SCHEMA}.sim_authorization_eligibility ae on ae.claim_sk = a.claim_sk
join {SCHEMA}.sim_documentation_coding dc on dc.claim_sk = a.claim_sk
join {SCHEMA}.fact_inpatient_claim f on f.claim_sk = a.claim_sk
join {SCHEMA}.dim_provider pv on pv.provider_key = f.provider_key
join {SCHEMA}.dim_drg drg on drg.drg_key = f.drg_key
join {SCHEMA}.dim_beneficiary b on b.bene_key = f.bene_key
left join {SCHEMA}.dim_date adm on adm.date_key = f.admission_date_key
"""

DIAGNOSIS_QUERY = f"""
select claim_sk, count(*) as diagnosis_count
from {SCHEMA}.fact_claim_diagnosis
group by claim_sk
"""

# docs/simulated_forbidden_columns.md §4: keep events at or before the claim's
# own CLAIM_SUBMITTED timestamp, then aggregate. The filter is on the timestamp
# rather than on the event type, so a new event type added later cannot walk in
# through a hardcoded list. Aggregating first and filtering second would encode
# rework effort, and therefore the label, into every aggregate.
WORKFLOW_QUERY = f"""
with boundary as (
    select claim_sk, min(sim_event_ts) as submitted_ts
    from {SCHEMA}.sim_workflow_events
    where sim_event_type = 'CLAIM_SUBMITTED'
    group by claim_sk
)
select
    e.claim_sk,
    sum(e.sim_touch_minutes) as pre_submission_touch_minutes,
    extract(epoch from (max(b.submitted_ts) - min(e.sim_event_ts))) / 3600.0
        as coding_to_submission_hours
from {SCHEMA}.sim_workflow_events e
join boundary b on b.claim_sk = e.claim_sk
where e.sim_event_ts <= b.submitted_ts
group by e.claim_sk
"""

# Model C works the denial queue, so it starts from denied claims and may see
# the denial itself (document §5). sim_appeals supplies the target only.
DENIAL_QUERY = f"""
select
    a.claim_sk,
    a.sim_denial_review_date,
    a.sim_denial_type,
    a.sim_denial_category,
    a.sim_denial_carc_group,
    a.sim_denied_amount,
    a.sim_allowed_amount,
    a.sim_paid_amount,
    a.sim_patient_responsibility_amount,
    a.sim_contractual_adjustment_amount,
    a.sim_days_to_adjudication
from {SCHEMA}.sim_claim_adjudication a
where a.sim_denial_flag
"""

APPEAL_TARGET_QUERY = f"""
select claim_sk, sim_appeal_outcome, sim_appeal_recovered_amount, sim_appeal_disputed_amount
from {SCHEMA}.sim_appeals
where sim_appeal_level = 1
"""


# EVALUATION ONLY. Denied dollars are an adjudication result and are forbidden
# as a Model A feature; they are read here to answer "how much of the money at
# risk does the top decile capture?", which is an outcome-side question. Kept in
# its own frame, joined only inside the evaluation functions, so it can never
# arrive in a feature matrix by being sat next to one.
OUTCOME_ECONOMICS_QUERY = f"""
select claim_sk, sim_denied_amount, sim_allowed_amount
from {SCHEMA}.sim_claim_adjudication
"""


def _read(engine: Engine, query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def fetch_claims(engine: Engine) -> pd.DataFrame:
    """Pre-submission claim facts, one row per claim, with the label attached."""
    claims = _read(engine, CLAIM_QUERY)
    diagnoses = _read(engine, DIAGNOSIS_QUERY)
    workflow = _read(engine, WORKFLOW_QUERY)
    merged = claims.merge(diagnoses, on="claim_sk", how="left").merge(
        workflow, on="claim_sk", how="left"
    )
    if len(merged) != len(claims):
        raise ValueError(
            f"claim grain broken by the joins: {len(claims)} claims became {len(merged)} rows"
        )
    return merged


def fetch_denials(engine: Engine) -> pd.DataFrame:
    """Denied claims with their remittance detail, for the Model C queue."""
    return _read(engine, DENIAL_QUERY)


def fetch_appeal_targets(engine: Engine) -> pd.DataFrame:
    """First-level appeal outcomes. Targets only — never features."""
    return _read(engine, APPEAL_TARGET_QUERY)


def fetch_outcome_economics(engine: Engine) -> pd.DataFrame:
    """Denied and allowed dollars. EVALUATION ONLY — never join this to features."""
    return _read(engine, OUTCOME_ECONOMICS_QUERY)
