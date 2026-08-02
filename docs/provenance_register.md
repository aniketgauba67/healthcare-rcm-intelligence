# Provenance Register

Every curated field carries a classification (CLAUDE.md §3):
`SOURCE` (CMS files unmodified) · `DERIVED` (computed from SOURCE) ·
`REFERENCE` (official code/facility files) · `SIMULATED` (our generator).
data-engineer + simulation-engineer maintain this file; it is updated in the
same PR as any schema change.

## Raw source artifacts (Phase 1 ingestion)

Downloaded by `src/ingestion/` into `data/raw/` (gitignored, never committed).
Checksums, sizes, and row counts are measured at download time and recorded in
`config/sources.yaml` (committed) and `data/raw/manifest.json` (gitignored).

| Artifact | Source group | Classification | Vintage | Notes |
|---|---|---|---|---|
| `cms_synthetic/beneficiary_2024.csv` | cms_synthetic_claims | SOURCE | 2023-04 | Master Beneficiary Summary Base, 2024 enrollment year. Pipe-delimited. |
| `cms_synthetic/inpatient.csv` | cms_synthetic_claims | SOURCE | 2023-04 | Inpatient FFS claims (line-level). Pipe-delimited. |
| `nppes/nppes_ri_extract.csv` | nppes_npi | REFERENCE | 2026-07 | Rhode Island NPPES extract. Role reclassified 2026-07-22: VALIDATION SAMPLE only (spot-check real NPIs); superseded as the crosswalk provider pool by the nationwide Medicare Physician dataset per human decision. |
| `reference/hospital_general_information.csv` | hospital_general_information | REFERENCE | 2026-04 | CMS Hospital General Information; 5,432 real facilities (real CCNs). Facility crosswalk target, never joined directly. |
| `reference/medicare_providers_extract.csv` | medicare_providers | REFERENCE | 2024 | Medicare Physician & Other Practitioners by Provider; 1,296,739 real providers (NPI/specialty/state). Nationwide provider crosswalk pool. Real NPIs, never joined directly. |

### Code-set reference artifacts (Phase 3 prerequisite, added 2026-07-23)

Public CMS code files matching the **FY2023 / 2023** claims vintage (CLAUDE.md §2
vintage rule — never ICD-9). Downloaded as zips into `data/raw/reference/`
(gitignored); only the code + description text is parsed. Loaded by
`src/ingestion/reference_codes.py`.

| Artifact | Source group | Classification | Vintage | Notes |
|---|---|---|---|---|
| `reference/icd10cm_2023.zip` | icd10 | REFERENCE | FY2023 | CMS ICD-10-CM tabular descriptions; 73,674 dx codes parsed (member `icd10cm_codes_2023.txt`). |
| `reference/icd10pcs_2023.zip` | icd10 | REFERENCE | FY2023 | CMS ICD-10-PCS codes file; 78,530 proc codes parsed (member `icd10pcs_codes_2023.txt`). |
| `reference/hcpcs_2023.zip` | hcpcs | REFERENCE | 2023 | CMS Jan-2023 Alpha-Numeric file; 7,404 **Level II** codes parsed. CPT Level I (numeric, AMA), 2-char modifiers, and D-series (ADA) excluded at load (§3.7). |
| `reference/msdrg_v40_table5.zip` | ms_drg | REFERENCE | FY2023 | IPPS FY2023 Final Rule Table 5 (MS-DRG v40); 767 DRGs (title, MDC, type). |
| _(no file)_ | carc_codes | REFERENCE | labels-only | CARC used as denial-category LABELS only (§3.7). No X12 file downloaded, no X12 description text reproduced; `ref_carc` pairs 10 public CARC code identifiers with **project-authored** labels. |

## Validated layer — typed Parquet (`data/validated/`, gitignored)

Typed representations of the raw RIF SOURCE files (dtype standardization only —
no new computed columns), produced by `src/validation/` (`make stage`).
Classification is unchanged from the raw source.

| Artifact | Derived from | Classification | Rows | Notes |
|---|---|---|---|---|
| `beneficiary_2024.parquet` | `cms_synthetic/beneficiary_2024.csv` | SOURCE | 9,660 | 185 cols; typed dates/money/int, codes kept as text. |
| `inpatient.parquet` | `cms_synthetic/inpatient.csv` | SOURCE | 58,066 | 197 cols; 33 typed date columns. |

Row counts reconcile exactly to the raw source (enforced by `src.validation.run`).

