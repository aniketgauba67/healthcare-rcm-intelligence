"""Reading the bundled demo extract.

This is the read side of the locked decision in CLAUDE.md §2 — the hosted demo
runs off a bundled DuckDB extract, not live Postgres. Opening a bundle needs no
database, no network, no credentials and no environment variables, which is the
whole point: it is what makes the app deployable to Streamlit Community Cloud or
a Hugging Face Space from a clean clone.

WHY READ-ONLY, AND WHY THAT IS NOT A DETAIL
-------------------------------------------
The connection is opened `read_only=True`. On a hosted demo several viewers share
one process and a writable handle would let any page mutate the shipped evidence;
on a developer's machine it is the difference between a dashboard that reads the
warehouse extract and one that can quietly become a second, divergent copy of it.
The bundle is an artifact of `make demo-extract` and nothing else may write it.

CACHING
-------
`open_bundle()` memoises per resolved path. DuckDB handles are cheap but the
5-second load budget for free-tier hosting is not, and Streamlit re-executes the
whole script on every interaction — so a fresh connection per rerun would pay the
open cost on every click.

ONE HANDLE, ONE CURSOR PER THREAD — AND WHY THIS IS NOT A MICRO-OPTIMISATION
---------------------------------------------------------------------------
A memoised handle is shared, and Streamlit runs every script run on a ScriptRunner
thread — so on the hosted demo, where several viewers share one process (the
sentence three paragraphs up), concurrent reads land on ONE connection. A
`DuckDBPyConnection` holds the result set of its last `execute` ON THE CONNECTION,
so two threads interleaving `execute` / `fetch_df` do not queue: they overwrite
each other. Measured on this bundle, an 8-thread read produced all three of
`fetch_df()` returning **None**, `show tables` missing a table that IS present —
surfacing to a user as the flatly wrong diagnosis "the bundle predates the
declaration; rebuild with make demo-extract" — and a hard **SIGSEGV** under
Streamlit's own test runner.

So every read goes through `_cursor()`, which hands each thread its own cursor via
`DuckDBPyConnection.cursor()` (DuckDB's documented pattern for concurrent reads of
one database). Cursors inherit the read-only mode, cost roughly nothing to make,
and are cached per thread so the 5-second budget is unaffected. The failure this
prevents is the worst kind for this project: not a crash but a page that renders
another query's result under its own heading, with the banner correctly displayed
above it.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
import threading
from typing import Any

import pandas as pd

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Where `make demo-extract` writes, and where the app looks by default. The path
#: is whitelisted in .gitignore (`!dashboard/demo_data/*.duckdb`) because this
#: file is COMMITTED — a hosted demo has no other way to get its data.
DEFAULT_BUNDLE_PATH = REPO_ROOT / "dashboard" / "demo_data" / "rcm_demo.duckdb"

#: Override for tests and for pointing a deployment at a bundle built elsewhere.
BUNDLE_PATH_ENV = "RCM_DEMO_BUNDLE"

MANIFEST_TABLE = "demo_manifest"
BUILD_INFO_TABLE = "demo_build_info"


class BundleNotFoundError(FileNotFoundError):
    """Raised with the command that produces the bundle, not just the path."""


def bundle_path() -> pathlib.Path:
    """The bundle this process should read."""
    override = os.environ.get(BUNDLE_PATH_ENV)
    return pathlib.Path(override).expanduser().resolve() if override else DEFAULT_BUNDLE_PATH


def bundle_exists(path: pathlib.Path | None = None) -> bool:
    return (path or bundle_path()).is_file()


class Bundle:
    """A read-only handle on one demo extract."""

    def __init__(self, path: pathlib.Path) -> None:
        if not path.is_file():
            raise BundleNotFoundError(
                f"no demo bundle at {path}. Build one with `make demo-extract` (needs the "
                f"PostgreSQL warehouse and the model artifacts), or point {BUNDLE_PATH_ENV} at "
                "an existing bundle. The hosted demo ships this file committed; a clean clone "
                "should already have it."
            )
        import duckdb

        self.path = path
        self._connection = duckdb.connect(str(path), read_only=True)
        self._local = threading.local()

    def _cursor(self):  # noqa: ANN202 - duckdb.DuckDBPyConnection
        """This thread's own cursor on the bundle. See the module docstring.

        Never read through `self._connection` directly: it is the one object whose
        result set concurrent readers overwrite.
        """
        cursor = getattr(self._local, "cursor", None)
        if cursor is None:
            cursor = self._connection.cursor()
            self._local.cursor = cursor
        return cursor

    # -- introspection ------------------------------------------------------

    def table_names(self) -> list[str]:
        rows = self._cursor().execute("show tables").fetchall()
        return sorted(row[0] for row in rows)

    def has_table(self, name: str) -> bool:
        return name in set(self.table_names())

    def columns(self, name: str) -> list[str]:
        return list(self.query(f'select * from "{name}" limit 0').columns)

    def row_count(self, name: str) -> int:
        return int(self._cursor().execute(f'select count(*) from "{name}"').fetchone()[0])

    # -- reading ------------------------------------------------------------

    def query(self, sql: str, parameters: object = None) -> pd.DataFrame:
        """Run read-only SQL against the bundle."""
        connection = self._cursor()
        cursor = connection.execute(sql, parameters) if parameters else connection.execute(sql)
        return cursor.fetch_df()

    def table(self, name: str) -> pd.DataFrame:
        """A whole declared dataset.

        The name is checked against `src/demo/spec.py` rather than passed
        through, so a typo names the datasets that exist instead of producing a
        DuckDB parse error three frames down.
        """
        from src.demo import spec

        spec.dataset(name)
        if not self.has_table(name):
            raise KeyError(
                f"{name!r} is declared in src/demo/spec.py but is not in the bundle at "
                f"{self.path}. The bundle predates the declaration; rebuild with "
                "`make demo-extract`."
            )
        return self.query(f'select * from "{name}"')

    def manifest(self) -> pd.DataFrame:
        """The bundle's own provenance register, one row per dataset."""
        return self.query(f'select * from "{MANIFEST_TABLE}" order by dataset')

    def build_info(self) -> dict[str, Any]:
        """Git commit, build timestamp, source vintages, tree state."""
        frame = self.query(f'select * from "{BUILD_INFO_TABLE}"')
        if frame.empty:
            return {}
        row = frame.iloc[0].to_dict()
        for key in ("source_vintages", "dataset_names"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
        return row

    def model_metrics(self, model: str) -> dict[str, Any]:
        """One model's metrics.json, exactly as the training run wrote it."""
        frame = self.query('select metrics_json from "model_metrics" where model = ?', [model])
        if frame.empty:
            available = sorted(self.query('select model from "model_metrics"')["model"])
            raise KeyError(f"no metrics for model {model!r} in the bundle. Available: {available}")
        return json.loads(frame.iloc[0]["metrics_json"])

    def close(self) -> None:
        self._connection.close()


@functools.lru_cache(maxsize=4)
def _open(path_str: str) -> Bundle:
    return Bundle(pathlib.Path(path_str))


def open_bundle(path: pathlib.Path | None = None) -> Bundle:
    """The process-wide handle on the demo bundle."""
    return _open(str(path or bundle_path()))


def reset_cache() -> None:
    """Drop memoised handles. For tests that build a bundle and read it back."""
    _open.cache_clear()
