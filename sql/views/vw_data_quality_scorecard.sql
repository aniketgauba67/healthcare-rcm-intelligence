-- ============================================================================
-- vw_data_quality_scorecard.sql — warehouse data-quality checks, one per row
--
-- Grain:        one row per named data-quality check (check_id). Each row is a
--               self-describing metric: numerator / denominator / rate, a
--               pass_flag against a stated threshold, a severity, and the
--               provenance class of the data being checked.
--
-- Sources:      rcm.vw_claim_enriched, rcm.fact_inpatient_claim,
--               rcm.fact_claim_diagnosis, rcm.dq_quarantine, and the sim_ tables
--               (coverage/orphan checks).
-- Provenance:   DERIVED (data-quality metadata). Column meanings:
--                 check_id/dimension/description/threshold/severity  = DERIVED labels
--                 subject_provenance = the class of the DATA under test
--                   (SOURCE | DERIVED | SIMULATED | REFERENCE)
--                 numerator/denominator/metric_value/pass_flag       = DERIVED
--               No SOURCE or SIMULATED business value is presented as real here;
--               this view measures the warehouse, it does not report KPIs.
--
-- Notes:        "review flag", never "fraud" (persona hard rule). A failing row
--               is a data-quality review flag. NULL provider/DRG routed to the
--               Unknown member (key 0) is EXPECTED (data-engineer design), so
--               those checks are informational (severity 'info'), not failures.
--
-- Control query (must reconcile):
--   Each numerator is independently reproducible, e.g.
--     select count(*) from rcm.fact_inpatient_claim;                 -- claims_total
--     select count(*) from rcm.sim_claim_adjudication;               -- adj coverage num
--     select count(*) from rcm.dq_quarantine;                        -- quarantine_rows
--   pass_flag = (metric_value within threshold). Row count = number of checks.
-- ============================================================================

