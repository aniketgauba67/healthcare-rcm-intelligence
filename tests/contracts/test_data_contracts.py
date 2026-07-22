"""Contract tests for the data-contract engine + quarantine."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from src.validation.contracts import check_contracts


def _inpatient(rows):
    return pd.DataFrame(rows)


def _row(clm_id="c1", line=1, frm=dt.date(2020, 1, 1), thru=dt.date(2020, 1, 3), pmt=100.0):
    return {
        "BENE_ID": "b1",
        "CLM_ID": clm_id,
        "CLM_LINE_NUM": line,
        "CLM_FROM_DT": frm,
        "CLM_THRU_DT": thru,
        "CLM_PMT_AMT": pmt,
        "CLM_TOT_CHRG_AMT": 120.0,
    }


def test_clean_table_passes_with_empty_quarantine():
    df = _inpatient([_row("c1", 1), _row("c2", 1)])
    r = check_contracts("inpatient", df)
    assert r.passed
    assert r.table_checks == {"required_columns": True, "key_uniqueness": True}
    assert r.quarantine.empty
    assert r.row_violations == {}


def test_date_order_violation_is_quarantined():
    df = _inpatient(
        [_row("c1", 1), _row("c2", 1, frm=dt.date(2020, 5, 10), thru=dt.date(2020, 5, 1))]
    )
    r = check_contracts("inpatient", df)
    assert r.passed  # table-level checks still pass; the bad row is isolated
    assert r.row_violations.get("date_order:CLM_FROM_DT<=CLM_THRU_DT") == 1
    q = r.quarantine
    assert len(q) == 1
    assert q.iloc[0]["contract"] == "date_order"
    assert q.iloc[0]["entity_key"] == "c2|1"


def test_negative_money_is_quarantined():
    df = _inpatient([_row("c1", 1, pmt=-5.0)])
    r = check_contracts("inpatient", df)
    assert r.row_violations.get("non_negative:CLM_PMT_AMT") == 1
    assert (r.quarantine["contract"] == "non_negative_money").all()


def test_duplicate_key_fails_uniqueness_and_quarantines():
    df = _inpatient([_row("c1", 1), _row("c1", 1)])  # duplicate CLM_ID+line
    r = check_contracts("inpatient", df)
    assert not r.passed
    assert r.table_checks["key_uniqueness"] is False
    assert r.row_violations.get("key_uniqueness") == 2
    assert (r.quarantine["contract"] == "key_uniqueness").all()


def test_missing_required_column_fails():
    df = pd.DataFrame({"CLM_ID": ["c1"], "CLM_LINE_NUM": [1]})  # missing BENE_ID etc.
    r = check_contracts("inpatient", df)
    assert not r.passed
    assert r.table_checks["required_columns"] is False
