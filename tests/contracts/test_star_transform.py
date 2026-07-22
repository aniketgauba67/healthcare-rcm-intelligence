"""Contract tests for the star-schema transform + warehouse integrity checks.

Uses a tiny in-repo fixture (no downloaded data) so it runs in CI. Exercises
Unknown-member routing, surrogate keys, FK resolution, and reconciliation.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from src.ingestion.star_transform import build_star
from src.ingestion.warehouse_checks import reconcile_to_source, run_integrity_checks


def _write_fixtures(tmp_path):
    bene = pd.DataFrame(
        {
            "BENE_ID": ["b1", "b2", "b3"],  # b3 has no claims
            "BENE_BIRTH_DT": [dt.date(1950, 1, 1)] * 3,
            "STATE_CODE": ["44", "44", "44"],
            "ZIP_CD": ["02860", "02840", "02903"],
            "AGE_AT_END_REF_YR": [74, 80, 65],
            "BENE_HI_CVRAGE_TOT_MONS": [12, 12, 12],
        }
    )
    # Two claims: c1 (2 lines, full data), c2 (null provider + null DRG -> Unknown).
    ip = pd.DataFrame(
        {
            "BENE_ID": ["b1", "b1", "b2"],
            "CLM_ID": ["c1", "c1", "c2"],
            "CLM_LINE_NUM": [1, 2, 1],
            "PRVDR_NUM": ["P1", "P1", None],
            "ORG_NPI_NUM": ["N1", "N1", None],
            "PRVDR_STATE_CD": ["44", "44", None],
            "CLM_DRG_CD": ["001", "001", None],
            "PTNT_DSCHRG_STUS_CD": ["1", "1", "1"],
            "CLM_FROM_DT": [dt.date(2020, 1, 1), dt.date(2020, 1, 1), dt.date(2020, 6, 1)],
            "CLM_THRU_DT": [dt.date(2020, 1, 3), dt.date(2020, 1, 3), dt.date(2020, 6, 2)],
            "CLM_ADMSN_DT": [dt.date(2020, 1, 1), dt.date(2020, 1, 1), dt.date(2020, 6, 1)],
            "NCH_BENE_DSCHRG_DT": [dt.date(2020, 1, 3), dt.date(2020, 1, 3), dt.date(2020, 6, 2)],
            "NCH_CLM_TYPE_CD": ["60", "60", "60"],
            "ADMTG_DGNS_CD": ["A1", "A1", "A2"],
            "PRNCPAL_DGNS_CD": ["D1", "D1", "D2"],
            "CLM_UTLZTN_DAY_CNT": [2, 2, 1],
            "CLM_PMT_AMT": [100.0, 100.0, 250.0],
            "CLM_TOT_CHRG_AMT": [120.0, 120.0, 300.0],
            "NCH_IP_NCVRD_CHRG_AMT": [0.0, 0.0, 0.0],
            "NCH_BENE_IP_DDCTBL_AMT": [0.0, 0.0, 0.0],
            "REV_CNTR": ["0450", "0451", "0450"],
            "HCPCS_CD": ["H1", "H2", "H3"],
            "ICD_DGNS_CD1": ["D1", "D1", "D2"],
            "CLM_POA_IND_SW1": ["Y", "Y", "N"],
        }
    )
    bene_path = tmp_path / "bene.parquet"
    ip_path = tmp_path / "ip.parquet"
    bene.to_parquet(bene_path)
    ip.to_parquet(ip_path)
    return bene_path, ip_path


def test_build_star_structure_and_unknown_members(tmp_path):
    bp, ipp = _write_fixtures(tmp_path)
    sf = build_star(bp, ipp)

    # Every dimension carries an Unknown member at key 0.
    assert (sf.dims["dim_beneficiary"]["bene_key"] == 0).sum() == 1
    assert (sf.dims["dim_provider"]["provider_key"] == 0).sum() == 1
    assert (sf.dims["dim_drg"]["drg_key"] == 0).sum() == 1
    assert (sf.dims["dim_date"]["date_key"] == 0).sum() == 1

    # Header grain: 2 claims, 3 revenue lines, diagnoses from both claims.
    assert len(sf.facts["fact_inpatient_claim"]) == 2
    assert len(sf.facts["fact_claim_revenue_line"]) == 3

    fic = sf.facts["fact_inpatient_claim"].set_index("clm_id")
    # c2 has null provider + null DRG -> Unknown member (key 0).
    assert fic.loc["c2", "provider_key"] == 0
    assert fic.loc["c2", "drg_key"] == 0
    # c1 resolves to real members and LOS = 3 days (Jan 1..3 inclusive).
    assert fic.loc["c1", "provider_key"] != 0
    assert fic.loc["c1", "length_of_stay_days"] == 3


def test_integrity_and_reconciliation_pass(tmp_path):
    bp, ipp = _write_fixtures(tmp_path)
    sf = build_star(bp, ipp)

    checks = run_integrity_checks(sf) + reconcile_to_source(
        sf, raw_inpatient_lines=3, source_distinct_claims=2
    )
    failed = [c.name for c in checks if not c.passed]
    assert not failed, f"integrity/reconciliation failed: {failed}"

    rec = sf.reconciliation
    assert rec["claims"] == 2
    assert rec["revenue_lines"] == 3
    assert rec["claims_with_unknown_drg"] == 1
    assert rec["unresolved_provider_fk"] == 1  # c2 null provider -> Unknown
    assert rec["unresolved_bene_fk"] == 0