create or replace view rcm.vw_data_quality_scorecard as
with claims as (select count(*) n from rcm.fact_inpatient_claim),
     checks as (

    -- 1. Adjudication coverage: every claim must have exactly one sim outcome.
    select 'adjudication_coverage' as check_id, 'completeness' as dimension,
           'SIMULATED' as subject_provenance,
           'Every claim has exactly one sim_claim_adjudication row (1:1)' as description,
           (select count(*) from rcm.sim_claim_adjudication)::numeric as numerator,
           (select n from claims)::numeric as denominator,
           1.0::numeric as threshold_min, 'critical' as severity

    -- 2. Adjudication orphans: no sim outcome without a parent claim.
    union all select 'adjudication_orphans', 'referential_integrity', 'SIMULATED',
           'sim_claim_adjudication rows with no matching fact claim (want 0)',
           (select count(*) from rcm.sim_claim_adjudication a
              left join rcm.fact_inpatient_claim f on f.claim_sk = a.claim_sk
              where f.claim_sk is null)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 3. Appeal orphans: every appeal ties to an adjudicated claim.
    union all select 'appeal_orphans', 'referential_integrity', 'SIMULATED',
           'sim_appeals rows with no matching adjudication (want 0)',
           (select count(*) from rcm.sim_appeals ap
              left join rcm.sim_claim_adjudication a on a.claim_sk = ap.claim_sk
              where a.claim_sk is null)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 4. Workflow-event orphans.
    union all select 'workflow_event_orphans', 'referential_integrity', 'SIMULATED',
           'sim_workflow_events rows with no matching claim (want 0)',
           (select count(*) from rcm.sim_workflow_events e
              left join rcm.fact_inpatient_claim f on f.claim_sk = e.claim_sk
              where f.claim_sk is null)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 5. Provenance labeling: every adjudication row is classed SIMULATED (§3.1).
    union all select 'adjudication_provenance_labeled', 'provenance', 'SIMULATED',
           'Share of sim_claim_adjudication rows classed SIMULATED (want 1.0)',
           (select count(*) from rcm.sim_claim_adjudication
              where sim_provenance = 'SIMULATED')::numeric,
           (select count(*) from rcm.sim_claim_adjudication)::numeric,
           1.0::numeric, 'critical'

    -- 6. Denial-category integrity: denied iff category present (schema invariant).
    union all select 'denial_category_consistency', 'validity', 'SIMULATED',
           'Denied claims whose denial_category is null (want 0)',
           (select count(*) from rcm.sim_claim_adjudication
              where sim_denial_flag and sim_denial_category is null)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 7. Money non-negativity on the SIMULATED layer.
    union all select 'sim_money_nonnegative', 'validity', 'SIMULATED',
           'sim adjudication rows with any negative money field (want 0)',
           (select count(*) from rcm.sim_claim_adjudication
              where sim_allowed_amount < 0 or sim_paid_amount < 0
                 or sim_denied_amount < 0)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 8. Paid never exceeds allowed (SIMULATED invariant).
    union all select 'sim_paid_le_allowed', 'validity', 'SIMULATED',
           'sim rows where paid > allowed (want 0)',
           (select count(*) from rcm.sim_claim_adjudication
              where sim_paid_amount > sim_allowed_amount)::numeric,
           0::numeric, 0::numeric, 'critical'

    -- 9. Billed charge present on the SOURCE fact (completeness of real money).
    union all select 'source_billed_present', 'completeness', 'SOURCE',
           'Share of claims with a non-null billed charge (want 1.0)',
           (select count(*) from rcm.fact_inpatient_claim
              where clm_tot_chrg_amt is not null)::numeric,
           (select n from claims)::numeric, 1.0::numeric, 'warning'

    -- 10. Diagnosis present: every claim has >=1 diagnosis slot.
    union all select 'diagnosis_present', 'completeness', 'SOURCE',
           'Share of claims with at least one diagnosis (want 1.0)',
           (select count(distinct claim_sk) from rcm.fact_claim_diagnosis)::numeric,
           (select n from claims)::numeric, 1.0::numeric, 'warning'

    -- 11. Quarantine load: validated-layer contract violations (0 on this subset).
    union all select 'quarantine_rows', 'validity', 'DERIVED',
           'Rows quarantined by data contracts (want 0 on the clean subset)',
           (select count(*) from rcm.dq_quarantine)::numeric,
           0::numeric, 0::numeric, 'warning'

    -- ---- Informational (EXPECTED by design; routed to Unknown member key 0) ----
    -- 12. Null-provider claims routed to the Unknown provider (key 0).
    union all select 'unknown_provider_routed', 'completeness_info', 'SOURCE',
           'Claims with no source provider, routed to Unknown (informational)',
           (select count(*) from rcm.fact_inpatient_claim where provider_key = 0)::numeric,
           (select n from claims)::numeric, null::numeric, 'info'

    -- 13. Null-DRG claims routed to the Unknown DRG (key 0).
    union all select 'unknown_drg_routed', 'completeness_info', 'SOURCE',
           'Claims with no source DRG, routed to Unknown (informational)',
           (select count(*) from rcm.fact_inpatient_claim where drg_key = 0)::numeric,
           (select n from claims)::numeric, null::numeric, 'info'

    -- 14. DRG display-name coverage (0 until the MS-DRG REFERENCE file lands).
    union all select 'drg_description_coverage', 'reference_enrichment', 'REFERENCE',
           'Share of DRG dim members with a description (0 until MS-DRG ref loaded)',
           (select count(*) from rcm.dim_drg where drg_desc is not null)::numeric,
           (select count(*) from rcm.dim_drg)::numeric, 1.0::numeric, 'info'
)
select
    check_id,
    dimension,
    subject_provenance,
    description,
    numerator,
    denominator,
    case when denominator = 0 then numerator            -- absolute-count checks
         else round(numerator / denominator, 6) end as metric_value,
    threshold_min,
    severity,
    case
        when severity = 'info' then null                -- informational, not scored
        when denominator = 0 then (numerator = threshold_min)   -- want-0 checks
        else (round(numerator / denominator, 6) >= threshold_min)
    end as pass_flag
from checks;

comment on view rcm.vw_data_quality_scorecard is
  'DERIVED data-quality scorecard: one row per warehouse integrity/completeness '
  'check. Failing rows are review flags, never fraud. Unknown-member routing and '
  'missing REFERENCE descriptions are informational (expected by design).';
