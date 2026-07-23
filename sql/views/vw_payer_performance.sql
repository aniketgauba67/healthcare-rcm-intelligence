-- ============================================================================
-- vw_payer_performance.sql — RCM performance by simulated payer archetype
--
-- Grain:        one row per simulated payer (sim_payer_id). 5 rows.
--
-- ***  THE PAYER DIMENSION IS 100 PERCENT SIMULATED (CLAUDE.md §3.5)  ***
--      Medicare FFS has exactly ONE payer. Every payer archetype below is
--      invented; NONE is modelled on or named after any real insurer, and NO
--      row here describes real Medicare / Medicare Advantage / commercial /
--      Medicaid adjudication behaviour. Every dashboard page and export built on
--      this view MUST carry the simulated-data banner. This is non-negotiable.
--
-- Sources:      rcm.vw_claim_enriched + rcm.sim_appeals.
-- Provenance:   SIMULATED throughout. sim_payer_name/mix_share/timely_filing_days
--               are SIMULATED dimension attributes; all volumes, rates, amounts,
--               timing and appeal measures are DERIVED from SIMULATED data.
--
-- Control query (must reconcile):
--   select sum(claims) from rcm.vw_payer_performance;         -- = 20867
--   select sum(denied_claims) from rcm.vw_payer_performance;  -- = 2663
--   round(sum(sim_payer_mix_share_config)::numeric,2) documents the design mix.
-- ============================================================================

create or replace view rcm.vw_payer_performance as
with appeals_by_claim as (
    select claim_sk,
           bool_or(sim_appeal_outcome = 'OVERTURNED') as any_overturned,
           sum(sim_appeal_recovered_amount)           as recovered_amt
    from rcm.sim_appeals
    group by claim_sk
)
select
    e.sim_payer_id,
    e.sim_payer_name,                                            -- SIMULATED archetype label
    max(e.sim_payer_mix_share)   as sim_payer_mix_share_config,  -- SIMULATED design share
    max(e.sim_timely_filing_days) as sim_timely_filing_days,     -- SIMULATED filing window

    -- ---- volume + realized mix ----
    count(*)                                                     as claims,
    round(count(*)::numeric
          / sum(count(*)) over (), 4)                            as realized_claim_share,

    -- ---- denial + quality (SIMULATED) ----
    count(*) filter (where e.sim_denial_flag)                    as denied_claims,
    round(avg(case when e.sim_denial_flag then 1 else 0 end), 4)      as denial_rate,
    round(avg(case when e.clean_claim_flag then 1 else 0 end), 4)     as clean_claim_rate,
    round(avg(case when e.sim_late_filing_flag then 1 else 0 end), 4) as late_filing_rate,

    -- ---- money (SIMULATED) ----
    round(sum(e.billed_charge_amt), 2)                          as source_billed_amt,
    round(sum(e.sim_allowed_amount), 2)                         as sim_allowed_amt,
    round(sum(e.sim_paid_amount), 2)                            as sim_paid_amt,
    round(sum(e.sim_paid_amount)
          / nullif(sum(e.sim_allowed_amount), 0), 4)            as sim_net_collection_rate,
    round(sum(e.sim_paid_amount)
          / nullif(sum(e.billed_charge_amt), 0), 4)             as sim_paid_to_billed_ratio,

    -- ---- timing (SIMULATED) ----
    round(avg(e.sim_days_to_payment)
          filter (where e.sim_days_to_payment is not null), 1)  as avg_days_to_payment,
    round((percentile_cont(0.5) within group (order by e.sim_days_to_payment)
          filter (where e.sim_days_to_payment is not null))::numeric, 1) as median_days_to_payment,
    round(avg(e.sim_days_to_adjudication), 1)                   as avg_days_to_adjudication,

    -- ---- appeals (SIMULATED) ----
    count(*) filter (where ap.claim_sk is not null)             as claims_appealed,
    round(avg(case when ap.any_overturned then 1 else 0 end)
          filter (where ap.claim_sk is not null), 4)            as appeal_overturn_rate,
    round(coalesce(sum(ap.recovered_amt), 0), 2)                as sim_appeal_recovered_amt,

    -- ---- cost to collect (SIMULATED) ----
    round(sum(e.sim_total_cost_to_collect), 2)                  as sim_cost_to_collect
from rcm.vw_claim_enriched e
left join appeals_by_claim ap on ap.claim_sk = e.claim_sk
group by e.sim_payer_id, e.sim_payer_name;

comment on view rcm.vw_payer_performance is
  'SIMULATED payer performance. The multi-payer dimension is 100 percent '
  'simulated (CLAUDE.md §3.5) — Medicare FFS has one payer; no row describes a '
  'real insurer. Every surface built on this view MUST show the synthetic banner.';
