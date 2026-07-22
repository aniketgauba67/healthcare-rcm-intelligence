"""Contract tests for the SIMULATED linkage crosswalk (CLAUDE.md §3.4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.ingestion.crosswalk import (
    SSA_TO_POSTAL,
    build_facility_crosswalk,
    build_provider_crosswalk,
)


def test_ssa_map_covers_50_states_plus_dc():
    postals = set(SSA_TO_POSTAL.values())
    assert "RI" in postals and SSA_TO_POSTAL["41"] == "RI"
    assert "AL" in postals and SSA_TO_POSTAL["01"] == "AL"
    # 50 states + DC all present.
    assert {"DC", "CA", "NY", "TX", "HI", "AK"} <= postals
    assert len(SSA_TO_POSTAL) >= 51


def _facilities():
    return pd.DataFrame(
        {
            "facility_ccn": ["100001", "010001", "360001"],
            "facility_name": ["FL Gen", "AL Gen", "OH Gen"],
            "facility_state": ["FL", "AL", "OH"],
            "facility_type": ["Acute Care Hospitals"] * 3,
        }
    )


def _providers():
    return pd.DataFrame(
        {
            "real_npi": ["1000000001", "1000000002", "1000000003"],
            "ent_cd": ["I", "I", "O"],
            "real_state": ["FL", "AL", "FL"],
            "real_specialty": ["Internal Medicine", "Emergency Medicine", "Clinic"],
        }
    )


def test_facility_crosswalk_same_state_and_reproducible():
    ip = pd.DataFrame({"PRVDR_NUM": ["p1", "p1", "p2"], "PRVDR_STATE_CD": ["10", "10", "01"]})
    fac = _facilities()
    a = build_facility_crosswalk(ip, fac, np.random.default_rng(7))
    b = build_facility_crosswalk(ip, fac, np.random.default_rng(7))

    assert len(a) == 2  # one row per distinct synthetic provider
    assert a.equals(b)  # same seed -> identical (reproducible)
    row = a.set_index("sim_prvdr_num").loc["p1"]
    assert row["sim_provider_postal_state"] == "FL"
    assert row["facility_state"] == "FL" and row["same_state"]
    assert bool(a["same_state"].all())


def test_provider_crosswalk_state_coherent_and_plausible():
    ip = pd.DataFrame({"AT_PHYSN_NPI": ["ph1", "ph1", "ph2"], "PRVDR_STATE_CD": ["10", "10", "01"]})
    prov = _providers()
    a = build_provider_crosswalk(ip, prov, np.random.default_rng(3))
    b = build_provider_crosswalk(ip, prov, np.random.default_rng(3))

    assert len(a) == 2
    assert a.equals(b)
    ph1 = a.set_index("sim_at_physn_npi").loc["ph1"]
    assert ph1["assigned_postal_state"] == "FL"
    assert ph1["real_provider_state"] == "FL" and ph1["same_state"]
    # Only individual, inpatient-plausible providers are chosen (not the 'O' clinic).
    assert ph1["real_specialty"] == "Internal Medicine"
    assert bool((a["match_rule"] == "state+plausible_specialty").all())
