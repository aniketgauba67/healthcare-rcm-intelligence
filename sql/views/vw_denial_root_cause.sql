-- ============================================================================
-- vw_denial_root_cause.sql — denial mix by category, CARC label, and driver
--
-- Grain:        one row per (sim_denial_category, sim_denial_carc_group,
--               sim_denial_driver_mechanism). Denied claims only. This is the
--               root-cause cross-tab: WHAT the denial was (category + CARC label)
--               against WHY the generator denied it (driver mechanism).
--
-- Sources:      rcm.vw_claim_enriched (denied subset) + rcm.sim_appeals.
-- Provenance:   SIMULATED. Every dimension and measure is generated:
--                 sim_denial_category / _carc_group / _driver_mechanism = SIMULATED
--                 sim_denial_carc_group is a CARC code used as a LABEL ONLY (§3.7);
--                   it is NOT sourced from an AMA/CMS description file.
--                 all counts, shares, amounts, appeal/overturn rates = DERIVED from SIMULATED.
--
-- HONESTY:      These are SIMULATED denials (the CMS synthetic claims contain no
--               denials). Roughly a third of simulated denials are pure label
--               noise with driver 'baseline' (docs/assumptions.md) — that is a
--               generator artifact, shown honestly, NOT a real root cause.
--               A high-frequency category is a review flag for process attention,
--               never an accusation of fraud.
--
-- Control query (must reconcile):
--   select sum(denial_count) from rcm.vw_denial_root_cause;   -- = 2663 (all denials)
--   select sum(denial_count) filter (where sim_denial_driver_mechanism='baseline')
--     from rcm.vw_denial_root_cause;                          -- = 1222 (label-noise-ish)
-- ============================================================================

create or replace view rcm.vw_denial_root_cause as
with denied as (
    select * from rcm.vw_claim_enriched where sim_denial_flag
),
appeal_agg as (
    select d.claim_sk,
           bool_or(true)                                   as appealed,
           bool_or(a.sim_appeal_outcome = 'OVERTURNED')    as overturned,
           sum(a.sim_appeal_recovered_amount)              as recovered_amt
    from denied d
    join rcm.sim_appeals a on a.claim_sk = d.claim_sk
    group by d.claim_sk
),
total as (select count(*) n from denied)
select
    d.sim_denial_category,
    d.sim_denial_carc_group,               -- CARC code as LABEL only (§3.7)
    d.sim_denial_driver_mechanism,

    count(*)                                                        as denial_count,
    round(count(*)::numeric / (select n from total), 4)            as share_of_denials,
    count(*) filter (where d.sim_denial_type = 'FULL')             as full_denials,
    count(*) filter (where d.sim_denial_type = 'PARTIAL')          as partial_denials,

    -- money at stake (SIMULATED)
    round(sum(d.sim_denied_amount), 2)                             as sim_denied_amt,
    round(avg(d.sim_denied_amount), 2)                             as sim_avg_denied_amt,

    -- rework burden (SIMULATED)
    round(sum(d.sim_denial_rework_cost), 2)                        as sim_rework_cost,

    -- appeal behaviour for this root-cause bucket (SIMULATED)
    count(*) filter (where ap.appealed)                            as claims_appealed,
    round(avg(case when ap.appealed then 1 else 0 end), 4)        as appeal_rate,
    count(*) filter (where ap.overturned)                          as claims_overturned,
    round(
        count(*) filter (where ap.overturned)::numeric
        / nullif(count(*) filter (where ap.appealed), 0), 4)      as overturn_rate_of_appealed,
    round(coalesce(sum(ap.recovered_amt), 0), 2)                   as sim_recovered_amt
from denied d
left join appeal_agg ap on ap.claim_sk = d.claim_sk
group by d.sim_denial_category, d.sim_denial_carc_group, d.sim_denial_driver_mechanism;

comment on view rcm.vw_denial_root_cause is
  'SIMULATED denial root-cause cross-tab: category + CARC label (label only, §3.7) '
  'vs generator driver mechanism. Counts are review signals, never fraud claims. '
  'Baseline-driver denials are largely label noise (docs/assumptions.md).';
