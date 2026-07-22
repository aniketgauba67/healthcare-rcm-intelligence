"""SIMULATED linkage crosswalk (CLAUDE.md §3.4).

Synthetic claims carry SYNTHETIC provider/facility identifiers that do NOT join
to real CCNs/NPIs. This module builds the reproducible, seeded, stratified
assignment of synthetic entities to REAL entities:

  * sim_facility_crosswalk : synthetic billing provider (PRVDR_NUM) -> real CMS
    Hospital General Information facility (CCN), stratified by state + type.
  * sim_provider_crosswalk : synthetic attending physician (AT_PHYSN_NPI) ->
    real Medicare provider (NPI), stratified by the state coherent with the
    physician's claims and by inpatient-plausible specialty.

Both tables are classified SIMULATED (sim_ prefix) in the provenance register.
The seed lives in config/simulation.yaml (`linkage.crosswalk_seed`, read-only
here) so the same seed reproduces an identical crosswalk. Nothing in this module
alters or reads the simulation generator's `seed`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yaml

from .paths import CONFIG_DIR, DATA_RAW

# Standard SSA state codes used in CMS RIF STATE_CD fields -> USPS postal code.
# (SSA/CMS state code table; the synthetic beneficiary/provider state fields use
# these, while the facility and provider reference files use postal codes.)
SSA_TO_POSTAL: dict[str, str] = {
    "01": "AL",
    "02": "AK",
    "03": "AZ",
    "04": "AR",
    "05": "CA",
    "06": "CO",
    "07": "CT",
    "08": "DE",
    "09": "DC",
    "10": "FL",
    "11": "GA",
    "12": "HI",
    "13": "ID",
    "14": "IL",
    "15": "IN",
    "16": "IA",
    "17": "KS",
    "18": "KY",
    "19": "LA",
    "20": "ME",
    "21": "MD",
    "22": "MA",
    "23": "MI",
    "24": "MN",
    "25": "MS",
    "26": "MO",
    "27": "MT",
    "28": "NE",
    "29": "NV",
    "30": "NH",
    "31": "NJ",
    "32": "NM",
    "33": "NY",
    "34": "NC",
    "35": "ND",
    "36": "OH",
    "37": "OK",
    "38": "OR",
    "39": "PA",
    "40": "PR",
    "41": "RI",
    "42": "SC",
    "43": "SD",
    "44": "TN",
    "45": "TX",
    "46": "UT",
    "47": "VT",
    "48": "VI",
    "49": "VA",
    "50": "WA",
    "51": "WV",
    "52": "WI",
    "53": "WY",
}

_ACUTE_TYPES = {
    "Acute Care Hospitals",
    "Critical Access Hospitals",
    "Acute Care - Veterans Administration",
    "Acute Care - Department of Defense",
    "Rural Emergency Hospital",
}

# Provider specialties plausible as an inpatient attending physician. Used to
# prefer coherent assignments; a fallback to any in-state individual is flagged.
_INPATIENT_PLAUSIBLE_TYPES = {
    "Internal Medicine",
    "Hospitalist",
    "Family Practice",
    "General Practice",
    "Cardiology",
    "Critical Care (Intensivists)",
    "Pulmonary Disease",
    "Emergency Medicine",
    "General Surgery",
    "Orthopedic Surgery",
    "Neurology",
    "Nephrology",
    "Gastroenterology",
    "Infectious Disease",
    "Hematology-Oncology",
    "Cardiac Surgery",
    "Vascular Surgery",
    "Neurosurgery",
    "Obstetrics & Gynecology",
    "Anesthesiology",
    "Physical Medicine and Rehabilitation",
    "Geriatric Medicine",
    "Endocrinology",
    "Rheumatology",
    "Urology",
    "Nurse Practitioner",
    "Physician Assistant",
}


@dataclass
class CrosswalkResult:
    facility: pd.DataFrame
    provider: pd.DataFrame
    report: dict[str, object] = field(default_factory=dict)

    def loadable_frames(self) -> dict[str, pd.DataFrame]:
        """Crosswalk frames with provenance + seed columns, ready to load/check."""
        seed = int(self.report["crosswalk_seed"])
        out = {}
        for name, df in (
            ("sim_facility_crosswalk", self.facility),
            ("sim_provider_crosswalk", self.provider),
        ):
            d = df.copy()
            d["crosswalk_seed"] = seed
            d["provenance"] = "SIMULATED"
            out[name] = d
        return out


def crosswalk_seed() -> int:
    """Read the dedicated crosswalk seed from config/simulation.yaml (read-only)."""
    doc = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
    return int(doc["linkage"]["crosswalk_seed"])


def load_facilities() -> pd.DataFrame:
    df = pd.read_csv(
        DATA_RAW / "reference" / "hospital_general_information.csv",
        dtype=str,
        encoding="latin-1",
    )
    df = df.rename(
        columns={
            "Facility ID": "facility_ccn",
            "Facility Name": "facility_name",
            "State": "facility_state",
            "Hospital Type": "facility_type",
        }
    )
    return df[["facility_ccn", "facility_name", "facility_state", "facility_type"]].dropna(
        subset=["facility_ccn", "facility_state"]
    )


def load_providers() -> pd.DataFrame:
    df = pd.read_csv(DATA_RAW / "reference" / "medicare_providers_extract.csv", dtype=str)
    df = df.rename(
        columns={
            "Rndrng_NPI": "real_npi",
            "Rndrng_Prvdr_Ent_Cd": "ent_cd",
            "Rndrng_Prvdr_State_Abrvtn": "real_state",
            "Rndrng_Prvdr_Type": "real_specialty",
        }
    )
    return df[["real_npi", "ent_cd", "real_state", "real_specialty"]].dropna(
        subset=["real_npi", "real_state"]
    )


def _pick(rng: np.random.Generator, frame: pd.DataFrame) -> pd.Series:
    return frame.iloc[int(rng.integers(len(frame)))]


def build_facility_crosswalk(
    inpatient: pd.DataFrame, facilities: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Assign each synthetic billing provider to a real facility (state+type)."""
    prov = (
        inpatient[["PRVDR_NUM", "PRVDR_STATE_CD"]]
        .dropna(subset=["PRVDR_NUM"])
        .drop_duplicates("PRVDR_NUM")
        .sort_values("PRVDR_NUM")
        .reset_index(drop=True)
    )
    by_state_acute = {
        st: g
        for st, g in facilities[facilities["facility_type"].isin(_ACUTE_TYPES)].groupby(
            "facility_state"
        )
    }
    by_state_any = {st: g for st, g in facilities.groupby("facility_state")}

    rows = []
    for prvdr_num, ssa in zip(prov["PRVDR_NUM"], prov["PRVDR_STATE_CD"]):
        postal = SSA_TO_POSTAL.get(str(ssa))
        pool = by_state_acute.get(postal)
        match = "state+acute"
        if pool is None or pool.empty:
            pool = by_state_any.get(postal)
            match = "state_any_type"
        if pool is None or pool.empty:
            pool = facilities  # nationwide fallback
            match = "nationwide_fallback"
        chosen = _pick(rng, pool)
        rows.append(
            {
                "sim_prvdr_num": prvdr_num,
                "sim_provider_ssa_state": ssa,
                "sim_provider_postal_state": postal,
                "facility_ccn": chosen["facility_ccn"],
                "facility_name": chosen["facility_name"],
                "facility_state": chosen["facility_state"],
                "facility_type": chosen["facility_type"],
                "match_rule": match,
                "same_state": bool(postal is not None and chosen["facility_state"] == postal),
            }
        )
    return pd.DataFrame(rows)


