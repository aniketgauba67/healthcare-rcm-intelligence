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
| dim_drg | `drg_cd` | SOURCE | inpatient | `drg_desc` will be REFERENCE (MS-DRG file); `drg_key` DERIVED. |
| dim_discharge_status | `discharge_status_cd` | SOURCE | inpatient | `discharge_status_key` DERIVED. |
| fact_inpatient_claim | measures, degenerate `clm_id`, diagnosis codes | SOURCE | inpatient | surrogate/FK keys DERIVED; `length_of_stay_days` DERIVED. |
| fact_claim_revenue_line | `clm_line_num`, `rev_cntr`, `hcpcs_cd` | SOURCE | inpatient | surrogate/FK keys DERIVED. |
| fact_claim_diagnosis | `dgns_seq`, `icd_dgns_cd`, `poa_ind_sw` | SOURCE | inpatient | unpivot of ICD_DGNS_CD1..25; keys DERIVED. |
| **sim_facility_crosswalk** | all | **SIMULATED** | seeded assignment | synthetic billing provider (`sim_prvdr_num`, FK to dim_provider) → REAL facility CCN, stratified by state+type. Not a real linkage. |
| **sim_provider_crosswalk** | all | **SIMULATED** | seeded assignment | synthetic attending physician (`sim_at_physn_npi`) → REAL Medicare NPI, stratified by coherent state + inpatient-plausible specialty. Not a real linkage. |
| dq_quarantine | all | DERIVED | contract engine | one row per data-contract violation (table, contract, entity key, reason). No SOURCE values beyond the offending key. |

The `sim_*_crosswalk` tables are the ONLY link between synthetic claims and real
CCNs/NPIs, and every row is a seeded random assignment (seed
`config/simulation.yaml:linkage.crosswalk_seed`), classified SIMULATED — never
presented as a real correspondence (CLAUDE.md §3.4). All non-`sim_` warehouse
columns remain SOURCE/DERIVED.

Reproducibility guarantee: the crosswalk is byte-identical for the same
`crosswalk_seed` **and** the same reference vintages. The facility/provider
reference vintages are pinned in `config/sources.yaml` (`hospital_general_information`
2026-04, `medicare_providers` 2024); re-pulling a reference at a new vintage will
change the assignment. The crosswalk integrity checks (FK, provenance, counts)
run identically against live Postgres and the DuckDB CI mirror.
