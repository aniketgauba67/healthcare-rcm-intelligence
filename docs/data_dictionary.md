# Data Dictionary

Covers the raw ingestion layer (`data/raw/`) and the typed validated layer
(`data/validated/`). Warehouse table dictionaries are added by the DDL task.

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
| `dim_drg` | one MS-DRG code | `drg_cd` | `drg_desc` null until MS-DRG REFERENCE loaded. |
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
