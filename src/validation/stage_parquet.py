"""Standardize raw RIF CSVs into typed Parquet (the validated layer).

Chunked, streaming, and idempotent: the raw pipe-delimited file is read in
row chunks (never fully in memory), every column cast to its resolved dtype,
and the result written to a single Parquet file under data/validated/. Row
counts are reconciled against the raw file and reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.ingestion.logging_utils import get_logger, log_event

from .schemas import DATE_FORMAT, arrow_schema, build_plan, columns_by_kind

_LOGGER = get_logger("validation.stage")
_ENCODING = "latin-1"  # matches the raw extract encoding; preserves legacy bytes
_DEFAULT_CHUNKSIZE = 50_000


@dataclass
class StageResult:
    """Outcome of staging one file."""

    name: str
    raw_path: str
    out_path: str
    rows_in: int
    rows_out: int
    columns: int
    date_null_from_nonempty: dict[str, int]  # values that were present but unparseable

    @property
    def reconciles(self) -> bool:
        return self.rows_in == self.rows_out


def _cast_chunk(
    chunk: pd.DataFrame,
    plan: dict[str, str],
    date_cols: list[str],
    money_cols: list[str],
    int_cols: list[str],
    bad_dates: dict[str, int],
) -> pa.Table:
    """Cast a text chunk to the typed Arrow table for the plan's schema."""
    for col in money_cols:
        chunk[col] = pd.to_numeric(chunk[col], errors="coerce").astype("float64")
    for col in int_cols:
        chunk[col] = pd.to_numeric(chunk[col], errors="coerce").astype("Int64")
    for col in date_cols:
        present = chunk[col].notna()
        parsed = pd.to_datetime(chunk[col], format=DATE_FORMAT, errors="coerce")
        # Count values that were non-null text but failed to parse (data quality).
        bad_dates[col] += int((present & parsed.isna()).sum())
        chunk[col] = parsed
    # Remaining columns stay as pandas string dtype.
    for col in columns_by_kind(plan, "string"):
        chunk[col] = chunk[col].astype("string")

    table = pa.Table.from_pandas(chunk, preserve_index=False)
    # Cast to the canonical schema (e.g. timestamp -> date32) for chunk stability.
    return table.cast(arrow_schema(plan))


def stage_file(
    name: str,
    raw_path: Path,
    out_path: Path,
    *,
    delimiter: str = "|",
    chunksize: int = _DEFAULT_CHUNKSIZE,
) -> StageResult:
    """Stage one raw RIF CSV to typed Parquet, returning a reconciliation result."""
    header = pd.read_csv(raw_path, sep=delimiter, nrows=0, dtype=str, encoding=_ENCODING)
    plan = build_plan(list(header.columns))
    schema = arrow_schema(plan)
    date_cols = columns_by_kind(plan, "date")
    money_cols = columns_by_kind(plan, "money")
    int_cols = columns_by_kind(plan, "int")
    bad_dates: dict[str, int] = {c: 0 for c in date_cols}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")

    log_event(_LOGGER, "stage.start", name=name, raw=str(raw_path), columns=len(plan))
    rows_in = 0
    writer = pq.ParquetWriter(tmp_path, schema)
    try:
        reader = pd.read_csv(
            raw_path,
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            encoding=_ENCODING,
            chunksize=chunksize,
        )
        for chunk in reader:
            rows_in += len(chunk)
            table = _cast_chunk(chunk, plan, date_cols, money_cols, int_cols, bad_dates)
            writer.write_table(table)
    finally:
        writer.close()
    tmp_path.replace(out_path)

    rows_out = pq.ParquetFile(out_path).metadata.num_rows
    result = StageResult(
        name=name,
        raw_path=str(raw_path),
        out_path=str(out_path),
        rows_in=rows_in,
        rows_out=rows_out,
        columns=len(plan),
        date_null_from_nonempty={c: n for c, n in bad_dates.items() if n},
    )
    log_event(
        _LOGGER,
        "stage.done",
        name=name,
        rows_in=rows_in,
        rows_out=rows_out,
        reconciles=result.reconciles,
        unparseable_dates=result.date_null_from_nonempty,
        out=str(out_path),
    )
    if not result.reconciles:
        raise ValueError(f"row reconciliation failed for {name}: in={rows_in} out={rows_out}")
    return result