def build_provider_crosswalk(
    inpatient: pd.DataFrame, providers: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Assign each synthetic attending physician to a real Medicare provider,
    in a state coherent with the physician's claims and an inpatient-plausible
    specialty where possible."""
    # Each physician's coherent state = modal claim provider-state (SSA -> postal).
    phys = inpatient[["AT_PHYSN_NPI", "PRVDR_STATE_CD"]].dropna(subset=["AT_PHYSN_NPI"]).copy()
    phys["postal"] = phys["PRVDR_STATE_CD"].map(lambda s: SSA_TO_POSTAL.get(str(s)))
    modal = (
        phys.dropna(subset=["postal"])
        .groupby("AT_PHYSN_NPI")["postal"]
        .agg(lambda s: s.mode().iloc[0])
        .reset_index()
        .sort_values("AT_PHYSN_NPI")
        .reset_index(drop=True)
    )

    indiv = providers[providers["ent_cd"] == "I"]
    plausible = indiv[indiv["real_specialty"].isin(_INPATIENT_PLAUSIBLE_TYPES)]
    by_state_plausible = {st: g for st, g in plausible.groupby("real_state")}
    by_state_indiv = {st: g for st, g in indiv.groupby("real_state")}

    rows = []
    for npi, postal in zip(modal["AT_PHYSN_NPI"], modal["postal"]):
        pool = by_state_plausible.get(postal)
        match = "state+plausible_specialty"
        if pool is None or pool.empty:
            pool = by_state_indiv.get(postal)
            match = "state_any_specialty"
        if pool is None or pool.empty:
            pool = indiv
            match = "nationwide_fallback"
        chosen = _pick(rng, pool)
        rows.append(
            {
                "sim_at_physn_npi": npi,
                "assigned_postal_state": postal,
                "real_npi": chosen["real_npi"],
                "real_provider_state": chosen["real_state"],
                "real_specialty": chosen["real_specialty"],
                "match_rule": match,
                "same_state": bool(chosen["real_state"] == postal),
            }
        )
    return pd.DataFrame(rows)


def build_crosswalk(inpatient_parquet=None, seed: int | None = None) -> CrosswalkResult:
    """Build both crosswalks reproducibly from the configured seed."""
    from .paths import DATA_VALIDATED

    ip = pd.read_parquet(inpatient_parquet or DATA_VALIDATED / "inpatient.parquet")
    used_seed = seed if seed is not None else crosswalk_seed()
    facilities = load_facilities()
    providers = load_providers()

    # Independent RNG streams so facility and provider assignment don't couple.
    fac = build_facility_crosswalk(ip, facilities, np.random.default_rng(used_seed))
    prov = build_provider_crosswalk(ip, providers, np.random.default_rng(used_seed + 1))

    report = {
        "crosswalk_seed": used_seed,
        "facility_rows": int(len(fac)),
        "facility_same_state_rate": round(float(fac["same_state"].mean()), 4),
        "facility_nationwide_fallback": int((fac["match_rule"] == "nationwide_fallback").sum()),
        "provider_rows": int(len(prov)),
        "provider_same_state_rate": round(float(prov["same_state"].mean()), 4),
        "provider_plausible_rate": round(
            float((prov["match_rule"] == "state+plausible_specialty").mean()), 4
        ),
    }
    return CrosswalkResult(facility=fac, provider=prov, report=report)