Provenance rules enforced here:
- Synthetic claims carry **synthetic** provider/facility identifiers
  (`PRVDR_NUM`, `ORG_NPI_NUM`, `AT_PHYSN_NPI`). These do **not** join to the
  real NPPES NPIs or real hospital CCNs. Any link between synthetic claims and
  the NPPES/Hospital reference data is created **only** by the seeded, stratified
  simulated crosswalk (CLAUDE.md §3.4), which is classified `SIMULATED`.
- No raw source is ever classified `SIMULATED`; no downloaded reference file is
  presented as claims-linked truth.

## Warehouse tables and columns (star schema `rcm`)

Star schema over the validated RIF (DDL in `sql/ddl/`, load via
`src/ingestion/star_transform.py` + `load_postgres.py`). The simulated crosswalk
and all `sim_`-prefixed tables are added here by their owning agents.

| Table | Column(s) | Classification | Source / Generator | Notes |
|---|---|---|---|---|
| dim_date | all | DERIVED | generated calendar | from fact date columns; key 0 = Unknown. |
| dim_beneficiary | all except `bene_key` | SOURCE | beneficiary_2024 | `bene_key` surrogate is DERIVED; Unknown row DERIVED. |
| dim_provider | `prvdr_num`, `org_npi_num`, `provider_state_cd` | SOURCE | inpatient | synthetic ids (`is_synthetic_id=true`); NOT real CCN/NPI. `provider_key` DERIVED. |
| dim_drg | `drg_cd` | SOURCE | inpatient | `drg_key` DERIVED. `drg_desc` is **REFERENCE** — enriched by value join from `ref_msdrg` (MS-DRG v40, FY2023). Enriched rows carry `provenance='REFERENCE'`; 167/167 real DRGs matched (2026-07-23). |
| dim_discharge_status | `discharge_status_cd` | SOURCE | inpatient | `discharge_status_key` DERIVED. |
| fact_inpatient_claim | measures, degenerate `clm_id`, diagnosis codes | SOURCE | inpatient | surrogate/FK keys DERIVED; `length_of_stay_days` DERIVED. |
| fact_claim_revenue_line | `clm_line_num`, `rev_cntr`, `hcpcs_cd` | SOURCE | inpatient | surrogate/FK keys DERIVED. |
| fact_claim_diagnosis | `dgns_seq`, `icd_dgns_cd`, `poa_ind_sw` | SOURCE | inpatient | unpivot of ICD_DGNS_CD1..25; keys DERIVED. |
| **sim_facility_crosswalk** | all (every column `sim_`-prefixed per §3.2) | **SIMULATED** | seeded assignment | synthetic billing provider (`sim_prvdr_num`, FK to dim_provider) → REAL facility CCN (`sim_facility_ccn`, with `sim_facility_name`/`sim_facility_state`/`sim_facility_type`), stratified by state+type (`sim_match_rule`, `sim_same_state`, `sim_crosswalk_seed`, `sim_provenance`). Not a real linkage. |
| **sim_provider_crosswalk** | all (every column `sim_`-prefixed per §3.2) | **SIMULATED** | seeded assignment | synthetic attending physician (`sim_at_physn_npi`) → REAL Medicare NPI (`sim_real_npi`, with `sim_real_provider_state`/`sim_real_specialty`/`sim_assigned_postal_state`), stratified by coherent state + inpatient-plausible specialty (`sim_match_rule`, `sim_same_state`, `sim_crosswalk_seed`, `sim_provenance`). Not a real linkage. |
| dq_quarantine | all | DERIVED | contract engine | one row per data-contract violation (table, contract, entity key, reason). No SOURCE values beyond the offending key. |
| ref_icd10cm | `icd10cm_code`, `long_desc` | REFERENCE | FY2023 ICD-10-CM file | 73,674 diagnosis descriptions. Dotless tabular codes. |
| ref_icd10pcs | `icd10pcs_code`, `long_desc` | REFERENCE | FY2023 ICD-10-PCS file | 78,530 procedure descriptions. 7-char codes. |
| ref_hcpcs | `hcpcs_code`, `long_desc`, `short_desc` | REFERENCE | 2023 HCPCS Alpha-Numeric | 7,404 Level II descriptions. CPT Level I / modifiers / D-series excluded (§3.7). |
| ref_msdrg | `drg_cd`, `drg_title`, `mdc`, `drg_type` | REFERENCE | IPPS FY2023 Table 5 | 767 MS-DRG v40 titles. Enriches `dim_drg.drg_desc`. |
| ref_carc | `carc_code` | REFERENCE | X12 CARC identifiers | Code identifiers only (§3.7). `category_label` is **DERIVED** (project-authored); NO X12 description text reproduced. Join target for `sim_denial_carc_group`. |
| **sim_payer** | all | **SIMULATED** | config/simulation.yaml | invented payer archetypes. Medicare FFS has ONE payer; this dimension is entirely simulated (§3.5). Not modelled on or named after any real insurer. |
| **sim_service_line** | all | **SIMULATED** | config/simulation.yaml | coarse MS-DRG numeric-range buckets. The boundaries are a design choice of the simulation, NOT an official CMS MS-DRG/MDC taxonomy — which is why the column is SIMULATED although its input `drg_cd` is SOURCE. |
| **sim_authorization_eligibility** | all | **SIMULATED** | `src/simulation/` | pre-submission authorization + eligibility facts. The CMS synthetic claims contain none of this. |
| **sim_documentation_coding** | all | **SIMULATED** | `src/simulation/` | pre-submission documentation + coding quality facts. Invented. |
| **sim_claim_adjudication** | all | **SIMULATED** | `src/simulation/` | denial outcome, money, and the submission→adjudication→payment timeline. The source claims contain no denials and no such dates. `sim_latent_p` and `sim_provider_quality_latent` are generator internals stored for validation only — never model features (§4). |
| **sim_appeals** | all | **SIMULATED** | `src/simulation/` | one row per (claim, appeal level). Invented; the source contains no appeals. `sim_appeal_latent_p` is validation-only. |
| **sim_workflow_events** | all | **SIMULATED** | `src/simulation/` | process-mining event log. Invented; the source contains no workflow events. |
| **sim_operating_costs** | all | **SIMULATED** | `src/simulation/` | cost to collect, accumulated from simulated touch minutes in `sim_workflow_events`. Invented. |

