-- ============================================================================
-- vw_executive_rcm_summary.sql — headline RCM KPIs, one row per submission month
--
-- Grain:        one row per claim-submission calendar month
--               (sim_submission_year_month, 'YYYY-MM'). Trending grain so the
--               dashboard can chart KPIs and also roll them up to a book total.
--
-- Sources:      rcm.vw_claim_enriched (+ sim_appeals for appeal KPIs).
-- Provenance:   MIXED, labeled per measure:
--                 sim_claims_submitted, sim_submission_year_month = SIMULATED
--                                                                 (simulated submission timing)
--                 billed_charge_amt                             = SOURCE (real Medicare billed charge)
--                 medicare_source_paid_amt                      = SOURCE (the ONE real payer's actual pay)
--                 sim_allowed_amt, sim_paid_amt, sim_denied_amt = SIMULATED
--                 sim_denial_rate, sim_clean_claim_rate,
--                 sim_first_pass_paid_rate, sim_net_collection_rate,
--                 sim_avg_days_to_payment, sim_appeal_*,
--                 sim_cost_to_collect                           = SIMULATED
--
-- HONESTY:      Every amount except billed_charge_amt and medicare_source_paid_amt
--               is SIMULATED (docs/project_rules.md §3). The multi-payer economics this view
--               summarizes do NOT describe real payer behaviour. Dashboard pages
--               rendering this view must show the synthetic-data banner.
--
-- Control query (must reconcile):
--   select sum(sim_claims_submitted) from rcm.vw_executive_rcm_summary;  -- = 20867
--   select sum(sim_denied_claims) from rcm.vw_executive_rcm_summary;     -- = 2663
--   round(sum(sim_denied_claims)::numeric / sum(sim_claims_submitted), 4) -- = 0.1276
--   sum(billed_charge_amt) must equal sum(clm_tot_chrg_amt) in the fact.
-- ============================================================================

-- `create or replace view` CANNOT rename an output column, so a tree that
-- already holds an older vw_executive_rcm_summary would keep the old names and this
-- file would silently fail to take effect. Locally that never showed, because
-- vw_claim_enriched drop-cascades and takes its dependants with it; a FRESH
-- database has no such cascade, which is how the hosted init surfaced it.
drop view if exists rcm.vw_executive_rcm_summary cascade;
create or replace view rcm.vw_executive_rcm_summary as
with appeals_by_claim as (
    select claim_sk,
           count(*)                                             as appeal_levels,
           bool_or(sim_appeal_outcome = 'OVERTURNED')           as any_overturned,
           sum(sim_appeal_recovered_amount)                     as recovered_amt
    from rcm.sim_appeals
    group by claim_sk
)
select
    e.sim_submission_year_month,
    min(e.sim_submission_date)                                  as sim_month_start,

    -- ---- volume ----
    count(*)                                                    as sim_claims_submitted,
    count(*) filter (where e.sim_denial_flag)                   as sim_denied_claims,
    count(*) filter (where e.sim_clean_claim_flag)              as sim_clean_claims,
    count(*) filter (where e.sim_first_pass_paid_flag)          as sim_first_pass_paid_claims,

    -- ---- rates (SIMULATED outcomes) ----
    round(avg(case when e.sim_denial_flag then 1 else 0 end), 4)      as sim_denial_rate,
    round(avg(case when e.sim_clean_claim_flag then 1 else 0 end), 4) as sim_clean_claim_rate,
    round(avg(case when e.sim_first_pass_paid_flag then 1 else 0 end), 4) as sim_first_pass_paid_rate,
    round(avg(case when e.sim_late_filing_flag then 1 else 0 end), 4) as sim_late_filing_rate,

    -- ---- money: SOURCE (real) ----
    round(sum(e.billed_charge_amt), 2)                          as billed_charge_amt,
    round(sum(e.medicare_source_paid_amt), 2)                   as medicare_source_paid_amt,

    -- ---- money: SIMULATED ----
    round(sum(e.sim_allowed_amount), 2)                         as sim_allowed_amt,
    round(sum(e.sim_paid_amount), 2)                            as sim_paid_amt,
    round(sum(e.sim_denied_amount), 2)                          as sim_denied_amt,
    -- net collection = simulated paid / simulated allowed (0 guarded)
    round(sum(e.sim_paid_amount)
          / nullif(sum(e.sim_allowed_amount), 0), 4)            as sim_net_collection_rate,

    -- ---- timing (SIMULATED dates) ----
    round(avg(e.sim_days_to_payment)
          filter (where e.sim_days_to_payment is not null), 1)  as sim_avg_days_to_payment,
    round(avg(e.sim_days_to_adjudication), 1)                   as sim_avg_days_to_adjudication,

    -- ---- appeals (SIMULATED) ----
    count(*) filter (where ap.claim_sk is not null)             as sim_claims_appealed,
    count(*) filter (where ap.any_overturned)                   as sim_claims_overturned,
    round(avg(case when ap.any_overturned then 1 else 0 end)
          filter (where ap.claim_sk is not null), 4)            as sim_appeal_overturn_rate,
    round(coalesce(sum(ap.recovered_amt), 0), 2)                as sim_appeal_recovered_amt,

    -- ---- cost to collect (SIMULATED) ----
    round(sum(e.sim_total_cost_to_collect), 2)                  as sim_cost_to_collect,
    round(sum(e.sim_total_cost_to_collect)
          / nullif(sum(e.sim_paid_amount), 0), 4)               as sim_cost_to_collect_ratio
from rcm.vw_claim_enriched e
left join appeals_by_claim ap on ap.claim_sk = e.claim_sk
group by e.sim_submission_year_month;

comment on view rcm.vw_executive_rcm_summary is
  'Headline RCM KPIs per submission month. Only billed_charge_amt and '
  'medicare_source_paid_amt are SOURCE; every other amount/rate is SIMULATED '
  '(docs/project_rules.md §3). Render the synthetic-data banner.';
