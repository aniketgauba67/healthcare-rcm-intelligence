# Data Dictionary

Phase 1 pipeline, source → warehouse (see `docs/provenance_register.md` for the
per-column classification of every table):

```
raw (data/raw, gitignored)          make ingest
  CMS synthetic RIF (SOURCE)  ─┐
  NPPES RI extract (REFERENCE) │   validated (data/validated)   make stage
  Hospital General Info (REF)  ├──▶  typed Parquet ──▶ contracts + quarantine  make contracts
  Medicare providers (REF)    ─┘        │                 (reconciliation_report.json)
                                        ▼
                              warehouse (PostgreSQL rcm)        make warehouse
                                dims + facts (SOURCE/DERIVED)
                                sim_*_crosswalk (SIMULATED, seeded)
                                dq_quarantine (DERIVED)
```

Core principle (CLAUDE.md §3): no simulated value is ever presented as real.
Synthetic claim identifiers never join real CCNs/NPIs directly — the only link
is the seeded, clearly-labelled `sim_*_crosswalk`. Sections below cover each
layer; the raw RIF is pipe-delimited, reference files are comma-delimited.

## Validated layer — typed Parquet (`data/validated/`)

`src/validation/` (`make stage`) standardizes the raw pipe-delimited RIF CSVs
into typed Parquet without altering values (leading zeros, signed synthetic
ids, and ICD codes are preserved as text). Dtypes are resolved by explicit,
auditable rules in `src/validation/schemas.py`:

| Kind | Rule | Arrow type |
|---|---|---|
| date | name matches `_DT` + optional index digit (e.g. `CLM_FROM_DT`, `PRCDR_DT1..25`), or `COVSTART` | `date32[day]` |
| money | name ends `_AMT`, or `CLM_PPS_CPTL_DRG_WT_NUM` | `float64` |
| int | name ends `_CNT`/`_MONS`/`_DAYS`/`_QTY`/`_YR`, or `CLM_LINE_NUM` | `int64` (nullable) |
| string | everything else — codes (`_CD`), ids (`_NUM`, `_NPI`, `_ID`), ZIP, switches (`_SW`/`_IND`) | `string` |

Dates parse with format `%d-%b-%Y` (e.g. `25-Mar-2015`); empty strings become
null, and non-empty values that fail to parse are counted per column and
reported (data-quality signal), never silently dropped.

Staged outputs (row counts reconcile exactly to the raw source):

| Parquet | Rows | Columns | Typed date cols |
|---|---|---|---|
| `data/validated/beneficiary_2024.parquet` | 9,660 | 185 | 3 |
| `data/validated/inpatient.parquet` | 58,066 | 197 | 33 |

Known limitation: a few RIF columns whose names are truncated so the `_DT`
token is not the final token (e.g. `NCH_BENE_MDCR_BNFTS_EXHTD_DT_I`,
`NCH_ACTV_OR_CVRD_LVL_CARE_THRU`) remain string-typed pending the official RIF
data dictionary. Their raw text is preserved losslessly; only the type hint is
conservative. These are rarely populated in the synthetic data.

## Data contracts, quarantine, and reconciliation (`make contracts`)

`src/validation/contracts.py` enforces the validated-layer contracts:

| Contract | Level | Action on failure |
|---|---|---|
| required columns | table | fail the table |
| key uniqueness (`BENE_ID`; `CLM_ID`+`CLM_LINE_NUM`) | table | fail the table + quarantine dup rows |
| date ordering (`CLM_FROM_DT ≤ CLM_THRU_DT`) | row | quarantine the row |
| non-negative money (`CLM_PMT_AMT`, `CLM_TOT_CHRG_AMT`, …) | row | quarantine the row |

Failing rows are isolated in `rcm.dq_quarantine` / `data/validated/quarantine/
quarantine.parquet` (columns: `table_name`, `contract`, `entity_key`, `reason`)
— never silently dropped. `make contracts` also writes
`data/validated/reconciliation_report.json` (per-table rows, contract results,
quarantine counts, and staged-vs-source row reconciliation). On this subset:
all contracts pass, 0 quarantined, staged rows tie to source (9,660 / 58,066).
The staging layer additionally counts present-but-unparseable money/int values
(`numeric_null_from_nonempty`) alongside dates, so a dirty value surfaces as a
data-quality signal instead of a silent null.