The `sim_*_crosswalk` tables are the ONLY link between synthetic claims and real
CCNs/NPIs, and every row is a seeded random assignment (seed
`config/simulation.yaml:linkage.crosswalk_seed`), classified SIMULATED — never
presented as a real correspondence (CLAUDE.md §3.4). All non-`sim_` warehouse
columns remain SOURCE/DERIVED.

## The simulated adjudication layer (Phase 2)

Generated by `src/simulation/` from `config/simulation.yaml` (`make simulate`),
loaded by `src/simulation/load_sim.py` (`make simulate-warehouse`). Every table
and every column is classified **SIMULATED**, and every row carries
`sim_provenance = 'SIMULATED'` plus the `sim_config_version` and `sim_seed` that
produced it — so a Parquet file that escapes into a demo bundle still declares
its own provenance.

**The claims contain no adjudication data.** CMS synthetic Medicare FFS claims
carry service dates and payment amounts. They do not carry denials, submission
or adjudication dates, appeals, or workflow events. All of that is fabricated.
Nothing in these tables describes real Medicare, Medicare Advantage, commercial,
or Medicaid adjudication behaviour. Calibration ranges and their published
anchors are in `docs/assumptions.md`, each labelled a DESIGN CHOICE.

Naming rule (§3.2): every column carries the `sim_` prefix except `claim_sk` and
`clm_id`, which are the warehouse's DERIVED surrogate key and SOURCE degenerate
key respectively. Those two are deliberately *not* renamed — prefixing them
would misrepresent a real key as generated. No SOURCE value is copied into a
`sim_` column: billed charges in particular stay in
`fact_inpatient_claim.clm_tot_chrg_amt` and are reached by join, so that no real
amount is ever displayed under a simulated name.

One column deserves its own note. `sim_service_line_id` is computed
deterministically from the SOURCE `drg_cd`, which would normally make it
DERIVED. It is classified SIMULATED because the bucket boundaries are an
arbitrary grouping invented for this project, not an official CMS MS-DRG/MDC
taxonomy; calling it DERIVED would imply a CMS grouping that does not exist.

Leakage interface (§4.5): the authoritative list of which simulated columns may
and may not be used as model features is `docs/simulated_forbidden_columns.md`,
published by simulation-engineer so ml-engineer never needs to read
`src/simulation/`.

