-- ============================================================================
-- vw_model_monitoring.sql — input-distribution monitoring (DRIFT SCAFFOLD)
--
-- ***  DRIFT SCAFFOLD — NO MODEL EXISTS YET  ***
--      Phase 4 has not run: there is no trained Model A, no scored predictions,
--      no calibration, no live inference. This view monitors the DISTRIBUTION of
--      the legitimate pre-submission feature candidates (and the outcome rate)
--      over submission-month cohorts, so that when Phase 4 lands, a Population
--      Stability Index / drift comparison can be computed against a frozen
--      training-window baseline. Until then it reports observed means/rates ONLY.
--      It contains NO model score, NO prediction, NO probability. Every row
--      carries is_drift_scaffold = true and metric_kind describes what it is.
--
-- Grain:        one row per (sim_submission_year_month, feature_name). The monitored
--               columns are exactly the pre-submission Model A candidates
--               (knowable before submission, CLAUDE.md §4) plus the denial
--               OUTCOME rate, which is flagged metric_kind='outcome_rate' — it is
--               a monitoring target, NOT a feature (never train on it).
--
-- Sources:      rcm.vw_claim_enriched.
-- Provenance:   SIMULATED. The period (sim_submission_year_month), the cohort size
--               (sim_n_claims) and every monitored value (sim_metric_value) come
--               from SIMULATED pre-submission facts / SIMULATED outcome, so all
--               three carry `sim_` (§3.2). feature_name / metric_kind /
--               is_drift_scaffold are PROCESS labels about this scaffold and stay
--               bare. No SOURCE value here.
--
-- HONESTY:      These are SIMULATED features/outcomes; drift here is drift in the
--               simulation, not in real payer behaviour. Not a fraud signal.
--
-- Control query (must reconcile):
--   select sum(sim_n_claims) from rcm.vw_model_monitoring where feature_name='denial_rate';
--     -- = 20867 (one full pass of the book for that metric)
--   select count(distinct sim_submission_year_month) from rcm.vw_model_monitoring
--     = distinct months in rcm.vw_executive_rcm_summary.
-- ============================================================================

-- `create or replace view` CANNOT rename an output column, so a tree that
-- already holds an older vw_model_monitoring would keep the old names and this
-- file would silently fail to take effect. Locally that never showed, because
-- vw_claim_enriched drop-cascades and takes its dependants with it; a FRESH
-- database has no such cascade, which is how the hosted init surfaced it.
drop view if exists rcm.vw_model_monitoring cascade;
create or replace view rcm.vw_model_monitoring as
with base as (
    select
        sim_submission_year_month,
        count(*)                                                            as sim_n_claims,
        avg(case when sim_auth_required then 1 else 0 end)                  as auth_required_rate,
        avg(case when sim_auth_missing then 1 else 0 end)                   as auth_missing_rate,
        avg(case when sim_eligibility_failed then 1 else 0 end)             as eligibility_failed_rate,
        avg(case when sim_documentation_complete then 1 else 0 end)         as documentation_complete_rate,
        avg(case when sim_coding_specificity_deficit then 1 else 0 end)     as coding_specificity_deficit_rate,
        avg(case when sim_duplicate_submission_flag then 1 else 0 end)      as duplicate_submission_rate,
        avg(sim_documentation_score)                                       as avg_documentation_score,
        avg(sim_coding_complexity_score)                                   as avg_coding_complexity_score,
        avg(case when sim_denial_flag then 1 else 0 end)                    as denial_rate
    from rcm.vw_claim_enriched
    group by sim_submission_year_month
)
select sim_submission_year_month, 'auth_required_rate'        as feature_name,
       'pre_submission_feature' as metric_kind, sim_n_claims,
       round(auth_required_rate, 6)        as sim_metric_value, true as is_drift_scaffold from base
union all select sim_submission_year_month, 'auth_missing_rate', 'pre_submission_feature', sim_n_claims,
       round(auth_missing_rate, 6), true from base
union all select sim_submission_year_month, 'eligibility_failed_rate', 'pre_submission_feature', sim_n_claims,
       round(eligibility_failed_rate, 6), true from base
union all select sim_submission_year_month, 'documentation_complete_rate', 'pre_submission_feature', sim_n_claims,
       round(documentation_complete_rate, 6), true from base
union all select sim_submission_year_month, 'coding_specificity_deficit_rate', 'pre_submission_feature', sim_n_claims,
       round(coding_specificity_deficit_rate, 6), true from base
union all select sim_submission_year_month, 'duplicate_submission_rate', 'pre_submission_feature', sim_n_claims,
       round(duplicate_submission_rate, 6), true from base
union all select sim_submission_year_month, 'avg_documentation_score', 'pre_submission_feature', sim_n_claims,
       round(avg_documentation_score, 6), true from base
union all select sim_submission_year_month, 'avg_coding_complexity_score', 'pre_submission_feature', sim_n_claims,
       round(avg_coding_complexity_score, 6), true from base
-- OUTCOME rate: a monitoring target, NOT a feature. Never a Model A input (§4).
union all select sim_submission_year_month, 'denial_rate', 'outcome_rate', sim_n_claims,
       round(denial_rate, 6), true from base;

comment on view rcm.vw_model_monitoring is
  'DRIFT SCAFFOLD (no model yet, Phase 4). Monthly distribution of pre-submission '
  'Model A feature candidates + the denial OUTCOME rate (metric_kind=outcome_rate, '
  'never a feature). Contains no prediction/score. All values are SIMULATED '
  '(sim_ prefixed); is_drift_scaffold=true on every row.';