## Raw layer — CMS Synthetic Medicare RIF (SOURCE, vintage 2023-04)

Files are **pipe-delimited** (`|`) plain text. Identifiers (`BENE_ID`,
`CLM_ID`, `PRVDR_NUM`, NPIs) are **synthetic** and do not correspond to real
people, providers, or facilities.

### `beneficiary_2024.csv` — Master Beneficiary Summary Base (enrollment)
Grain: one row per synthetic beneficiary per reference year. Key: `BENE_ID`.

| Column | Meaning |
|---|---|
| `BENE_ID` | Synthetic beneficiary id (join key to claims). |
| `STATE_CODE`, `COUNTY_CD`, `ZIP_CD` | SSA/geographic residence codes. |
| `BENE_BIRTH_DT`, `BENE_DEATH_DT` | Birth / death dates (`DDMMMYYYY`). |
| `SEX_IDENT_CD`, `BENE_RACE_CD`, `RTI_RACE_CD` | Demographic codes. |
| `BENE_ENROLLMT_REF_YR` | Enrollment reference year. |
| `AGE_AT_END_REF_YR` | Age at end of reference year. |
| `ENTLMT_RSN_ORIG`, `ENTLMT_RSN_CURR`, `ESRD_IND` | Entitlement reason / ESRD. |
| `BENE_HI_CVRAGE_TOT_MONS`, `BENE_SMI_CVRAGE_TOT_MONS` | Part A / Part B coverage months. |
| `BENE_HMO_CVRAGE_TOT_MONS`, `PTD_PLAN_CVRG_MONS` | HMO / Part D coverage months. |
| `MDCR_STATUS_CODE_01..12`, `MDCR_ENTLMT_BUYIN_IND_01..12` | Monthly status / buy-in arrays. |
| `DUAL_STUS_CD_01..12`, `DUAL_ELGBL_MONS` | Monthly dual-eligibility status / months. |
| `PTC_*`, `PTD_*` (`_01..12`) | Monthly Part C/D contract, plan, segment ids. |

(185 columns total; monthly arrays `_01..12` carry the per-month values.)

### `inpatient.csv` — Inpatient FFS claims
Grain: claim line (claim header repeats across revenue-center lines).
Keys: `CLM_ID` (claim), `CLM_ID` + `CLM_LINE_NUM` (line). FK: `BENE_ID`.

| Column | Meaning |
|---|---|
| `BENE_ID` | Synthetic beneficiary (FK to enrollment). |
| `CLM_ID` | Synthetic claim id. |
| `NCH_CLM_TYPE_CD` | NCH claim type. |
| `CLM_FROM_DT`, `CLM_THRU_DT` | Service span (from ≤ thru). |
| `CLM_ADMSN_DT`, `NCH_BENE_DSCHRG_DT` | Admission / discharge dates. |
| `NCH_WKLY_PROC_DT`, `FI_CLM_PROC_DT` | NCH weekly / FI processing (adjudication) dates. |
| `PRVDR_NUM` | Synthetic provider (CCN-shaped) number. |
| `PRVDR_STATE_CD` | Provider state code. |
| `ORG_NPI_NUM`, `AT_PHYSN_NPI`, `OP_PHYSN_NPI`, `OT_PHYSN_NPI` | Synthetic organizational / physician NPIs. |
| `CLM_PMT_AMT` | Medicare claim payment amount. |
| `CLM_TOT_CHRG_AMT` | Total submitted charges. |
| `NCH_PRMRY_PYR_CLM_PD_AMT` | Primary-payer paid amount. |
| `NCH_IP_NCVRD_CHRG_AMT`, `NCH_BENE_IP_DDCTBL_AMT` | Non-covered / deductible amounts. |
| `CLM_DRG_CD` | MS-DRG code. |
| `ADMTG_DGNS_CD`, `PRNCPAL_DGNS_CD` | Admitting / principal diagnosis (ICD-10-CM). |
| `ICD_DGNS_CD1..25` + `CLM_POA_IND_SW1..25` | Diagnosis codes + present-on-admission switches. |
| `ICD_PRCDR_CD1..25` + `PRCDR_DT1..25` | Procedure codes (ICD-10-PCS) + dates. |
| `CLM_UTLZTN_DAY_CNT` | Covered utilization days. |
| `PTNT_DSCHRG_STUS_CD` | Patient discharge status. |
| `CLM_LINE_NUM`, `REV_CNTR`, `HCPCS_CD` | Revenue-center line: line number, revenue code, HCPCS. |