Reproducibility guarantee (simulation): same `seed` in `config/simulation.yaml`
⇒ byte-identical output, defined as the SHA-256 of each table's canonical CSV
serialization and recorded in `data/simulated/simulation_report.json`. Verified
2026-07-22 across two separate `make simulate` invocations: all 8 canonical
hashes matched, and in fact all 8 Parquet files were byte-identical too. The
guarantee is stated against the CSV hash rather than the Parquet bytes because
Parquet embeds writer metadata that can differ between pyarrow builds without a
single value having changed — the contract should not depend on a library
version. Each
component draws from an independently *named* RNG stream, so adding a component
never perturbs an existing one. Changing `seed`, changing any calibration
parameter, or reloading source data that renumbers `claim_sk` all change the
output; the loader refuses to attach the layer to a star schema it does not
match.

Reproducibility guarantee (crosswalk): the crosswalk is byte-identical for the
same `crosswalk_seed` **and** the same reference vintages. The facility/provider
reference vintages are pinned in `config/sources.yaml` (`hospital_general_information`
2026-04, `medicare_providers` 2024); re-pulling a reference at a new vintage will
change the assignment. The crosswalk integrity checks (FK, provenance, counts)
run identically against live Postgres and the DuckDB CI mirror.

## The analytics KPI views (Phase 3)

`sql/views/` defines 9 read-only views (`vw_claim_enriched` base + 8
metric-contract views), applied by `sql/views/apply_views.py` (`make views`) and
guarded by the reconciliation gate `sql/quality/view_reconciliation.py` (21
checks). As an analytics layer they are classified **DERIVED**: no view
introduces a new fact, each is a reprojection/aggregation of already-registered
warehouse tables. **Provenance is not laundered by a view.** A column keeps the
class of its input — SOURCE fields (real Medicare billed charge, the one real
Medicare paid amount) stay SOURCE, `sim_*`-derived measures stay SIMULATED,
REFERENCE code descriptions (`drg_desc`, etc.) stay REFERENCE and are display-only.
Per-column provenance is stated authoritatively in each view's SQL header block
(grain, sources, per-column class, and the control query it must reconcile to);
those headers are the register's cited source of truth for this layer, and
`view_reconciliation.py` asserts the labeled invariants stay true against live PG.

Two honesty rules are enforced structurally, not just documented:

- **Payer views are 100% SIMULATED (§3.5).** `vw_payer_performance` and every
  payer-dimension measure elsewhere describe invented archetypes, never any real
  insurer; the header carries the mandatory banner and every dashboard/export on
  them must too.
- **Facility/provider grain keys on the synthetic `prvdr_num`, never on
  `sim_facility_ccn`/`sim_facility_name`** (§3.2 crosswalk ruling — the crosswalk
  multiplexes 4,876 synthetic providers onto 2,857 real CCNs). Real CCN/name are
  carried display-only; the reconciliation gate check
  `clean_claim:grain_is_synthetic_prvdr_num` fails the build if a view keys on CCN.
- **The `sim_` prefix survives the view boundary (§3.2, team-lead ruling
  2026-07-27).** The simulated-linkage columns are emitted by `vw_claim_enriched`
  as `sim_facility_ccn`/`sim_facility_name`/`sim_facility_state`/
  `sim_facility_type` with no alias back to a bare name, re-exported by
  `vw_clean_claim_performance` as `sim_display_facility_*` and by
  `vw_work_queue_priority` as `sim_facility_name`. Rationale: `vw_claim_enriched`
  is the flattened matrix the Phase 4 feature store consumes and the §4 leakage
  blacklist matches on COLUMN NAMES, so aliasing the prefix away at the view
boundary would delete the provenance marker exactly where §4 depends on it.

The same rule applies to simulated-derived outputs. `vw_claim_enriched` preserves
`sim_adjudicated`, `sim_clean_claim_flag`, `sim_first_pass_paid_flag`,
`sim_ar_open_flag`, and `sim_ar_balance_amt`; `vw_work_queue_priority` preserves
`sim_action_type`, `sim_dollars_at_stake`, `sim_heuristic_priority_score`,
`sim_priority_tier`, and `sim_appeal_levels`. The API, dashboard, and committed
demo bundle use those same names, so no presentation layer removes the marker.
  Guarded by `tests/contracts/test_view_sim_prefix.py` (static) and
  `tests/integration/test_crosswalk_prefix_postgres.py` (live catalog).

