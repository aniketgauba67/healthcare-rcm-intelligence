-- ============================================================================
-- vw_clean_claim_performance.sql — clean-claim / first-pass quality by provider
--
-- Grain:        one row per SYNTHETIC billing provider (prvdr_num). ~4,877 rows
--               (includes the Unknown provider member for null-provider claims).
--
-- ***  MANDATORY SYNTHETIC-PROVIDER KEYING  ***
--      This view groups on the SYNTHETIC prvdr_num, NEVER on sim_facility_ccn /
--      sim_facility_name. The simulated crosswalk multiplexes 4,876 synthetic
--      providers onto only 2,857 real CCNs (worst 8:1), so grouping by CCN would
--      silently merge up to 8 distinct synthetic hospitals and inflate volume.
--      sim_facility_ccn / sim_facility_name are carried DISPLAY-ONLY (max() of
--      the 1:1 crosswalk value) and must not be used as a grouping key
--      downstream. They are re-exported as sim_display_facility_* — `sim_` first
--      per §3.2 (these are SIMULATED-linkage values and §4.2 names the provider
--      clean-claim rate as a Phase 4 feature, so the column-name provenance
--      marker has to survive here too), `display_` retained to keep the
--      display-only signal.
--
-- Sources:      rcm.vw_claim_enriched.
-- Provenance:   provider identity (prvdr_num, provider_state_cd) = SOURCE;
--               sim_display_facility_ccn/name/state = SIMULATED linkage,
--               DISPLAY-ONLY;
--               sim_clean_claim_rate/sim_first_pass_paid_rate/sim_denial_rate/
--               sim_rework_rate and the simulated amounts = SIMULATED;
--               source_billed_amt = SOURCE (CMS billed charges, stays bare).
--               "Clean claim" = adjudicated
--               with no denial, no late filing, no eligibility failure, no
--               duplicate flag (definition lives in vw_claim_enriched).
--
-- HONESTY:      A low clean-claim rate is a process review flag for that
--               synthetic provider, never an accusation. Provider volumes vary
--               widely; sim_provider_claims is shown alongside every rate so thin
--               denominators are visible (do not rank single-claim providers).
--
-- Control query (must reconcile):
--   select sum(sim_provider_claims) from rcm.vw_clean_claim_performance;  -- = 20867
--   select sum(sim_clean_claims)    from rcm.vw_clean_claim_performance;  -- = 17148
--   select sum(sim_denied_claims)   from rcm.vw_clean_claim_performance;  -- = 2663
-- ============================================================================

-- `create or replace view` CANNOT rename an output column, so a tree that
-- already holds an older vw_clean_claim_performance would keep the old names and this
-- file would silently fail to take effect. Locally that never showed, because
-- vw_claim_enriched drop-cascades and takes its dependants with it; a FRESH
-- database has no such cascade, which is how the hosted init surfaced it.
drop view if exists rcm.vw_clean_claim_performance cascade;
create or replace view rcm.vw_clean_claim_performance as
select
    e.prvdr_num,                                        -- SOURCE synthetic key (grouping)
    max(e.provider_state_cd)             as provider_state_cd,   -- SOURCE
    max(e.sim_facility_ccn)          as sim_display_facility_ccn,    -- SIMULATED, display only
    max(e.sim_facility_name)         as sim_display_facility_name,   -- SIMULATED, display only
    max(e.sim_facility_state)        as sim_display_facility_state,  -- SIMULATED, display only

    count(*)                                            as sim_provider_claims,
    count(*) filter (where e.sim_clean_claim_flag)      as sim_clean_claims,
    count(*) filter (where e.sim_first_pass_paid_flag)  as sim_first_pass_paid_claims,
    count(*) filter (where e.sim_denial_flag)           as sim_denied_claims,

    -- rates (SIMULATED). Read alongside sim_provider_claims.
    round(avg(case when e.sim_clean_claim_flag then 1 else 0 end), 4) as sim_clean_claim_rate,
    round(avg(case when e.sim_first_pass_paid_flag then 1 else 0 end), 4) as sim_first_pass_paid_rate,
    round(avg(case when e.sim_denial_flag then 1 else 0 end), 4)       as sim_denial_rate,
    round(avg(case when e.sim_late_filing_flag then 1 else 0 end), 4)  as sim_late_filing_rate,
    -- rework rate = share of claims that incurred any denial rework cost
    round(avg(case when e.sim_denial_rework_cost > 0 then 1 else 0 end), 4) as sim_rework_rate,

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
  'on the synthetic id per the crosswalk ruling; sim_display_facility_ccn/name '
  'are display-only. Rates are SIMULATED (sim_ prefixed); low rates are review '
  'flags, not accusations. low_volume_flag marks thin denominators.';