> Note: the synthetic RIF has service and processing dates but no explicit
> submission/payment timeline. Submission → adjudication → payment timing and
> denial/appeal fields are added by the SIMULATED layer (Phase 2), so the
> date-ordering contract (service ≤ submission ≤ adjudication ≤ payment) is
> enforced against `sim_` fields there; the SOURCE contract enforces
> `CLM_FROM_DT ≤ CLM_THRU_DT`.

## Raw layer — NPPES provider extract (REFERENCE, vintage 2026-07)

### `nppes_ri_extract.csv`
State-filtered (Rhode Island) subset of the NPPES monthly dissemination main
file (`npidata_pfile_*`). Comma-delimited, fields quoted. Standard ~330-column
NPPES layout. Key: `NPI`. Filter column: `Provider Business Practice Location
Address State Name`. Real NPIs — linked to synthetic claims only via the
SIMULATED crosswalk.

## Raw layer — CMS Hospital General Information (REFERENCE, vintage 2026-04)

### `reference/hospital_general_information.csv`
CMS Hospital General Information (dataset `xubh-q36u`). Comma-delimited, quoted.
5,432 real facilities across 56 states/territories. Key columns for the
SIMULATED crosswalk: `Facility ID` (CCN), `State`, `Hospital Type` (stratifiers).
Real CCNs — linked to synthetic claims only via the seeded crosswalk (§3.4).

## Warehouse layer — star schema (`rcm`, PostgreSQL 16)

Built by `src/ingestion/star_transform.py` + `sql/ddl/` (`make warehouse` loads
live PostgreSQL 16; `make validate-warehouse` runs the acceptance checks against
it; `make warehouse-check` is the DuckDB CI mirror of the same check SQL). Every
dimension reserves surrogate key
`0` for an **Unknown** member so facts never carry null foreign keys. Synthetic
provider ids are flagged `is_synthetic_id = true` — they are NOT real CCNs/NPIs.

Dimensions:

| Table | Grain | Natural key | Notes |
|---|---|---|---|
| `dim_date` | one calendar day | `date_key` (yyyymmdd) | key 0 = Unknown/undated; keys are date-ordered ints. |
| `dim_beneficiary` | one beneficiary | `bene_id` | demographics + coverage months; SOURCE. |
| `dim_provider` | one billing provider | `prvdr_num` | synthetic CCN/NPI; `is_synthetic_id`. |
| `dim_drg` | one MS-DRG code | `drg_cd` | `drg_desc` enriched (REFERENCE) from `ref_msdrg` (MS-DRG v40, FY2023); see reference code sets below. |
| `dim_discharge_status` | one status code | `discharge_status_cd` | SOURCE. |

Facts:

| Table | Grain | Key | Notes |
|---|---|---|---|
| `fact_inpatient_claim` | one claim (`CLM_ID`) | `claim_sk` | claim-header measures (payment/charges constant per claim); FKs to all dims; `length_of_stay_days` is DERIVED; CHECK constraints enforce non-negative money and `from_date_key ≤ thru_date_key`. |
| `fact_claim_revenue_line` | one revenue line (`CLM_ID`+`CLM_LINE_NUM`) | `claim_line_sk` | rev code / HCPCS; FK to claim. |
| `fact_claim_diagnosis` | one (claim, diagnosis slot) | `claim_dgns_sk` | long form of `ICD_DGNS_CD1..25` + POA; only non-empty codes. |

Loaded counts (this subset, reconciled to source): 20,867 claims / 58,066
revenue lines / 338,024 diagnoses; dims 9,660 beneficiaries, 4,876 providers,
167 DRGs. 910 claims have a null billing provider and 2,741 a null DRG — both
routed to the Unknown member (reported as data-quality metrics, not errors).

SIMULATED crosswalk tables (CLAUDE.md §3.4 — seeded random assignment, NOT a
real linkage; seed in `config/simulation.yaml:linkage.crosswalk_seed`):

Per CLAUDE.md §3.2, both tables are SIMULATED so **every** column carries the
`sim_` prefix (enforced by `tests/contracts/test_crosswalk.py` and
`tests/integration/test_crosswalk_prefix_postgres.py`).

