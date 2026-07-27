"""Contract tests for the SIMULATED linkage crosswalk (CLAUDE.md §3.4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

import duckdb

from src.ingestion.crosswalk import (
    SSA_TO_POSTAL,
    CrosswalkResult,
    build_facility_crosswalk,
    build_provider_crosswalk,
)
from src.ingestion.warehouse_sql_checks import run_crosswalk_checks


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
    assert row["sim_facility_state"] == "FL" and row["sim_same_state"]
    assert bool(a["sim_same_state"].all())


def test_provider_crosswalk_state_coherent_and_plausible():
    ip = pd.DataFrame({"AT_PHYSN_NPI": ["ph1", "ph1", "ph2"], "PRVDR_STATE_CD": ["10", "10", "01"]})
    prov = _providers()
    a = build_provider_crosswalk(ip, prov, np.random.default_rng(3))
    b = build_provider_crosswalk(ip, prov, np.random.default_rng(3))

    assert len(a) == 2
    assert a.equals(b)
    ph1 = a.set_index("sim_at_physn_npi").loc["ph1"]
    assert ph1["sim_assigned_postal_state"] == "FL"
    assert ph1["sim_real_provider_state"] == "FL" and ph1["sim_same_state"]
    # Only individual, inpatient-plausible providers are chosen (not the 'O' clinic).
    assert ph1["sim_real_specialty"] == "Internal Medicine"
    assert bool((a["sim_match_rule"] == "state+plausible_specialty").all())


def test_crosswalk_checks_detect_fk_and_provenance_violations():
    con = duckdb.connect()
    con.execute("create schema rcm")
    con.execute("create table rcm.dim_provider (prvdr_num text)")
    con.execute("insert into rcm.dim_provider values ('p1')")
    con.execute("create table rcm.sim_facility_crosswalk (sim_prvdr_num text, sim_provenance text)")
    con.execute("create table rcm.sim_provider_crosswalk (sim_provenance text)")

    def scalar(sql):
        return con.execute(sql).fetchone()[0]

    # Clean: p1 resolves, provenance SIMULATED.
    con.execute("insert into rcm.sim_facility_crosswalk values ('p1', 'SIMULATED')")
    con.execute("insert into rcm.sim_provider_crosswalk values ('SIMULATED')")
    assert all(c.passed for c in run_crosswalk_checks(scalar, "rcm."))

    # Inject an orphan FK and a bad provenance -> both checks fail.
    con.execute("insert into rcm.sim_facility_crosswalk values ('p_missing', 'REAL')")
    results = {c.name: c.passed for c in run_crosswalk_checks(scalar, "rcm.")}
    assert results["xwalk_fk:sim_prvdr_num->dim_provider"] is False
    assert results["xwalk_provenance:sim_facility_crosswalk"] is False
    assert results["xwalk_provenance:sim_provider_crosswalk"] is True


def test_every_crosswalk_column_carries_sim_prefix():
    """Regression guard for CLAUDE.md §3.2: BOTH crosswalk tables are SIMULATED,
    so every column that lands in Postgres must start with `sim_`. Asserts on the
    full loadable frames (builder output + seed/provenance) actually written by
    load_crosswalk via to_sql (column-name mapped)."""
    ip_fac = pd.DataFrame({"PRVDR_NUM": ["p1", "p2"], "PRVDR_STATE_CD": ["10", "01"]})
    ip_prov = pd.DataFrame({"AT_PHYSN_NPI": ["ph1", "ph2"], "PRVDR_STATE_CD": ["10", "01"]})
    fac = build_facility_crosswalk(ip_fac, _facilities(), np.random.default_rng(1))
    prov = build_provider_crosswalk(ip_prov, _providers(), np.random.default_rng(2))
    result = CrosswalkResult(facility=fac, provider=prov, report={"crosswalk_seed": 1})

    frames = result.loadable_frames()
    assert set(frames) == {"sim_facility_crosswalk", "sim_provider_crosswalk"}
    for name, df in frames.items():
        offenders = [c for c in df.columns if not c.startswith("sim_")]
        assert not offenders, f"{name} has non-sim_-prefixed columns: {offenders}"