Two views are explicitly pre-Phase-4 scaffolds, self-declaring in every row:
`vw_work_queue_priority` is a HEURISTIC PLACEHOLDER (`is_heuristic_placeholder`
true, `sim_heuristic_priority_score` and `sim_priority_tier` — not a model, not a
probability, not an Expected Net Recovery) and `vw_model_monitoring` is a DRIFT SCAFFOLD (`is_drift_scaffold`
true — observed input distributions only, no score/prediction). Phase 4 Model A/C
replace them.

The `notebooks/` EDA layer (`01`–`06`) reads these views + `sim_*` tables and is
DERIVED analysis output that writes nothing to the warehouse; every notebook
renders the SIMULATED banner and frames anything it flags as a review signal,
never fraud. Notebook 06 (interrupted time series) is illustrative-only, on a
clearly-labeled hypothetical intervention, and persists no intervention field.

## The committed Model A training matrix (Phase 4)

`artifacts/features/model_a_training_matrix.parquet` (1.4 MB) with its sidecar
`model_a_training_matrix.json`. **This is one of exactly two data files in the
repository that are committed to git, and therefore openable from a clean clone
with no database** — the other is the Phase 5 hosted-demo bundle registered in the
next section. Until Phase 5 this sentence read "the only data file", which was
true when written and false as soon as an 8.0 MB `.duckdb` was committed; a
register that keeps a stale exclusivity claim is asserting something it no longer
checks. Everything else under `data/` and `models_artifacts/` is gitignored. Both
are registered for the same reason: they are the artifacts most likely to be
inspected out of context, so they are the ones that can least afford to be
unlabelled.

| Property | Value |
|---|---|
| Grain | one row per claim — 20,867 rows, 44 columns |
| Contents | 39 features + 4 passthrough + 1 fold label |
| Regeneration | `make features` (reads the warehouse, writes this file + manifest) |
| Population | every claim in `rcm.sim_claim_adjudication`, inner-joined to its pre-submission simulated facts and its CMS claim row |
| Row order | `sim_submission_date`, then `claim_sk` — a total order, so the file is reproducible |
| Split | temporal, cut `2021-12-28`; 16,694 train / 4,173 test (`config/model.yaml: split`) |
| Label | `sim_denial_flag` — **SIMULATED**, and forbidden as a feature |

**Nothing in this file is real.** The claim facts are official CMS *synthetic*
Medicare data (SOURCE); everything `sim_`-prefixed is generated by this
project's simulation layer (SIMULATED). No real patient, provider, payer or
adjudication data exists in it. The crosswalked real CCNs and NPIs are **not**
present — the provider column is the synthetic `prvdr_num` (CLAUDE.md §3.4).

### Per-column provenance

**Every `sim_`-prefixed column in this file is SIMULATED** — 34 of the 44,
comprising 32 of the 39 features plus two passthrough columns (the label
`sim_denial_flag` and the time column `sim_submission_date`). The remaining 10
are 4 SOURCE and 6 DERIVED; 34 + 4 + 6 = 44, so no column is unclassified. That
is the whole rule, and the tables below exist so a reader does not have to take
it on faith.

*Features from the CMS claim — SOURCE (3):*

| Column | Classification | From |
|---|---|---|
| `billed_charge_amt` | SOURCE | `fact_inpatient_claim.clm_tot_chrg_amt`, renamed only — the value is the CMS synthetic claim's billed charge, unmodified |
| `drg_cd` | SOURCE | `dim_drg.drg_cd` (the MS-DRG coded on the claim; the REFERENCE `drg_desc` is **not** carried here) |
| `provider_state_cd` | SOURCE | `dim_provider.provider_state_cd` — the *synthetic* provider's state, never the crosswalked real facility's |

*Features computed from SOURCE — DERIVED (4):*

| Column | Classification | Computed from |
|---|---|---|
| `length_of_stay_days` | DERIVED | `fact_inpatient_claim` discharge − admission + 1 (already DERIVED in the warehouse) |
| `diagnosis_count` | DERIVED | row count over `fact_claim_diagnosis` |
| `log_billed_charge_amt` | DERIVED | `log1p(clm_tot_chrg_amt)` |
| `patient_age_years` | DERIVED | `dim_beneficiary.birth_date` and the claim's admission date |

*Features from the simulation layer — SIMULATED (32).* Grouped by the `sim_`
table they read; all are pre-submission facts by construction (CLAUDE.md §4):