| Table | Grain | Maps | Stratified by |
|---|---|---|---|
| `sim_facility_crosswalk` | one synthetic billing provider (`sim_prvdr_num`, FK to `dim_provider`) | → real facility CCN (`sim_facility_ccn`, Hospital General Information; plus `sim_facility_name`/`sim_facility_state`/`sim_facility_type`) | state (SSA→postal, `sim_provider_ssa_state`/`sim_provider_postal_state`) + acute-care type |
| `sim_provider_crosswalk` | one synthetic attending physician (`sim_at_physn_npi`) | → real Medicare NPI (`sim_real_npi`, Medicare Physician by Provider; plus `sim_real_provider_state`/`sim_real_specialty`) | coherent state (`sim_assigned_postal_state`) + inpatient-plausible specialty |

Each row carries `sim_match_rule`, `sim_same_state`, `sim_crosswalk_seed`, and
`sim_provenance='SIMULATED'`. On this subset: 4,876 facility rows and 2,463
provider rows, 100% same-state matches. Same seed reproduces an identical
crosswalk.

REFERENCE code-set tables (`sql/ddl/60_reference_codes.sql`, loaded by
`src/ingestion/reference_codes.py`). Vintage matches the 2023-04 claims period
(CLAUDE.md §2 — FY2023, never ICD-9). Loaded ADDITIVELY: the loader applies only
the create-if-not-exists DDL and enriches `dim_drg.drg_desc`, never dropping or
reloading `fact_*` / `sim_*`. Give SOURCE claim codes human-readable names:

| Table | Grain | Key | Columns | Rows | Provenance |
|---|---|---|---|---|---|
| `ref_icd10cm` | one ICD-10-CM code | `icd10cm_code` (dotless) | `long_desc` | 73,674 | REFERENCE (FY2023) |
| `ref_icd10pcs` | one ICD-10-PCS code | `icd10pcs_code` (7-char) | `long_desc` | 78,530 | REFERENCE (FY2023) |
| `ref_hcpcs` | one HCPCS Level II code | `hcpcs_code` | `long_desc`, `short_desc` | 7,404 | REFERENCE (2023) |
| `ref_msdrg` | one MS-DRG (v40) | `drg_cd` (3-digit) | `drg_title`, `mdc`, `drg_type` | 767 | REFERENCE (FY2023) |
| `ref_carc` | one CARC code | `carc_code` | `category_label` | 10 | code=REFERENCE; label=DERIVED |

§3.7 boundaries enforced at load: `ref_hcpcs` keeps **Level II only** (letter +
4 digits) — CPT Level I (5-digit numeric, AMA-licensed), 2-character modifiers,
and the D-series (dental CDT, ADA-copyright) are dropped. `ref_carc` reproduces
**no X12 description text**: `carc_code` holds public code identifiers used as
category labels only, and `category_label` is a project-authored short label
(kept in sync with `config/simulation.yaml` denial categories). Join
`sim_denial_carc_group = ref_carc.carc_code` for the denial-category name.

`dim_drg.drg_desc` enrichment: `update dim_drg set drg_desc = ref_msdrg.drg_title
where drg_cd = drg_cd`; 167/167 real DRGs in this subset matched, enriched rows
flip `provenance` to `REFERENCE`. Re-run after any full `make warehouse` reload
(which recreates `dim_drg` with a null `drg_desc`).

## Raw layer — Medicare Physician & Other Practitioners by Provider (REFERENCE, vintage 2024)

### `reference/medicare_providers_extract.csv`
Compact column extract (NPI, entity code, state, specialty, name) of the CMS
"Medicare Physician & Other Practitioners - by Provider" dataset (D24 release).
1,296,739 real providers, nationwide. Provider pool for the SIMULATED
`sim_provider_crosswalk`. Real NPIs — linked to synthetic claims only via that
crosswalk. (The full ~485 MB source is checksummed then deleted; only the
extract is retained.)

## Simulated adjudication layer (`sim_*`, SIMULATED — Phase 2)

Generated by `src/simulation/` from `config/simulation.yaml`
(`make simulate` writes Parquet to `data/simulated/`; `make simulate-warehouse`
loads PostgreSQL; `make simulate-check` is the DuckDB CI mirror of the same
check SQL). DDL: `sql/ddl/50_sim_adjudication.sql`.

**Everything here is invented.** The CMS synthetic claims contain no denials, no
submission or adjudication dates, no appeals and no workflow events. Nothing in
these tables represents real payer behaviour. Calibration anchors and their
published sources: `docs/assumptions.md`. Which columns may be used as model
features: `docs/simulated_forbidden_columns.md`.

