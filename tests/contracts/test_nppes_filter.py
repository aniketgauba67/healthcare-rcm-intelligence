"""Contract tests for the NPPES state-filter streaming logic."""

from __future__ import annotations

import io

import pytest

from src.ingestion.nppes import stream_filter

_STATE_COL = "Provider Business Practice Location Address State Name"

_HEADER = f'"NPI","Entity Type Code","{_STATE_COL}"\n'
_ROWS = (
    '"1000000001","1","RI"\n'
    '"1000000002","2","MA"\n'
    '"1000000003","1","ri"\n'  # lowercase should still match
    '"1000000004","1"," RI "\n'  # padded should still match
    '"1000000005","1","CT"\n'
)


def test_stream_filter_keeps_only_target_state():
    src = io.StringIO(_HEADER + _ROWS)
    dst = io.StringIO()
    scanned, kept = stream_filter(src, dst, state_column=_STATE_COL, state_value="RI")
    assert scanned == 5
    assert kept == 3  # RI, ri, " RI "
    out = dst.getvalue().splitlines()
    assert out[0].count("NPI") == 1  # header preserved once
    assert len(out) == 4  # header + 3 rows
    assert all("MA" not in line and "CT" not in line for line in out[1:])


def test_stream_filter_missing_column_raises():
    src = io.StringIO('"NPI","Entity Type Code"\n"1000000001","1"\n')
    with pytest.raises(KeyError):
        stream_filter(src, io.StringIO(), state_column=_STATE_COL, state_value="RI")


def test_stream_filter_empty_result_is_valid():
    src = io.StringIO(_HEADER + _ROWS)
    dst = io.StringIO()
    scanned, kept = stream_filter(src, dst, state_column=_STATE_COL, state_value="WY")
    assert scanned == 5
    assert kept == 0
    assert dst.getvalue().splitlines()[0].count("NPI") == 1  # header still written
