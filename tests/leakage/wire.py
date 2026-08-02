"""What a curated view actually puts on the wire, and how to build a frame of it.

QA-OWNED (`tests/leakage/` is qa's). The companion to `exposure.py`, and it exists
because `exposure.py` cannot do this job — see the module docstring of
`test_wire_provenance.py` for the measurement that established that.

`exposure.py` perturbs a simulated input and reports emitted columns that MOVE.
That works on `src/models/work_queue.py`, which COMPUTES its columns. Every read
surface in `src/api/` and (shortly) `dashboard/` is a PASS-THROUGH: the columns
arrive already computed by a view, nothing downstream moves when an input moves,
and the probe reports a clean surface no matter what the column is made of. So
provenance at the wire has to be checked against a DECLARATION, and the only
declarations that exist are the view SQL and `config/model.yaml`.

THE PARSER IS CHECKED AGAINST THE THING IT APPROXIMATES
------------------------------------------------------
`view_output_columns` reads the final select list out of the shipped SQL so the
gate runs in CI with no database. A parser that silently returns the wrong column
set would make the gate green for the wrong reason, which is the failure this
project keeps paying for, so `test_the_sql_parse_matches_the_live_catalog`
(integration) asserts the parse equals `information_schema` for every view. One
view is excluded by name and with its reason, not silently skipped.
"""

from __future__ import annotations

import pathlib
import re

import numpy as np
import pandas as pd

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS_DIR = REPO_ROOT / "sql" / "views"

#: `vw_model_monitoring`'s body is a UNION of literal-projection selects, so the
#: "final select list" this parser reads is a set of literals rather than the
#: view's output names. Named here rather than dropped from the sweep quietly: it
#: is a DRIFT SCAFFOLD carrying no simulated money, and the integration
#: cross-check below still measures it against the live catalog.
UNPARSEABLE_VIEWS = {
    "vw_model_monitoring": "body is a union of literal projections; parsed names are the literals",
}


def strip_sql_comments(sql: str) -> str:
    """Drop `--` line comments and `/* */` blocks, keeping line structure."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _split_top_level(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)
    return [" ".join(item.split()) for item in items if item.strip()]


def view_output_columns(path: pathlib.Path) -> list[str]:
    """The output column names of the view defined in `path`.

    Reads the last depth-0 `select` of the `create view` statement and takes each
    item's alias, falling back to the trailing identifier of a bare column
    reference. Depth tracking is what keeps a scalar subquery or a `case` in the
    select list from being mistaken for the projection boundary.
    """
    body = strip_sql_comments(path.read_text())
    statements = [s for s in body.split(";") if "view" in s.lower() and "select" in s.lower()]
    statement = statements[-1]

    depth = 0
    start = 0
    for match in re.finditer(r"[()]|\bselect\b", statement, flags=re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            start = match.end()

    tail = statement[start:]
    depth = 0
    end = len(tail)
    for match in re.finditer(r"[()]|\bfrom\b", tail, flags=re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            end = match.start()
            break

    columns: list[str] = []
    for item in _split_top_level(tail[:end]):
        alias = re.search(r"\bas\s+([A-Za-z_]\w*)\s*$", item, flags=re.IGNORECASE)
        if alias:
            columns.append(alias.group(1))
            continue
        bare = re.search(r"([A-Za-z_]\w*)\s*$", item)
        columns.append(bare.group(1) if bare else item)
    return columns


def view_paths() -> list[pathlib.Path]:
    paths = sorted(VIEWS_DIR.glob("vw_*.sql"))
    assert paths, f"no view SQL under {VIEWS_DIR}"
    return paths


def view_columns_by_name() -> dict[str, list[str]]:
    """Every parseable view, mapped to its output columns."""
    return {
        path.stem: view_output_columns(path)
        for path in view_paths()
        if path.stem not in UNPARSEABLE_VIEWS
    }


# ---------------------------------------------------------------------------
# A frame shaped like a view, so a route can be driven without a database
# ---------------------------------------------------------------------------

#: Dtypes are inferred from the NAME, and the default is NUMERIC rather than
#: string. The direction matters: a measure that arrives as text blows up inside
#: a `sum()` and the failure is loud, whereas an identifier that arrives as a
#: number is silently accepted and the gate goes on measuring something slightly
#: unlike the real payload.
_BOOL = ("_flag", "_placeholder", "adjudicated", "any_overturned")
_DATE = ("_date",)
_TEXT = (
    "_cd",
    "_id",
    "_name",
    "_desc",
    "_type",
    "_category",
    "_group",
    "_mechanism",
    "_label",
    "_bucket",
    "_month",
    "_state",
    "_kind",
    "_num",
    "_ccn",
    "_line",
    "description",
    "dimension",
    "severity",
)


def _column_values(name: str, n: int, rng: np.random.Generator) -> pd.Series:
    if name.endswith(_BOOL) or name in _BOOL:
        return pd.Series(rng.random(n) < 0.5)
    if name.endswith(_DATE):
        return pd.Series(pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 400, n), "D"))
    if name.endswith(_TEXT) or name in _TEXT:
        return pd.Series([f"{name}_{i}" for i in range(n)])
    return pd.Series(rng.uniform(1.0, 9000.0, n).round(2))


def frame_like(columns: list[str], n: int = 40, seed: int = 20260729) -> pd.DataFrame:
    """A synthetic frame with a view's columns and plausible dtypes.

    The VALUES are meaningless; the COLUMN SET is the measurement. What this gate
    checks is which names reach a reader, and the names are the view's.
    """
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({name: _column_values(name, n, rng) for name in columns})
    if "claim_sk" in frame.columns:
        frame["claim_sk"] = np.arange(1, n + 1)
    if "clm_id" in frame.columns:
        frame["clm_id"] = [f"-{10000930037831 + i}" for i in range(n)]
    if "submission_year_month" in frame.columns:
        frame["submission_year_month"] = [f"2023-{(i % 12) + 1:02d}" for i in range(n)]
    return frame
