"""Typed schema resolution for the CMS RIF flat files.

RIF files carry hundreds of columns, so dtypes are resolved by explicit,
auditable rules over column names plus small override sets for the exceptions,
rather than a hand-maintained 300-line literal map. Every raw column is first
read as text (to preserve leading zeros, signed synthetic ids, and ICD codes)
and then cast by the `kind` this module assigns:

    date   -> parsed DD-Mon-YYYY into a date        (arrow date32)
    money  -> float64                                (arrow float64)
    int    -> nullable integer                       (arrow int64)
    string -> left as text                           (arrow string)

Codes (`*_CD`), identifiers (`*_NUM`, `*_NPI`, `*_ID`), ZIPs, and indicator
switches (`*_SW`, `*_IND`) intentionally stay strings.
"""

from __future__ import annotations

import re

import pyarrow as pa

DATE_FORMAT = "%d-%b-%Y"

# Date columns end in `_DT`, optionally followed by an index digit
# (e.g. CLM_FROM_DT, and the procedure dates PRCDR_DT1..PRCDR_DT25).
_DATE_RE = re.compile(r"_DT\d*$")
_MONEY_SUFFIXES: tuple[str, ...] = ("_AMT",)
_INT_SUFFIXES: tuple[str, ...] = ("_CNT", "_MONS", "_DAYS", "_QTY", "_YR")

# Explicit exceptions where the suffix rule would misclassify.
_DATE_EXPLICIT: frozenset[str] = frozenset({"COVSTART"})
_FLOAT_EXPLICIT: frozenset[str] = frozenset({"CLM_PPS_CPTL_DRG_WT_NUM"})
_INT_EXPLICIT: frozenset[str] = frozenset({"CLM_LINE_NUM"})

_KINDS = ("date", "money", "int", "string")


def classify_column(name: str) -> str:
    """Return the dtype kind for a RIF column: date|money|int|string."""
    if name in _DATE_EXPLICIT or _DATE_RE.search(name):
        return "date"
    if name in _FLOAT_EXPLICIT or name.endswith(_MONEY_SUFFIXES):
        return "money"
    if name in _INT_EXPLICIT or name.endswith(_INT_SUFFIXES):
        return "int"
    return "string"


def build_plan(columns: list[str]) -> dict[str, str]:
    """Map every column name to its kind, preserving column order."""
    return {c: classify_column(c) for c in columns}


_ARROW_BY_KIND = {
    "date": pa.date32(),
    "money": pa.float64(),
    "int": pa.int64(),
    "string": pa.string(),
}


def arrow_schema(plan: dict[str, str]) -> pa.Schema:
    """Build the target Arrow schema (all fields nullable) from a plan."""
    return pa.schema([pa.field(name, _ARROW_BY_KIND[kind]) for name, kind in plan.items()])


def columns_by_kind(plan: dict[str, str], kind: str) -> list[str]:
    """Return the columns assigned a given kind."""
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    return [c for c, k in plan.items() if k == kind]