Every column carries the `sim_` prefix except `claim_sk` (DERIVED warehouse
surrogate key) and `clm_id` (SOURCE degenerate key), which are join keys this
layer did not invent. Every row also carries `sim_provenance = 'SIMULATED'`,
`sim_config_version` and `sim_seed`.

| Table | Grain | Key | Notes |
|---|---|---|---|
| `sim_payer` | one simulated payer archetype | `sim_payer_id` | 5 invented archetypes (Medicare FFS, Medicare Advantage, two commercial, Medicaid MCO) with mix share and contractual filing limit. Not modelled on any real insurer (CLAUDE.md §3.5). |
| `sim_service_line` | one service-line bucket | `sim_service_line_id` | 10 contiguous MS-DRG numeric ranges + an UNKNOWN member for claims with no DRG. Bucket boundaries are this project's design choice, not a CMS grouping. |
| `sim_authorization_eligibility` | one claim | `claim_sk` | PRE-SUBMISSION: prior-auth required/obtained/missing/late, eligibility checked/failed, secondary payer present, with dates. Legitimate Model A features. |
| `sim_documentation_coding` | one claim | `claim_sk` | PRE-SUBMISSION: documentation completeness flag + score, coder query outstanding, coding specificity deficit, complexity score, duplicate-submission flag. Legitimate Model A features. |
| `sim_claim_adjudication` | one claim | `claim_sk` | The hub. Payer + service line, the full generated timeline (coded → submitted → acknowledged → adjudicated → paid), simulated money, denial outcome with a CARC-labelled category, and the latent generator internals `sim_latent_p` / `sim_provider_quality_latent` (VALIDATION ONLY). Mostly post-submission — see the forbidden-columns doc. |
| `sim_appeals` | one (claim, appeal level) | `sim_appeal_sk` | Denied claims that get worked. Level 1 reconsideration, level 2 only when level 1 was upheld and the balance clears the configured floor. Outcome, disputed and recovered amounts, `sim_appeal_latent_p` (VALIDATION ONLY). |
| `sim_workflow_events` | one (claim, event occurrence) | `sim_event_sk` | Process-mining event log: 11 event types from `CODING_COMPLETE` to `CLAIM_CLOSED`, sequenced by generated timestamp with actor role and touch minutes. |
| `sim_operating_costs` | one claim | `claim_sk` | Cost to collect, accumulated bottom-up from the touch minutes in the event log (labour rate × minutes × overhead, plus flat per-event fees), split into coding / submission / payment posting / denial rework / appeal. |

Money invariant, enforced by CHECK constraints plus the validation suite:
`sim_paid_amount ≤ sim_allowed_amount ≤ fact_inpatient_claim.clm_tot_chrg_amt`,
and `sim_allowed_amount = sim_paid_amount + sim_patient_responsibility_amount +
sim_denied_amount`. There is deliberately **no** `sim_billed_amount`: billed
charges are a SOURCE value and stay in the SOURCE fact table, reached by join.

Generated counts on this subset (config v0.3.0, seed 42): 20,867 adjudicated
claims, 998 appeal rows, 131,077 workflow events, 20,867 cost rows; realized
denial rate 12.8% (target band 10–18%), 36.3% of denials appealed, 48.0% of
appeals overturned.

**Load ordering.** `make warehouse` drops `fact_inpatient_claim` with CASCADE,
which drops the foreign keys these tables hold into it. The correct sequence is
always `make warehouse` → `make simulate` → `make simulate-warehouse`; the
loader refuses to run against a star schema whose `claim_sk` set does not match
the one the generator saw.

## Analytics KPI views (`vw_*`, DERIVED analytics layer — Phase 3)

Read-only SQL views in `sql/views/`, applied idempotently by
`sql/views/apply_views.py` (`make views`, which then runs the reconciliation
gate `sql/quality/view_reconciliation.py`, 21 checks). They compute nothing new
about the world: every view is a DERIVED reprojection of the warehouse fact/dim
tables and the SIMULATED `sim_*` layer, so a view's honesty is inherited from
its inputs — anything sourced from `sim_*` stays SIMULATED downstream, and the
payer dimension stays 100% simulated (§3.5). **Per-column provenance is stated
authoritatively in each view's SQL header block; that header is the source of
truth this table summarizes.** No view keys on `sim_facility_ccn`/`sim_facility_name`
(display-only, §3.2 crosswalk ruling); facility/provider grain keys on the
synthetic `prvdr_num`.

