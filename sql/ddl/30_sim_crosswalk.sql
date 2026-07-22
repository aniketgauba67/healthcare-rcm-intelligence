-- ============================================================================
-- 30_sim_crosswalk.sql — SIMULATED linkage crosswalk (CLAUDE.md §3.4)
-- Grain:        sim_facility_crosswalk: one synthetic billing provider (PRVDR_NUM)
--               sim_provider_crosswalk: one synthetic attending physician (NPI)
-- Sources:      DERIVED from synthetic claims + REAL reference directories
--               (Hospital General Information CCNs; Medicare Physician NPIs)
-- Provenance:   SIMULATED — every row is a seeded random assignment, NOT a real
--               linkage. Synthetic claim ids do NOT actually correspond to these
--               real facilities/providers. Seed: config/simulation.yaml
--               linkage.crosswalk_seed (recorded per row for reproducibility).
-- Notes:        sim_ prefix per §3.2. Idempotent drop/recreate. PostgreSQL 16.
--               Written by data-engineer under team-lead delegation for Phase 1;
--               simulation-engineer reviews at Phase 2 (§5).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- sim_facility_crosswalk — synthetic billing provider -> real facility (CCN),
-- seeded, stratified by state (+ acute-care type). SIMULATED.
-- ---------------------------------------------------------------------------
drop table if exists rcm.sim_facility_crosswalk cascade;
create table rcm.sim_facility_crosswalk (
    sim_prvdr_num             text primary key
        references rcm.dim_provider (prvdr_num),
    sim_provider_ssa_state    text,
    sim_provider_postal_state text,
    facility_ccn              text not null,   -- REAL CMS CCN
    facility_name             text,
    facility_state            text,
    facility_type             text,
    match_rule                text not null,   -- state+acute | state_any_type | nationwide_fallback
    same_state                boolean not null,
    crosswalk_seed            integer not null,
    provenance                text not null default 'SIMULATED'
);
create index ix_sfx_ccn   on rcm.sim_facility_crosswalk (facility_ccn);
create index ix_sfx_state on rcm.sim_facility_crosswalk (facility_state);

-- ---------------------------------------------------------------------------
-- sim_provider_crosswalk — synthetic attending physician -> real Medicare
-- provider (NPI), seeded, stratified by coherent state + inpatient-plausible
-- specialty. SIMULATED. (No FK: synthetic physician NPIs are a degenerate id,
-- not a modeled dimension.)
-- ---------------------------------------------------------------------------
drop table if exists rcm.sim_provider_crosswalk cascade;
create table rcm.sim_provider_crosswalk (
    sim_at_physn_npi      text primary key,   -- synthetic attending physician id
    assigned_postal_state text,
    real_npi              text not null,       -- REAL Medicare NPI
    real_provider_state   text,
    real_specialty        text,
    match_rule            text not null,       -- state+plausible_specialty | state_any_specialty | nationwide_fallback
    same_state            boolean not null,
    crosswalk_seed        integer not null,
    provenance            text not null default 'SIMULATED'
);
create index ix_spx_npi     on rcm.sim_provider_crosswalk (real_npi);
create index ix_spx_state   on rcm.sim_provider_crosswalk (real_provider_state);