| Source table (all SIMULATED) | Columns |
|---|---|
| `sim_authorization_eligibility` | `sim_auth_required`, `sim_auth_obtained`, `sim_auth_missing`, `sim_auth_obtained_late`, `sim_eligibility_checked`, `sim_eligibility_failed`, `sim_secondary_payer_present`, `sim_auth_decision_lead_days` |
| `sim_documentation_coding` | `sim_documentation_complete`, `sim_coder_query_outstanding`, `sim_coding_specificity_deficit`, `sim_duplicate_submission_flag`, `sim_documentation_score`, `sim_coding_complexity_score`, `sim_coding_lag_days` |
| `sim_claim_adjudication` (pre-submission fields only) | `sim_late_filing_flag`, `sim_days_service_to_submission`, `sim_filing_limit_days`, `sim_filing_headroom_days`, `sim_filing_use_ratio`, `sim_submission_month` |
| `sim_workflow_events` (rows at or before `CLAIM_SUBMITTED` only) | `sim_pre_submission_touch_minutes`, `sim_coding_to_submission_hours` |
| `sim_payer` / `sim_service_line` | `sim_payer_id`, `sim_service_line_id` |
| prior-period aggregates of `sim_denial_flag` | `sim_overall_prior_denial_rate`, `sim_payer_prior_denial_rate`, `sim_payer_prior_claims`, `sim_provider_prior_denial_rate`, `sim_provider_prior_claims`, `sim_service_line_prior_denial_rate`, `sim_service_line_prior_claims` |

The seven prior-period columns deserve their own note. They are the only
features that read the outcome at all, they do so under the CLAUDE.md §4.2
exemption (strictly prior window, 60-day embargo), and **they are SIMULATED
because a denial rate here is an aggregate of a fabricated denial** — not a real
Medicare denial rate, whatever the column name suggests to a reader who arrives
at the Parquet file without this page. `sim_provider_prior_denial_rate` is keyed
on the synthetic `prvdr_num`, never on a crosswalked real CCN.

*Passthrough and fold label (5) — carried for splitting, slicing and joining,
dropped before any estimator sees the matrix:*

| Column | Classification | Note |
|---|---|---|
| `claim_sk` | DERIVED | warehouse surrogate key |
| `prvdr_num` | SOURCE | synthetic billing-provider id; **not** a real NPI or CCN |
| `sim_submission_date` | SIMULATED | the point-in-time boundary; the claims carry no submission date |
| `sim_denial_flag` | SIMULATED | the label. On `forbidden_features`; never a feature |
| `split` | DERIVED | `train`/`test`, from `sim_submission_date` against the `split.cut` in `config/model.yaml` |

Three naming decisions here are deliberate and are recorded rather than left to
be re-derived:

- **`sim_overall_prior_denial_rate` carries the prefix** (renamed from
  `overall_prior_denial_rate`, 2026-07-28). It is computed entirely from
  `sim_denial_flag`, so under §3.2 it is a simulated column and must say so. An
  unprefixed `overall_prior_denial_rate` sitting in the one committed data file
  would read to an outsider as a real Medicare book denial rate. Its Model C
  counterpart `sim_overall_prior_overturn_rate` was renamed with it.
- **`split` does not carry the prefix, and is the one column here whose name
  does not follow its input.** It is a fold assignment — metadata about OUR
  EXPERIMENT, not an attribute of a claim and not a statement about the simulated
  world. §3.2 governs simulated values; a partition label is not one. Classified
  DERIVED, with its input date SIMULATED, and flagged here rather than silently
  prefixed or silently left.
  This page previously justified it differently — that the §4.1 guard discovers
  the column by name from `{is_train, split, fold}`, so a rename would blind the
  temporal check. QA ruling C (2026-07-28) upheld the outcome and **rejected that
  reasoning**, and the correction is recorded rather than swapped in silently: a
  guard must never be the reason a correctness-improving rename cannot happen,
  because that turns the safety net into a constraint on the code it protects.
  The general form "we cannot rename X because a guard looks for it by name" must
  always lose. Name-based discovery is tracked separately as `[SPLIT-DISCOVERY]`.
- **`sim_log_denied_amount` (Model C) leads with the marker** (renamed from
  `log_sim_denied_amount`, 2026-07-29). It is `log1p(sim_denied_amount)`, so §3.2
  makes it a simulated column. The marker was present but INFIXED, which satisfies
  the property §3.2 protects — nobody reads `log_sim_denied_amount` as a real
  Medicare quantity — and it was twice ruled a naming preference on a MEASURED
  exposure of zero. That measurement still held at the rename: Model C's frame is
  not committed, `models_artifacts/` is gitignored, Model C publishes no SHAP, and
  no work-queue or slice column carries the name. It was renamed anyway because it
  was the last infixed name in either feature set, so the cost was one feature and
  the gain is a rule with no exception list — `tests/features/
  test_feature_marker_position.py` now states §3.2 literally at the feature layer.
  Phase 5 adds a dashboard, an API and a demo extract, and an exception holds only
  while every future author remembers it. Model C's frame is not a curated table
  and appears in no table on this page or in `docs/data_dictionary.md`, which is
  why this rename adds a note here and no row anywhere.

