-- ============================================================================
-- vw_claim_enriched.sql — flattened one-row-per-claim analytics base view
--
-- Grain:        one row per inpatient claim (rcm.fact_inpatient_claim.claim_sk).
--               Exactly 20,867 rows. All downstream vw_* contract views read
--               from THIS view so the join logic and provenance live in one
--               place. Every 1:1 sim_ table (adjudication, auth/eligibility,
--               documentation, operating costs) attaches on claim_sk.
--
-- Sources & per-column provenance classification (CLAUDE.md §3.1):
--   SOURCE     (from CMS synthetic RIF, unmodified):
--     clm_id, prvdr_num, org_npi_num, provider_state_cd, drg_cd,
--     discharge_status_cd, admission_date, discharge_date, from_date, thru_date,
--     length_of_stay_days*, nch_clm_type_cd, admtg_dgns_cd, prncpal_dgns_cd,
--     clm_utlztn_day_cnt, billed_charge_amt (clm_tot_chrg_amt),
--     medicare_source_paid_amt (clm_pmt_amt), ncvrd_charge_amt, bene_deductible_amt
--     (*length_of_stay_days is DERIVED in the fact but originates from SOURCE dates)
--   DERIVED    (computed here from SOURCE/SIMULATED inputs):
--     claim_sk (warehouse surrogate), diagnosis_count, clean_claim_flag,
--     first_pass_paid_flag, ar_open_flag, ar_balance_amt, submission_year_month
--   SIMULATED  (generated adjudication layer, CLAUDE.md §3 — NOT real payer
--              behaviour; every sim_* column below is invented):
--     sim_payer_id, sim_payer_name, sim_service_line_id/name, all denial fields,
--     all sim money (allowed/paid/patient-resp/contractual/denied), all sim
--     timeline dates + day-count intervals, late-filing flag, all pre-submission
--     auth/eligibility + documentation/coding facts, all operating-cost fields.
--   SIMULATED-LINKAGE / DISPLAY-ONLY (CLAUDE.md §3.4, tasks.md crosswalk ruling):
--     facility_ccn, facility_name, facility_state, facility_type.
--     These come from sim_facility_crosswalk (a SEEDED RANDOM assignment, NOT a
--     real correspondence) and are DISPLAY-ONLY. The crosswalk multiplexes 4,876
--     synthetic providers onto 2,857 real CCNs (worst 8:1), so aggregation MUST
--     key on the synthetic prvdr_num, NEVER on facility_ccn/facility_name.
--
-- Payer note: sim_payer_* is 100% SIMULATED (Medicare FFS has one payer,
--             CLAUDE.md §3.5). Any view grouping by payer carries the banner.
--
-- Control query (must reconcile):
--   select count(*) from vw_claim_enriched;                     -- = 20867
--   select count(*) from vw_claim_enriched where adjudicated;   -- = 20867 (1:1)
--   select count(*) filter (where sim_denial_flag) ...          -- = 2663
--   Row count and every claim_sk must equal rcm.fact_inpatient_claim.
-- ============================================================================