**View OUTPUT columns keep the `sim_` prefix (§3.2, team-lead ruling 2026-07-27).**
The simulated-linkage facility columns are exposed by `vw_claim_enriched` as
`sim_facility_ccn` / `sim_facility_name` / `sim_facility_state` /
`sim_facility_type` — no alias back to a bare name — and re-exported by
`vw_clean_claim_performance` as `sim_display_facility_ccn` / `_name` / `_state`
and by `vw_work_queue_priority` as `sim_facility_name`. `vw_claim_enriched` is
the flattened matrix the Phase 4 feature store reads and the §4 leakage blacklist
is column-name based, so an unprefixed simulated column arriving there would lose
its provenance marker at exactly the point that marker is load-bearing.

`vw_claim_enriched` is the shared base (all others read from it), so the join
logic and provenance live in one place.

The derived adjudication flags and A/R balance on that base retain simulated
provenance in their names: `sim_adjudicated`, `sim_clean_claim_flag`,
`sim_first_pass_paid_flag`, `sim_ar_open_flag`, and `sim_ar_balance_amt`.
`vw_work_queue_priority` likewise exposes `sim_action_type`,
`sim_dollars_at_stake`, `sim_heuristic_priority_score`, `sim_priority_tier`, and
`sim_appeal_levels`. They are computed from simulated outcomes, money, and appeal
history; queue membership itself is restricted to simulated denials or open A/R.

| View | Grain | Provenance summary |
|---|---|---|
| `vw_claim_enriched` | one inpatient claim (`claim_sk`), 20,867 rows | MIXED, labeled per column in-header: SOURCE (CMS RIF fields, incl. real billed charge + the one real Medicare paid amount), DERIVED (length-of-stay, flags), REFERENCE (`drg_desc` and code descriptions, display-only), SIMULATED (all `sim_*` adjudication/timeline/money **and the `sim_facility_*` linkage columns, display-only, prefix preserved on output**). |
| `vw_executive_rcm_summary` | one submission month (`YYYY-MM`) | MIXED: billed + Medicare-paid = SOURCE; allowed/paid/denied amounts + denial/clean/first-pass rates = DERIVED from SIMULATED. |
| `vw_denial_root_cause` | (denial_category, CARC group, driver_mechanism), denied only | SIMULATED throughout. CARC group used as a LABEL only (§3.7), not from any AMA/CMS description file. |
| `vw_ar_aging` | one AR aging bucket (0-30…120+) | DERIVED from SIMULATED timeline + money; "open" = no simulated payment posted. |
| `vw_payer_performance` | one simulated payer (`sim_payer_id`), 5 rows | **100% SIMULATED (§3.5)** — payer dimension is invented; every dashboard/export on this view MUST carry the simulated-data banner. |
| `vw_clean_claim_performance` | one SYNTHETIC billing provider (`prvdr_num`), ~4,877 rows | DERIVED from SIMULATED; keyed on synthetic `prvdr_num` (mandatory), `sim_display_facility_ccn`/`_name`/`_state` display-only. |
| `vw_work_queue_priority` | one actionable claim (`claim_sk`) | HEURISTIC PLACEHOLDER, not a model — `sim_heuristic_priority_score` and `sim_priority_tier`, `is_heuristic_placeholder` always true; Phase 4 Model A/C replace it. `sim_facility_name` display-only. |
| `vw_data_quality_scorecard` | one named DQ check (`check_id`) | DERIVED data-quality metadata; each row also carries the provenance class of the data under test. |
| `vw_model_monitoring` | (submission_month, feature_name) | DRIFT SCAFFOLD, no model exists — `is_drift_scaffold` always true; observed input distributions only, no score/prediction/probability. |

EDA notebooks (`notebooks/01`–`06`, `make`-independent, re-runnable top-to-bottom
against live PG via `notebooks/analytics_common.py`) read these views + the
`sim_*` tables and print numbered insights; every notebook renders the SIMULATED
banner and frames findings as review signals, never fraud. Notebook 06
(interrupted time series) is illustrative-only and writes nothing to the
warehouse.

## Model A training matrix (committed Parquet — Phase 4)