### Why it is committed, and how staleness is caught

CLAUDE.md §4.1 requires a build-failing test whenever a forbidden column enters a
training matrix, and that test cannot check a matrix it cannot see. Regenerating
on demand would make the guard live only on a machine with a loaded warehouse —
the same shape as the "green suite over a warehouse nobody checked" failure this
project has already hit once. Committing it makes the §4.1 value probes run on a
clean clone, in CI, over real feature values. The `!artifacts/features/*.parquet`
exception in `.gitignore` exists for this file. Only Model A's matrix lives here;
Model C's frame goes to the gitignored `models_artifacts/model_c/`, because the
guard's forbidden set is Model A's and Model C is *permitted* to see the denial.

The sidecar manifest records rows, the feature list, the split boundary, the
parquet SHA-256 and a digest of every `forbidden_*` block in `config/model.yaml`,
so a widened blacklist with no rebuild fails a unit test instead of leaving the
guard checking an older column set. **The manifest contains no wall clock**: every
field is a function of the content or the config, so `make features`, `make train`
and any test that trains against live Postgres all emit byte-identical bytes
(verified 2026-07-28 — two consecutive `make features` runs and two `make train`
runs produced manifest SHA-256 `d5c1ca88aa0786f7d39c7ac055b13c264654ea1a84ef9e75412c5d2fed454e8c`
unchanged). A committed artifact that changed on every test run would train
reviewers to ignore its diff, which is how a real content change slips through.
The build time of record is the git commit date.

## The committed hosted-demo bundle (Phase 5)

`dashboard/demo_data/rcm_demo.duckdb` — **8,400,896 bytes (8.0 MB), 16 tables,
71,813 rows**, committed to git via the `!dashboard/demo_data/*.duckdb` exception
in `.gitignore`. CLAUDE.md §2 locks the hosted demo to a bundled Parquet/DuckDB
extract rather than live Postgres, and this is that extract: it opens with no
database, no network, no credentials and no environment variables.

**It is the most exposed artifact in this repository.** The training matrix is a
feature file that a reader has to go looking for; this is the file the deployed
demo *is*. Anyone who clones the repo or visits the hosted app is reading it,
usually without reading anything else — so it is the artifact least able to rely on
context supplied elsewhere, and §3.3 is registered here in full rather than by
pointing at code.

| Property | Value |
|---|---|
| Path | `dashboard/demo_data/rcm_demo.duckdb` |
| Size | 8,400,896 bytes (8.0 MB) |
| Tables | 16 — 9 curated-view copies, 5 model outputs, 2 self-describing meta tables |
| Rows | 71,813 across all tables |
| Declaration | `src/demo/spec.py` — the single authority; the build refuses to write an undeclared table and refuses to omit a declared one |
| Regeneration | `make demo-extract` (needs the PostgreSQL warehouse **and** the model artifacts) |
| Opened | read-only, one cursor per thread (a hosted demo shares one process between viewers) |
| Published-surface entry | `src/features/provenance.py`, glob `dashboard/demo_data/*.duckdb` |
| Bundle's own register | the `demo_manifest` table, 14 rows — one per *data* dataset |
| Build stamp | the `demo_build_info` table: git commit, branch, dirty-tree flag, reproducible UTC source-commit timestamp, source vintages |

`make demo-extract` writes a complete candidate before replacing the published
artifact. When every table schema and row multiset matches the existing bundle,
the existing bytes are retained. This keeps the pinned SHA stable across honest
no-op rebuilds despite DuckDB checkpoint padding that is not part of the logical
data contract; any schema or row change replaces the artifact and requires a new
pin.

### Per-table classification

Every table carries a §3.1 class and a declared `contains_simulated` flag. The
flag is **declared, not inferred from column names**, because a table can be
entirely simulated in substance while carrying no `sim_` column of its own — the
three marked `sim: 0` below with `contains_simulated = True` are exactly that case,
and a spelling-based rule would call them clean.