create or replace view rcm.vw_claim_enriched as
select
    -- ---- keys (DERIVED surrogate / SOURCE degenerate) ----
    fic.claim_sk,
    fic.clm_id,

    -- ---- provider: SYNTHETIC id is the mandatory grouping key (SOURCE) ----
    prov.prvdr_num,                              -- SOURCE (synthetic, not a real CCN)
    prov.org_npi_num,                            -- SOURCE (synthetic, not a real NPI)
    prov.provider_state_cd,                      -- SOURCE

    -- ---- facility: DISPLAY-ONLY simulated linkage (never a grouping key) ----
    fx.facility_ccn,                             -- SIMULATED linkage, display only
    fx.facility_name,                            -- SIMULATED linkage, display only
    fx.facility_state,                           -- SIMULATED linkage, display only
    fx.facility_type,                            -- SIMULATED linkage, display only

    -- ---- clinical / source claim attributes (SOURCE) ----
    fic.bene_key,
    drg.drg_cd,
    drg.drg_desc,                                -- SOURCE code; REFERENCE text (null until MS-DRG ref loaded)
    ds.discharge_status_cd,
    fic.nch_clm_type_cd,
    fic.admtg_dgns_cd,
    fic.prncpal_dgns_cd,
    fic.clm_utlztn_day_cnt,
    fic.length_of_stay_days,                     -- DERIVED from SOURCE service dates
    dcnt.diagnosis_count,                        -- DERIVED (count of materialized dgns slots)
    d_adm.full_date  as admission_date,          -- SOURCE
    d_dis.full_date  as discharge_date,          -- SOURCE

    -- ---- source money (Medicare RIF; the ONE real payer) ----
    fic.clm_tot_chrg_amt      as billed_charge_amt,      -- SOURCE
    fic.clm_pmt_amt           as medicare_source_paid_amt, -- SOURCE (Medicare FFS actual)
    fic.nch_ip_ncvrd_chrg_amt as ncvrd_charge_amt,       -- SOURCE
    fic.nch_bene_ip_ddctbl_amt as bene_deductible_amt,   -- SOURCE

    -- ---- simulated payer + service line (SIMULATED, §3.5) ----
    adj.sim_payer_id,                            -- SIMULATED
    pay.sim_payer_name,                          -- SIMULATED
    pay.sim_payer_mix_share,                     -- SIMULATED
    pay.sim_timely_filing_days,                  -- SIMULATED
    adj.sim_service_line_id,                     -- SIMULATED
    sl.sim_service_line_name,                    -- SIMULATED

    -- ---- simulated timeline (SIMULATED) ----
    adj.sim_coded_date,
    adj.sim_submission_date,
    adj.sim_ack_date,
    adj.sim_adjudication_date,
    adj.sim_denial_review_date,
    adj.sim_payment_date,
    adj.sim_filing_limit_days,
    adj.sim_days_service_to_submission,
    adj.sim_days_to_adjudication,
    adj.sim_days_to_payment,
    adj.sim_late_filing_flag,
    to_char(adj.sim_submission_date, 'YYYY-MM') as submission_year_month, -- DERIVED

    -- ---- simulated money (SIMULATED) ----
    adj.sim_allowed_amount,
    adj.sim_paid_amount,
    adj.sim_patient_responsibility_amount,
    adj.sim_contractual_adjustment_amount,
    adj.sim_denied_amount,

    -- ---- simulated outcome (SIMULATED) ----
    adj.sim_denial_flag,
    adj.sim_denial_type,
    adj.sim_denial_category,
    adj.sim_denial_carc_group,                   -- CARC code used as a LABEL only (§3.7)
    adj.sim_denial_driver_mechanism,

    -- ---- simulated pre-submission facts (SIMULATED; legit Model A features) ----
    ae.sim_auth_required,
    ae.sim_auth_obtained,
    ae.sim_auth_missing,
    ae.sim_auth_obtained_late,
    ae.sim_eligibility_checked,
    ae.sim_eligibility_failed,
    ae.sim_secondary_payer_present,
    dc.sim_documentation_complete,
    dc.sim_documentation_score,
    dc.sim_coder_query_outstanding,
    dc.sim_coding_specificity_deficit,
    dc.sim_coding_complexity_score,
    dc.sim_duplicate_submission_flag,

    -- ---- simulated operating costs (SIMULATED) ----
    oc.sim_touch_minutes_total,
    oc.sim_denial_rework_cost,
    oc.sim_appeal_cost,
    oc.sim_total_cost_to_collect,

    -- ---- derived RCM flags (DERIVED) ----
    true as adjudicated,   -- 1:1 by construction; explicit for the control query
    -- clean claim = first-pass clean: adjudicated with no denial, no late filing,
    -- no eligibility failure, no duplicate flag. (Heuristic, quality funnel.)
    (not adj.sim_denial_flag
        and not adj.sim_late_filing_flag
        and not coalesce(ae.sim_eligibility_failed, false)
        and not coalesce(dc.sim_duplicate_submission_flag, false)) as clean_claim_flag,
    (adj.sim_payment_date is not null and not adj.sim_denial_flag) as first_pass_paid_flag,
    (adj.sim_payment_date is null) as ar_open_flag,   -- not yet paid = outstanding
    greatest(adj.sim_allowed_amount - adj.sim_paid_amount, 0) as ar_balance_amt
from rcm.fact_inpatient_claim fic
join rcm.dim_provider          prov  on prov.provider_key = fic.provider_key
join rcm.dim_drg               drg   on drg.drg_key = fic.drg_key
join rcm.dim_discharge_status  ds    on ds.discharge_status_key = fic.discharge_status_key
join rcm.dim_date              d_adm on d_adm.date_key = fic.admission_date_key
join rcm.dim_date              d_dis on d_dis.date_key = fic.discharge_date_key
-- sim adjudication is 1:1 with the fact (inner join; every claim is adjudicated)
join rcm.sim_claim_adjudication      adj on adj.claim_sk = fic.claim_sk
join rcm.sim_payer                   pay on pay.sim_payer_id = adj.sim_payer_id
join rcm.sim_service_line            sl  on sl.sim_service_line_id = adj.sim_service_line_id
left join rcm.sim_authorization_eligibility ae on ae.claim_sk = fic.claim_sk
left join rcm.sim_documentation_coding      dc on dc.claim_sk = fic.claim_sk
left join rcm.sim_operating_costs           oc on oc.claim_sk = fic.claim_sk
-- facility crosswalk: display-only left join keyed on the SYNTHETIC prvdr_num
left join rcm.sim_facility_crosswalk fx on fx.sim_prvdr_num = prov.prvdr_num
-- diagnosis count per claim (DERIVED from the diagnosis bridge)
left join (
    select claim_sk, count(*) as diagnosis_count
    from rcm.fact_claim_diagnosis
    group by claim_sk
) dcnt on dcnt.claim_sk = fic.claim_sk;

comment on view rcm.vw_claim_enriched is
  'Analytics base: one row per claim (claim_sk). SOURCE claim + SIMULATED '
  'adjudication/auth/documentation/costs, facility linkage DISPLAY-ONLY. '
  'Group facility/provider analytics on synthetic prvdr_num, never facility_ccn '
  '(crosswalk multiplexes 8:1). Payer dimension is 100 percent SIMULATED (§3.5).';
