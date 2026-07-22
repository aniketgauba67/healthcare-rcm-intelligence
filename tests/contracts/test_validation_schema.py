"""Contract tests for validated-layer dtype resolution and Parquet staging."""

from __future__ import annotations

import pyarrow.parquet as pq

from src.validation.schemas import arrow_schema, build_plan, classify_column
from src.validation.stage_parquet import stage_file


def test_classify_column_rules():
    # dates
    assert classify_column("CLM_FROM_DT") == "date"
    assert classify_column("PRCDR_DT1") == "date"
    assert classify_column("COVSTART") == "date"
    # money
    assert classify_column("CLM_PMT_AMT") == "money"
    assert classify_column("CLM_PPS_CPTL_DRG_WT_NUM") == "money"
    # int
    assert classify_column("CLM_UTLZTN_DAY_CNT") == "int"
    assert classify_column("BENE_HI_CVRAGE_TOT_MONS") == "int"
    assert classify_column("AGE_AT_END_REF_YR") == "int"
    assert classify_column("CLM_LINE_NUM") == "int"
    # string: codes, ids, indicators must stay text (leading zeros / signs)
    for col in (
        "CLM_DRG_CD",
        "PRVDR_NUM",
        "ORG_NPI_NUM",
        "ZIP_CD",
        "REV_CNTR",
        "VALID_DEATH_DT_SW",
        "ESRD_IND",
        "BENE_ID",
    ):
        assert classify_column(col) == "string", col


def test_arrow_schema_types():
    plan = build_plan(["BENE_ID", "CLM_FROM_DT", "CLM_PMT_AMT", "CLM_UTLZTN_DAY_CNT"])
    sch = arrow_schema(plan)
    types = {f.name: str(f.type) for f in sch}
    assert types == {
        "BENE_ID": "string",
        "CLM_FROM_DT": "date32[day]",
        "CLM_PMT_AMT": "double",
        "CLM_UTLZTN_DAY_CNT": "int64",
    }


def _write_fixture(path):
    header = "BENE_ID|PRVDR_NUM|CLM_FROM_DT|CLM_PMT_AMT|CLM_UTLZTN_DAY_CNT|PRNCPAL_DGNS_CD"
    rows = [
        "-1|011500|25-Mar-2015|96.65|0|S134XX",
        "-2|000700||100.00|3|Z3480",  # empty date -> null, not a parse failure
        "-3|012000|BADDATE|1.00|1|A00",  # non-empty unparseable date -> counted
    ]
    path.write_text("\n".join([header, *rows]) + "\n")


def test_stage_file_types_and_reconciliation(tmp_path):
    raw = tmp_path / "mini.csv"
    out = tmp_path / "mini.parquet"
    _write_fixture(raw)

    result = stage_file("mini", raw, out, chunksize=2)  # force multi-chunk path

    assert result.rows_in == 3
    assert result.rows_out == 3
    assert result.reconciles
    assert result.columns == 6
    # BADDATE is present-but-unparseable; empty string is not counted.
    assert result.date_null_from_nonempty == {"CLM_FROM_DT": 1}

    table = pq.read_table(out)
    types = {f.name: str(f.type) for f in table.schema}
    assert types["BENE_ID"] == "string"
    assert types["PRVDR_NUM"] == "string"
    assert types["CLM_FROM_DT"] == "date32[day]"
    assert types["CLM_PMT_AMT"] == "double"
    assert types["CLM_UTLZTN_DAY_CNT"] == "int64"
    assert types["PRNCPAL_DGNS_CD"] == "string"

    df = table.to_pandas()
    # Leading zeros and signs preserved.
    assert list(df["PRVDR_NUM"]) == ["011500", "000700", "012000"]
    assert list(df["BENE_ID"]) == ["-1", "-2", "-3"]
    assert str(df["CLM_FROM_DT"].iloc[0]) == "2015-03-25"
    assert df["CLM_FROM_DT"].iloc[1] is None or df["CLM_FROM_DT"].isna().iloc[1]
