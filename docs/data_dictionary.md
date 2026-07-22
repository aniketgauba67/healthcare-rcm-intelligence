# Data Dictionary

Covers the raw ingestion layer (`data/raw/`). Typed Parquet (validated layer)
and warehouse table dictionaries are added by their respective Phase 1 tasks.

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

(158 columns total; monthly arrays `_01..12` carry the per-month values.)

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
