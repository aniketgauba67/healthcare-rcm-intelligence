-- ============================================================================
-- vw_work_queue_priority.sql — actionable-claim work queue (HEURISTIC PLACEHOLDER)
--
-- ***  HEURISTIC SCAFFOLD — NOT A MODEL  ***
--      Phase 4 has not run. This view ranks work with a TRANSPARENT, RULE-BASED
--      heuristic (dollars at stake weighted by age). heuristic_priority_score is
--      NOT a model score, NOT a probability, and NOT an Expected Net Recovery.
--      Model A (denial risk) and Model C (appeal success / Expected Net Recovery)
--      replace this ranking in Phase 4; this view exists only so the dashboard
--      work-queue page has a defensible ordering until then. Every score column
--      is named heuristic_* and is_heuristic_placeholder is always true.
--
-- Grain:        one row per ACTIONABLE claim (claim_sk). Actionable =
--               denied (sim_denial_flag) OR open AR (ar_open_flag / unpaid).
--
-- Sources:      rcm.vw_claim_enriched + rcm.sim_appeals (appeal-state context).
-- Provenance:   keys/provider = SOURCE (synthetic); payer/denial/amounts/dates =
--               SIMULATED; heuristic_priority_score, priority_tier, action_type,
--               age_days = DERIVED (rule-based, from SIMULATED inputs).
--
-- HONESTY:      Payer shown is SIMULATED (§3.5). A queued claim is a work item,
--               never a fraud flag. The heuristic deliberately uses only dollars
--               and age — no learned weights — so nothing here is presented as a
--               predictive model.
--
-- Control query (must reconcile):
--   select count(*) from rcm.vw_work_queue_priority;   -- = actionable claim count
--   equals: select count(*) from rcm.vw_claim_enriched
--           where sim_denial_flag or ar_open_flag;
-- ============================================================================

create or replace view rcm.vw_work_queue_priority as
with snapshot as (
    select greatest(max(sim_adjudication_date), max(sim_payment_date),
                    max(sim_denial_review_date)) as as_of_date
    from rcm.sim_claim_adjudication
),
appeal_state as (
    select claim_sk,
           count(*)                                        as appeal_levels,
           bool_or(sim_appeal_outcome = 'OVERTURNED')      as any_overturned
    from rcm.sim_appeals
    group by claim_sk
),
actionable as (
    select
        e.claim_sk,
        e.clm_id,
        e.prvdr_num,                                       -- SOURCE synthetic key
        e.facility_name,                                   -- SIMULATED, display only
        e.sim_payer_id,                                    -- SIMULATED
        e.sim_denial_flag,
        e.sim_denial_category,                             -- SIMULATED
        e.sim_denial_type,
        e.ar_open_flag,
        e.sim_denied_amount,
        e.ar_balance_amt,
        (s.as_of_date - e.sim_submission_date)             as age_days,
        ap.appeal_levels,
        ap.any_overturned,
        -- dollars at stake: denied amount if denied, else outstanding AR balance
        case when e.sim_denial_flag then e.sim_denied_amount
             else e.ar_balance_amt end                     as dollars_at_stake
    from rcm.vw_claim_enriched e
    cross join snapshot s
    left join appeal_state ap on ap.claim_sk = e.claim_sk
    where e.sim_denial_flag or e.ar_open_flag
),
scored as (
    select *,
        -- HEURISTIC: dollars at stake scaled by an age factor in [1, 2].
        -- Purely mechanical; NOT a learned or calibrated score.
        round(
            dollars_at_stake
            * (1 + least(greatest(age_days, 0), 365) / 365.0)
        , 2) as heuristic_priority_score,
        case
            when sim_denial_flag and coalesce(appeal_levels, 0) = 0 then 'DENIAL_REWORK'
            when sim_denial_flag and appeal_levels > 0 and not any_overturned then 'APPEAL_REVIEW'
            when sim_denial_flag then 'DENIAL_RESOLVED_MONITOR'
            else 'AR_FOLLOWUP'
        end as action_type
    from actionable
)
select
    claim_sk,
    clm_id,
    prvdr_num,
    facility_name,                                         -- display only
    sim_payer_id,                                          -- SIMULATED
    action_type,
    sim_denial_flag,
    sim_denial_category,
    sim_denial_type,
    ar_open_flag,
    age_days,
    round(dollars_at_stake, 2)         as dollars_at_stake,
    heuristic_priority_score,
    ntile(4) over (order by heuristic_priority_score desc) as priority_tier,  -- 1 = highest
    coalesce(appeal_levels, 0)         as appeal_levels,
    true                               as is_heuristic_placeholder             -- Phase 4 replaces
from scored;

comment on view rcm.vw_work_queue_priority is
  'HEURISTIC PLACEHOLDER work queue (NOT a model). Rule-based dollars-x-age '
  'ranking of actionable (denied or open-AR) claims; Phase 4 Model A/C replace '
  'it. Score columns are heuristic_*; payer is SIMULATED (§3.5); items are work, '
  'not fraud flags.';
