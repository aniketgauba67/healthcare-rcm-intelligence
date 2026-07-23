-- ============================================================================
-- vw_clean_claim_performance.sql — clean-claim / first-pass quality by provider
--
-- Grain:        one row per SYNTHETIC billing provider (prvdr_num). ~4,877 rows
--               (includes the Unknown provider member for null-provider claims).
--
-- ***  MANDATORY KEYING (tasks.md crosswalk ruling)  ***
--      This view groups on the SYNTHETIC prvdr_num, NEVER on facility_ccn /
--      facility_name. The simulated crosswalk multiplexes 4,876 synthetic
--      providers onto only 2,857 real CCNs (worst 8:1), so grouping by CCN would
--      silently merge up to 8 distinct synthetic hospitals and inflate volume.
--      facility_ccn / facility_name are carried DISPLAY-ONLY (max() of the 1:1
--      crosswalk value) and must not be used as a grouping key downstream.
--
-- Sources:      rcm.vw_claim_enriched.
-- Provenance:   provider identity (prvdr_num, provider_state_cd) = SOURCE;
--               facility_ccn/name/state = SIMULATED linkage, DISPLAY-ONLY;
--               clean_claim_rate/first_pass_rate/denial_rate/rework_rate and all
--               amounts = DERIVED from SIMULATED. "Clean claim" = adjudicated
--               with no denial, no late filing, no eligibility failure, no
--               duplicate flag (definition lives in vw_claim_enriched).
--
-- HONESTY:      A low clean-claim rate is a process review flag for that
--               synthetic provider, never an accusation. Provider volumes vary
--               widely; provider_claims is shown alongside every rate so thin
--               denominators are visible (do not rank single-claim providers).
--
-- Control query (must reconcile):
--   select sum(provider_claims) from rcm.vw_clean_claim_performance;  -- = 20867
--   select sum(clean_claims)    from rcm.vw_clean_claim_performance;  -- = 17148
--   select sum(denied_claims)   from rcm.vw_clean_claim_performance;  -- = 2663
-- ============================================================================

create or replace view rcm.vw_clean_claim_performance as
select
    e.prvdr_num,                                        -- SOURCE synthetic key (grouping)
    max(e.provider_state_cd)             as provider_state_cd,   -- SOURCE
    max(e.facility_ccn)                  as display_facility_ccn,    -- SIMULATED, display only
    max(e.facility_name)                 as display_facility_name,   -- SIMULATED, display only
    max(e.facility_state)                as display_facility_state,  -- SIMULATED, display only

    count(*)                                            as provider_claims,
    count(*) filter (where e.clean_claim_flag)          as clean_claims,
    count(*) filter (where e.first_pass_paid_flag)      as first_pass_paid_claims,
    count(*) filter (where e.sim_denial_flag)           as denied_claims,

    -- rates (DERIVED from SIMULATED). Read alongside provider_claims.
    round(avg(case when e.clean_claim_flag then 1 else 0 end), 4)      as clean_claim_rate,
    round(avg(case when e.first_pass_paid_flag then 1 else 0 end), 4)  as first_pass_paid_rate,
    round(avg(case when e.sim_denial_flag then 1 else 0 end), 4)       as denial_rate,
    round(avg(case when e.sim_late_filing_flag then 1 else 0 end), 4)  as late_filing_rate,
    -- rework rate = share of claims that incurred any denial rework cost
    round(avg(case when e.sim_denial_rework_cost > 0 then 1 else 0 end), 4) as rework_rate,

    -- money context (SOURCE billed vs SIMULATED paid)
    round(sum(e.billed_charge_amt), 2)                  as source_billed_amt,
    round(sum(e.sim_paid_amount), 2)                    as sim_paid_amt,
    round(sum(e.sim_denial_rework_cost), 2)             as sim_rework_cost,

    -- flag thin denominators so ranking logic can exclude them
    (count(*) < 10)                                     as low_volume_flag
from rcm.vw_claim_enriched e
group by e.prvdr_num;

comment on view rcm.vw_clean_claim_performance is
  'Clean-claim / first-pass quality per SYNTHETIC provider (prvdr_num). Grouped '
  'on the synthetic id per the crosswalk ruling; facility_ccn/name are '
  'display-only. Rates are DERIVED from SIMULATED outcomes; low rates are review '
  'flags, not accusations. low_volume_flag marks thin denominators.';
