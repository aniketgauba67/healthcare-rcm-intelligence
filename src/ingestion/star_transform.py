"""Transform the validated Parquet layer into star-schema frames.

Engine-agnostic: produces the dimension and fact DataFrames (with deterministic
surrogate keys and Unknown members) that `load_postgres` bulk-loads, and that
the offline DuckDB harness validates. Keeping this pure and separate from the
database is what lets FK integrity and reconciliation be checked without a live
Postgres instance.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .paths import DATA_VALIDATED

_DIAG_SLOTS = range(1, 26)  # ICD_DGNS_CD1..25 / CLM_POA_IND_SW1..25


@dataclass
class StarFrames:
    """The full set of warehouse frames plus a reconciliation report."""

    dims: dict[str, pd.DataFrame]
    facts: dict[str, pd.DataFrame]
    reconciliation: dict[str, object] = field(default_factory=dict)


def _date_key(d: object) -> int:
    """yyyymmdd integer key for a date; 0 (Unknown) for null/NaT."""
    if d is None or pd.isna(d):
        return 0
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return d.year * 10000 + d.month * 100 + d.day


def _build_dim_date(date_series: list[pd.Series]) -> pd.DataFrame:
    """Build dim_date from every date column feeding the facts (+ Unknown)."""
    values: set[dt.date] = set()
    for s in date_series:
        for v in s.dropna().unique():
            values.add(v if isinstance(v, dt.date) else pd.Timestamp(v).date())

    rows = [
        {
            "date_key": 0,
            "full_date": None,
            "year": None,
            "quarter": None,
            "month": None,
            "month_name": None,
            "day": None,
            "day_of_week": None,
            "day_name": None,
            "is_weekend": None,
        }
    ]
    for d in sorted(values):
        rows.append(
            {
                "date_key": _date_key(d),
                "full_date": d,
                "year": d.year,
                "quarter": (d.month - 1) // 3 + 1,
                "month": d.month,
                "month_name": d.strftime("%B"),
                "day": d.day,
                "day_of_week": d.weekday(),
                "day_name": d.strftime("%A"),
                "is_weekend": d.weekday() >= 5,
            }
        )
    return pd.DataFrame(rows)


def _build_dim_beneficiary(bene: pd.DataFrame) -> pd.DataFrame:
    src = bene.rename(
        columns={
            "BENE_ID": "bene_id",
            "BENE_BIRTH_DT": "birth_date",
            "BENE_DEATH_DT": "death_date",
            "SEX_IDENT_CD": "sex_ident_cd",
            "BENE_RACE_CD": "race_cd",
            "RTI_RACE_CD": "rti_race_cd",
            "STATE_CODE": "state_code",
            "COUNTY_CD": "county_cd",
            "ZIP_CD": "zip_cd",
            "AGE_AT_END_REF_YR": "age_at_end_ref_yr",
            "BENE_ENROLLMT_REF_YR": "enrollmt_ref_yr",
            "BENE_HI_CVRAGE_TOT_MONS": "part_a_cvrg_mons",
            "BENE_SMI_CVRAGE_TOT_MONS": "part_b_cvrg_mons",
            "BENE_HMO_CVRAGE_TOT_MONS": "hmo_cvrg_mons",
            "PTD_PLAN_CVRG_MONS": "ptd_cvrg_mons",
        }
    )
    cols = [
        "bene_id",
        "birth_date",
        "death_date",
        "sex_ident_cd",
        "race_cd",
        "rti_race_cd",
        "state_code",
        "county_cd",
        "zip_cd",
        "age_at_end_ref_yr",
        "enrollmt_ref_yr",
        "part_a_cvrg_mons",
        "part_b_cvrg_mons",
        "hmo_cvrg_mons",
        "ptd_cvrg_mons",
    ]
    src = src[[c for c in cols if c in src.columns]].drop_duplicates("bene_id")
    src = src.sort_values("bene_id").reset_index(drop=True)
    src.insert(0, "bene_key", range(1, len(src) + 1))
    src["provenance"] = "SOURCE"

    unknown = {c: None for c in src.columns}
    unknown.update({"bene_key": 0, "bene_id": "UNKNOWN", "provenance": "DERIVED"})
    return pd.concat([pd.DataFrame([unknown]), src], ignore_index=True)


def _build_dim_provider(ip: pd.DataFrame) -> pd.DataFrame:
    prov = (
        ip[["PRVDR_NUM", "ORG_NPI_NUM", "PRVDR_STATE_CD"]]
        .rename(
            columns={
                "PRVDR_NUM": "prvdr_num",
                "ORG_NPI_NUM": "org_npi_num",
                "PRVDR_STATE_CD": "provider_state_cd",
            }
        )
        .dropna(subset=["prvdr_num"])
        .drop_duplicates("prvdr_num")
        .sort_values("prvdr_num")
        .reset_index(drop=True)
    )
    prov.insert(0, "provider_key", range(1, len(prov) + 1))
    prov["is_synthetic_id"] = True
    prov["provenance"] = "SOURCE"
    unknown = {
        "provider_key": 0,
        "prvdr_num": "UNKNOWN",
        "org_npi_num": None,
        "provider_state_cd": None,
        "is_synthetic_id": True,
        "provenance": "DERIVED",
    }
    return pd.concat([pd.DataFrame([unknown]), prov], ignore_index=True)


def _build_simple_code_dim(
    ip: pd.DataFrame, src_col: str, key_col: str, code_col: str
) -> pd.DataFrame:
    codes = (
        ip[src_col]
        .dropna()
        .loc[lambda s: s.astype(str).str.strip() != ""]
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    dim = pd.DataFrame({code_col: codes})
    dim.insert(0, key_col, range(1, len(dim) + 1))
    dim["provenance"] = "SOURCE"
    unknown = {key_col: 0, code_col: "UNKNOWN", "provenance": "DERIVED"}
    return pd.concat([pd.DataFrame([unknown]), dim], ignore_index=True)


def _los_days(row: pd.Series) -> object:
    f, t = row["CLM_FROM_DT"], row["CLM_THRU_DT"]
    if pd.isna(f) or pd.isna(t):
        return None
    f = f if isinstance(f, dt.date) else pd.Timestamp(f).date()
    t = t if isinstance(t, dt.date) else pd.Timestamp(t).date()
    return (t - f).days + 1


def build_star(
    beneficiary_parquet: Path | None = None,
    inpatient_parquet: Path | None = None,
) -> StarFrames:
    """Build all dimension and fact frames from the validated Parquet layer."""
    bene_path = beneficiary_parquet or DATA_VALIDATED / "beneficiary_2024.parquet"
    ip_path = inpatient_parquet or DATA_VALIDATED / "inpatient.parquet"
    bene = pd.read_parquet(bene_path)
    ip = pd.read_parquet(ip_path)

    dim_bene = _build_dim_beneficiary(bene)
    dim_provider = _build_dim_provider(ip)
    dim_drg = _build_simple_code_dim(ip, "CLM_DRG_CD", "drg_key", "drg_cd")
    dim_dstat = _build_simple_code_dim(
        ip, "PTNT_DSCHRG_STUS_CD", "discharge_status_key", "discharge_status_cd"
    )
    dim_date = _build_dim_date(
        [ip["CLM_FROM_DT"], ip["CLM_THRU_DT"], ip["CLM_ADMSN_DT"], ip["NCH_BENE_DSCHRG_DT"]]
    )

    # Natural-key -> surrogate-key lookups (default to Unknown = 0).
    bene_map = dict(zip(dim_bene["bene_id"], dim_bene["bene_key"]))
    prov_map = dict(zip(dim_provider["prvdr_num"], dim_provider["provider_key"]))
    drg_map = dict(zip(dim_drg["drg_cd"], dim_drg["drg_key"]))
    dstat_map = dict(zip(dim_dstat["discharge_status_cd"], dim_dstat["discharge_status_key"]))

    def norm(v: object) -> object:
        return None if (pd.isna(v) or str(v).strip() == "") else v

    # ---- fact_inpatient_claim (header grain: one row per CLM_ID) ----
    hdr = ip.drop_duplicates("CLM_ID").reset_index(drop=True)
    fic = pd.DataFrame(
        {
            "claim_sk": range(1, len(hdr) + 1),
            "clm_id": hdr["CLM_ID"],
            "bene_key": hdr["BENE_ID"].map(lambda v: bene_map.get(v, 0)),
            "provider_key": hdr["PRVDR_NUM"].map(lambda v: prov_map.get(v, 0)),
            "drg_key": hdr["CLM_DRG_CD"].map(lambda v: drg_map.get(norm(v), 0)),
            "discharge_status_key": hdr["PTNT_DSCHRG_STUS_CD"].map(
                lambda v: dstat_map.get(norm(v), 0)
            ),
            "from_date_key": hdr["CLM_FROM_DT"].map(_date_key),
            "thru_date_key": hdr["CLM_THRU_DT"].map(_date_key),
            "admission_date_key": hdr["CLM_ADMSN_DT"].map(_date_key),
            "discharge_date_key": hdr["NCH_BENE_DSCHRG_DT"].map(_date_key),
            "nch_clm_type_cd": hdr["NCH_CLM_TYPE_CD"],
            "admtg_dgns_cd": hdr["ADMTG_DGNS_CD"],
            "prncpal_dgns_cd": hdr["PRNCPAL_DGNS_CD"],
            "clm_utlztn_day_cnt": hdr["CLM_UTLZTN_DAY_CNT"],
            "length_of_stay_days": hdr.apply(_los_days, axis=1),
            "clm_pmt_amt": hdr["CLM_PMT_AMT"].round(2),
            "clm_tot_chrg_amt": hdr["CLM_TOT_CHRG_AMT"].round(2),
            "nch_ip_ncvrd_chrg_amt": hdr["NCH_IP_NCVRD_CHRG_AMT"].round(2),
            "nch_bene_ip_ddctbl_amt": hdr["NCH_BENE_IP_DDCTBL_AMT"].round(2),
        }
    )
    claim_sk_map = dict(zip(fic["clm_id"], fic["claim_sk"]))

    # ---- fact_claim_revenue_line (line grain) ----
    fcrl = pd.DataFrame(
        {
            "claim_line_sk": range(1, len(ip) + 1),
            "claim_sk": ip["CLM_ID"].map(claim_sk_map),
            "clm_id": ip["CLM_ID"],
            "clm_line_num": ip["CLM_LINE_NUM"],
            "rev_cntr": ip["REV_CNTR"],
            "hcpcs_cd": ip["HCPCS_CD"],
        }
    )

    # ---- fact_claim_diagnosis (unpivot ICD_DGNS_CD1..25) ----
    diag_rows = []
    for seq in _DIAG_SLOTS:
        code_col, poa_col = f"ICD_DGNS_CD{seq}", f"CLM_POA_IND_SW{seq}"
        if code_col not in hdr.columns:
            continue
        sub = hdr[["CLM_ID", code_col, poa_col]].copy()
        sub = sub[sub[code_col].notna() & (sub[code_col].astype(str).str.strip() != "")]
        if sub.empty:
            continue
        diag_rows.append(
            pd.DataFrame(
                {
                    "clm_id": sub["CLM_ID"],
                    "dgns_seq": seq,
                    "icd_dgns_cd": sub[code_col],
                    "poa_ind_sw": sub[poa_col] if poa_col in sub else None,
                }
            )
        )
    fcd = (
        pd.concat(diag_rows, ignore_index=True)
        if diag_rows
        else pd.DataFrame(columns=["clm_id", "dgns_seq", "icd_dgns_cd", "poa_ind_sw"])
    )
    fcd = fcd.sort_values(["clm_id", "dgns_seq"]).reset_index(drop=True)
    fcd.insert(0, "claim_dgns_sk", range(1, len(fcd) + 1))
    fcd.insert(2, "claim_sk", fcd["clm_id"].map(claim_sk_map))

    dims = {
        "dim_date": dim_date,
        "dim_beneficiary": dim_bene,
        "dim_provider": dim_provider,
        "dim_drg": dim_drg,
        "dim_discharge_status": dim_dstat,
    }
    facts = {
        "fact_inpatient_claim": fic,
        "fact_claim_revenue_line": fcrl,
        "fact_claim_diagnosis": fcd,
    }
    reconciliation = {
        "raw_inpatient_lines": int(len(ip)),
        "claims": int(len(fic)),
        "revenue_lines": int(len(fcrl)),
        "diagnoses": int(len(fcd)),
        "beneficiaries_dim": int(len(dim_bene) - 1),  # excl. Unknown
        "providers_dim": int(len(dim_provider) - 1),
        "drgs_dim": int(len(dim_drg) - 1),
        "claims_with_unknown_drg": int((fic["drg_key"] == 0).sum()),
        "sum_clm_pmt_amt": round(float(fic["clm_pmt_amt"].sum()), 2),
        "unresolved_bene_fk": int((fic["bene_key"] == 0).sum()),
        "unresolved_provider_fk": int((fic["provider_key"] == 0).sum()),
    }
    return StarFrames(dims=dims, facts=facts, reconciliation=reconciliation)