`artifacts/features/model_a_training_matrix.parquet` + sidecar
`model_a_training_matrix.json`. Built by `src/features/` (`make features`);
`make train` rewrites it from the same code path as a side effect of fitting, so
the guard always checks the object the model actually saw.

**This is one of exactly two data files committed to git in this repository**, the
other being the Phase 5 hosted-demo bundle
`dashboard/demo_data/rcm_demo.duckdb` (registered at the end of this file). Until
Phase 5 this section read "the only data file committed to git", and that sentence
became false the moment the bundle landed — a stale exclusivity claim in a
provenance document, corrected here rather than left to age. Everything else under
`data/` and `models_artifacts/` is gitignored. A reader with a clean clone and no
database can open these two files and nothing else, which is why this one's
columns are classified in full in `docs/provenance_register.md` ("The committed
Model A training matrix") rather than only summarized here.

| Property | Value |
|---|---|
| Grain | one row per claim |
| Rows × columns | 20,867 × 44 |
| Composition | 39 features + 4 passthrough + `split` |
| Label | `sim_denial_flag` (SIMULATED; forbidden as a feature) |
| Time column | `sim_submission_date` (SIMULATED) |
| Split | temporal, cut `2021-12-28` — 16,694 train / 4,173 test |
| Row order | `sim_submission_date`, `claim_sk` |
| Regenerate | `make features` |

Classification summary — the authoritative per-column breakdown is in the
provenance register:

| Class | Count | Columns |
|---|---|---|
| SIMULATED | 34 | every `sim_`-prefixed column: 32 features + the label `sim_denial_flag` + `sim_submission_date` |
| SOURCE | 4 | `billed_charge_amt` (= `clm_tot_chrg_amt`, renamed only), `drg_cd`, `provider_state_cd`, `prvdr_num` |
| DERIVED | 6 | `length_of_stay_days`, `diagnosis_count`, `log_billed_charge_amt`, `patient_age_years`, `claim_sk`, `split` |

34 + 4 + 6 = 44, no column unclassified.

**Nothing in this file is real.** CMS *synthetic* claim facts plus this project's
own simulated adjudication inputs; no real patient, provider, payer or
adjudication data. No crosswalked real CCN or NPI is present — the provider
column is the synthetic `prvdr_num` (§3.4). Column names follow §3.2: a feature
computed from a simulated input keeps the `sim_` prefix through the engineering
step, so `sim_overall_prior_denial_rate` is a rate over *fabricated* denials, not
a Medicare book rate. The single exception is `split`, a modelling fold label
whose name is fixed by the §4.1 leakage guard's discovery contract; both
decisions are recorded in the register.

The sidecar manifest carries rows, feature list, split boundary, parquet SHA-256
and a digest of every `forbidden_*` block in `config/model.yaml`. It contains **no
timestamp**: every field derives from content or config, so every writer produces
byte-identical bytes and any diff on this artifact means the data changed.

## Hosted-demo bundle (committed DuckDB — Phase 5)

`dashboard/demo_data/rcm_demo.duckdb`, 8,400,896 bytes (8.0 MB), **16 tables /
71,813 rows**. Built by `src/demo/build.py` (`make demo-extract`) from the
PostgreSQL warehouse plus the model artifacts; committed via the
`!dashboard/demo_data/*.duckdb` exception in `.gitignore`. CLAUDE.md §2 locks the
hosted demo to a bundled extract, so this file is what the deployed Streamlit app
reads — no database, no network, no credentials.

The authoritative per-table classification is in `docs/provenance_register.md`
("The committed hosted-demo bundle"). This section is the column-level reader's
guide.

| Property | Value |
|---|---|
| Tables | 16 — 9 curated-view copies, 5 model outputs, 2 meta |
| Rows | 71,813 |
| Declaration | `src/demo/spec.py` (build fails on an undeclared or a missing table) |
| Opened | read-only; one DuckDB cursor per thread |
| Self-description | `demo_manifest` (14 rows) and `demo_build_info` (1 row) |
| Regenerate | `make demo-extract` |

### The nine curated-view tables

Copied `select * from rcm.vw_...` **verbatim** — no recomputation, so column names,
types and grain are exactly those documented under "Analytics KPI views (`vw_*`)"
above. Nothing needs restating here; that is the point of copying rather than
recomputing.

| Table | Grain | Rows | Cols |
|---|---|---|---|
| `vw_claim_enriched` | one inpatient claim (`claim_sk`) | 20,867 | 77 |
| `vw_executive_rcm_summary` | one claim-submission month | 109 | 24 |
| `vw_denial_root_cause` | (denial category, CARC group, driver) | 34 | 16 |
| `vw_ar_aging` | one A/R aging bucket (5-bucket spine) | 5 | 10 |
| `vw_payer_performance` | one **simulated** payer archetype | 5 | 22 |
| `vw_clean_claim_performance` | one **synthetic** billing provider (`prvdr_num`) | 4,877 | 18 |
| `vw_work_queue_priority` | one actionable claim (denied or open A/R) | 2,663 | 16 |
| `vw_data_quality_scorecard` | one named data-quality check | 15 | 10 |
| `vw_model_monitoring` | (submission month, monitored feature) | 981 | 6 |

### The five model-output tables

These exist only in the bundle — a training run produced them, no warehouse view
holds them.

| Table | Grain | Rows | Cols | Notes |
|---|---|---|---|---|
| `model_a_scores` | one claim | 20,867 | 7 | `fold` ∈ {fit, calibrate, test}. **16,694 rows are IN-SAMPLE**; every performance figure on the dashboard restricts to `fold == 'test'` (4,173 rows) and says so. `sim_denial_flag` is carried for evaluation only and is never a feature. |
| `model_a_reason_codes` | (claim, contributing feature), test fold, top drivers | 20,865 | 7 | Per-claim SHAP folded back to declared features and mapped to project-authored reason codes / analyst actions. ~33% of simulated denials are label noise with no mechanism, so a decomposition is not a causal account. |
| `model_a_shap_global` | one declared feature | 39 | 5 | Global SHAP importance, summed over each feature's encoded columns. |
| `model_c_work_queue` | one denial per queue snapshot | 469 | 13 | `queue_mode` separates `backtest` (468 rows) from `live_snapshot` (1 row — degenerate, and shipped rather than dropped because the degeneracy is a true fact about the data). `tier`, `tier_rank`, `queue_position` and `recommended_action` carry no `sim_` marker because they describe **our process**, not the simulated world. |
| `model_metrics` | one model (A, C) | 2 | 4 | Each run's `metrics.json` verbatim, so the dashboard reports what the run reported. Every metric scores a SIMULATED label — `contains_simulated` is `true` despite there being no `sim_` column. |

### The two self-describing tables

| Table | Columns | Notes |
|---|---|---|
| `demo_manifest` | `dataset`, `provenance`, `contains_simulated`, `grain`, `rows`, `columns`, `simulated_columns`, `note` | The bundle's own register, rendered on the dashboard's Model & data quality page. **14 rows, not 16** — it omits itself and `demo_build_info` to stop the self-reference. |
| `demo_build_info` | `git_commit`, `git_branch`, `git_tree_dirty`, `built_at_utc`, `source_vintages`, `dataset_names`, `contains_simulated`, `notice` | The build stamp ([SHA-STAMP] applied to this artifact). The shipped bundle carries `git_tree_dirty = true`, and the dashboard displays "built from an UNCOMMITTED working tree" rather than showing a commit and implying reproducibility. |

### Reading the columns

`contains_simulated` is **declared, not inferred from spellings.** `vw_model_monitoring`
and `model_metrics` hold no `sim_`-prefixed column and are both simulated in
substance — drift in the first is drift in the simulation, and every metric in the
second scores a simulated label. A rule that read column names would call both
clean, which is why §3.2's marker identifies simulated *columns* while the *table*
flag is asserted by `src/demo/spec.py`.

Within a table, the §3.2 marker is a **prefix** test and not a substring one:
`medicare_source_paid_amt` in `vw_executive_rcm_summary` is SOURCE — the one real
Medicare payer's paid amount — and must never be reported as simulated.

**Nothing downstream of submission is real**: every denial, appeal, payment,
payment date, workflow event, cost and the whole five-payer dimension is generated
here (§3.5 — Medicare FFS has one payer). Real facility and provider names are
display-only and forbidden as a model feature (§3.4); 4,876 synthetic providers map
onto 2,857 real CCNs carrying only 2,816 distinct names, so collisions run to 8:1
by CCN and **15:1 by name**, and every provider-level table is keyed on the
synthetic `prvdr_num`. The claims are vintage 2023-04 while the facility reference
is 2026-04 and the provider reference is data year 2024 — roughly three years
newer than the claims they decorate.