| Table | Class | Rows | Cols | `sim_` cols | Contains simulated |
|---|---|---|---|---|---|
| `vw_claim_enriched` | MIXED | 20,867 | 77 | 48 | yes |
| `vw_executive_rcm_summary` | MIXED | 109 | 24 | 7 | yes |
| `vw_denial_root_cause` | SIMULATED | 34 | 16 | 7 | yes |
| `vw_ar_aging` | SIMULATED | 5 | 10 | 1 | yes |
| `vw_payer_performance` | SIMULATED | 5 | 22 | 10 | yes |
| `vw_clean_claim_performance` | MIXED | 4,877 | 18 | 5 | yes |
| `vw_work_queue_priority` | MIXED | 2,663 | 16 | 5 | yes |
| `vw_data_quality_scorecard` | DERIVED | 15 | 10 | 0 | no |
| `vw_model_monitoring` | SIMULATED | 981 | 6 | 0 | **yes** — drift here is drift in the simulation |
| `model_a_scores` | DERIVED | 20,867 | 7 | 4 | yes |
| `model_a_reason_codes` | DERIVED | 20,865 | 7 | 1 | yes |
| `model_a_shap_global` | DERIVED | 39 | 5 | 2 | yes |
| `model_c_work_queue` | DERIVED | 469 | 13 | 6 | yes |
| `model_metrics` | DERIVED | 2 | 4 | 0 | **yes** — every metric scores a SIMULATED label |
| `demo_manifest` | DERIVED | 14 | 8 | 0 | no |
| `demo_build_info` | DERIVED | 1 | 8 | 0 | no |

The nine `vw_` tables are `select * from rcm.vw_...` copies — **verbatim, never
recomputed**. That is what makes §7's "dashboard totals reconcile to SQL control
queries" a property of the pipeline instead of a claim somebody checked once: a
page cannot hold a second definition of a KPI, because it never computes one.

### What is real in this file, and what is not

**Nothing downstream of claim submission is real.** The claim facts are official
CMS *synthetic* Medicare records (SOURCE) containing no real patients. Every
denial, appeal, payment, payment date, workflow event, operating cost and the
entire multi-payer dimension is generated by this project's simulation layer
(SIMULATED, §3.5 — Medicare FFS has exactly one payer, and the five archetypes in
`vw_payer_performance` are invented and named after no real insurer).

The real facility and provider names in `vw_claim_enriched` and
`vw_clean_claim_performance` are **display-only** and **forbidden as a model
feature** (§3.4). They arrive through a seeded crosswalk that maps 4,876 synthetic
billing providers onto 2,857 real CCNs — 8:1 at worst by CCN, and **15:1 at worst
by NAME**, since those CCNs carry only 2,816 distinct display names. The name is
the key a dashboard is likelier to group on and it is the worse one, so every
provider-level table here is keyed on the synthetic `prvdr_num`, never on
`sim_facility_ccn` and never on `sim_facility_name`.

**Reference-vintage skew.** The claims are vintage 2023-04 and their code sets
match (ICD-10-CM/PCS FY2023, HCPCS 2023, MS-DRG v40 FY2023). The two crosswalk
reference files do not: Hospital General Information is vintage 2026-04 and the
Medicare Physician & Other Practitioners file is data year 2024 — roughly three
years newer than the claims they decorate. A facility type or a provider specialty
in this bundle is what a reference file recorded about three years *after* the
claim was filed.

### Artifact-specific notes

1. **The release candidate was built from a clean working tree.** Its
   `demo_build_info` row carries `git_tree_dirty = false` and exact source commit
   `aabbb5073a841f7746c63018ef5638fb9b412fee`. The artifact SHA-256 is
   `abf7853605bdd3dc439325d01831d3be85bcc863bfb046f4260a878d4d7189b7`.
2. **`demo_manifest` describes 14 of the 16 tables, not all 16.** It omits itself
   and `demo_build_info`. That is a deliberate stop to the self-reference, and it
   is stated here because "the bundle ships its own register" is otherwise read as
   total coverage. The two omitted tables are classified in the table above.
3. **A `.duckdb` is opaque to the repository's text-based provenance checks.**
   Rule 3 cannot read a binary, so registering the path alone would make rule 1
   green while leaving rule 3 silent over the one file most likely to be read out
   of context. `tests/features/test_demo_bundle_provenance.py` closes that by
   opening the bundle and applying the §3.2 marker rule to the columns actually
   in it — the check follows the file rather than stopping at its extension.
