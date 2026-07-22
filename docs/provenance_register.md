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
| `nppes/nppes_ri_extract.csv` | nppes_npi | REFERENCE | 2026-07 | Rhode Island state-filtered NPPES provider extract (comma-delimited, quoted). |

Provenance rules enforced here:
- Synthetic claims carry **synthetic** provider/facility identifiers
  (`PRVDR_NUM`, `ORG_NPI_NUM`, `AT_PHYSN_NPI`). These do **not** join to the
  real NPPES NPIs or real hospital CCNs. Any link between synthetic claims and
  the NPPES/Hospital reference data is created **only** by the seeded, stratified
  simulated crosswalk (CLAUDE.md §3.4), which is classified `SIMULATED`.
- No raw source is ever classified `SIMULATED`; no downloaded reference file is
  presented as claims-linked truth.

## Warehouse tables and columns

Populated with the DDL task (Phase 1 item 3). The simulated crosswalk table and
all `sim_`-prefixed tables are added here by their owning agents.

| Table | Column | Classification | Source / Generator | Notes |
|---|---|---|---|---|
| _(pending DDL)_ | | | | |
