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
| **sim_facility_crosswalk** | all | **SIMULATED** | seeded assignment | synthetic billing provider (`sim_prvdr_num`, FK to dim_provider) → REAL facility CCN, stratified by state+type. Not a real linkage. |
| **sim_provider_crosswalk** | all | **SIMULATED** | seeded assignment | synthetic attending physician (`sim_at_physn_npi`) → REAL Medicare NPI, stratified by coherent state + inpatient-plausible specialty. Not a real linkage. |
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
