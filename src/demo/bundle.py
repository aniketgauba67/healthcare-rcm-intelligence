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
import hashlib
import json
import os
import pathlib
import stat
import threading
from dataclasses import dataclass
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

# The currently committed demo artifact. Final Phase 5 acceptance requires a
# clean-SHA rebuild, and that rebuild must update this pin in the same commit as
# the DuckDB file. An override exists for an explicitly configured replacement.
EXPECTED_BUNDLE_SHA256 = "ef9d8013d84f74133153033a5e68f950cf51cc5e1e559cf80175f93a94c3e7e0"
BUNDLE_SHA256_ENV = "RCM_DEMO_BUNDLE_SHA256"
MAX_READINESS_BUNDLE_BYTES = 64 * 1024 * 1024

_BUILD_INFO_COLUMNS = frozenset(
    {
        "git_commit",
        "git_branch",
        "git_tree_dirty",
        "built_at_utc",
        "source_vintages",
        "dataset_names",
        "contains_simulated",
        "notice",
    }
)
_MANIFEST_COLUMNS = frozenset(
    {
        "dataset",
        "provenance",
        "contains_simulated",
        "grain",
        "rows",
        "columns",
        "simulated_columns",
        "note",
    }
)


class BundleNotFoundError(FileNotFoundError):
    """Raised with the command that produces the bundle, not just the path."""


class BundleReadinessError(RuntimeError):
    """The configured on-disk bundle is not the pinned, self-describing artifact."""


@dataclass(frozen=True)
class BundleReadiness:
    """Identity returned after a fresh, independent readiness validation."""

    path: pathlib.Path
    sha256: str
    datasets: tuple[str, ...]
    git_commit: str


def bundle_path() -> pathlib.Path:
    """The bundle this process should read."""
    override = os.environ.get(BUNDLE_PATH_ENV)
    return pathlib.Path(override).expanduser().resolve() if override else DEFAULT_BUNDLE_PATH


def bundle_exists(path: pathlib.Path | None = None) -> bool:
    return (path or bundle_path()).is_file()


def expected_bundle_sha256() -> str:
    """The artifact identity readiness must observe at the configured path."""
    return os.environ.get(BUNDLE_SHA256_ENV, EXPECTED_BUNDLE_SHA256).strip().lower()


def _sha256_bounded(path: pathlib.Path, size: int) -> str:
    if size <= 0:
        raise BundleReadinessError(f"demo bundle is empty: {path}")
    if size > MAX_READINESS_BUNDLE_BYTES:
        raise BundleReadinessError(
            f"demo bundle is {size} bytes, above the {MAX_READINESS_BUNDLE_BYTES}-byte "
            "readiness limit"
        )

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise BundleReadinessError(f"demo bundle is not readable: {path}: {error}") from error
    return digest.hexdigest()


def _column_names(connection: Any, table: str) -> set[str]:
    return {str(row[0]) for row in connection.execute(f'describe "{table}"').fetchall()}


def validate_bundle_readiness(
    path: pathlib.Path | None = None,
    *,
    expected_sha256: str | None = None,
) -> BundleReadiness:
    """Validate the configured artifact through a new read-only connection.

    This deliberately does not call `open_bundle()` or `get_source()`: an
    already-open DuckDB descriptor remains usable after its pathname is deleted
    on Unix. Readiness is a claim about what a new process could open now, not
    what this process opened earlier.
    """
    from src.demo import spec

    resolved = (path or bundle_path()).expanduser().resolve()
    try:
        metadata = resolved.stat()
    except OSError as error:
        raise BundleReadinessError(
            f"demo bundle path is unavailable: {resolved}: {error}"
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise BundleReadinessError(f"demo bundle path is not a regular file: {resolved}")
    if not os.access(resolved, os.R_OK):
        raise BundleReadinessError(f"demo bundle path is not readable: {resolved}")

    observed_sha256 = _sha256_bounded(resolved, metadata.st_size)
    required_sha256 = (expected_sha256 or expected_bundle_sha256()).strip().lower()
    if len(required_sha256) != 64 or any(c not in "0123456789abcdef" for c in required_sha256):
        raise BundleReadinessError(
            "configured demo bundle SHA-256 is not a 64-character hex digest"
        )
    if observed_sha256 != required_sha256:
        raise BundleReadinessError(
            "demo bundle fingerprint mismatch: "
            f"expected {required_sha256}, observed {observed_sha256} at {resolved}"
        )

    expected_datasets = set(spec.DATASETS_BY_NAME)
    connection = None
    try:
        import duckdb

        connection = duckdb.connect(str(resolved), read_only=True)
        actual_datasets = {str(row[0]) for row in connection.execute("show tables").fetchall()}
        if actual_datasets != expected_datasets:
            missing = sorted(expected_datasets - actual_datasets)
            unexpected = sorted(actual_datasets - expected_datasets)
            raise BundleReadinessError(
                f"demo bundle inventory mismatch: missing={missing}, unexpected={unexpected}"
            )

        build_columns = _column_names(connection, BUILD_INFO_TABLE)
        missing_build_columns = sorted(_BUILD_INFO_COLUMNS - build_columns)
        if missing_build_columns:
            raise BundleReadinessError(
                f"{BUILD_INFO_TABLE} is missing columns: {missing_build_columns}"
            )
        build_rows = connection.execute(
            f'select git_commit, dataset_names from "{BUILD_INFO_TABLE}"'
        ).fetchall()
        if len(build_rows) != 1 or not str(build_rows[0][0]).strip():
            raise BundleReadinessError(f"{BUILD_INFO_TABLE} must contain one pinned build row")
        try:
            stamped_datasets = set(json.loads(build_rows[0][1]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BundleReadinessError(
                f"{BUILD_INFO_TABLE}.dataset_names is not valid JSON"
            ) from error
        if stamped_datasets != expected_datasets:
            raise BundleReadinessError(
                f"{BUILD_INFO_TABLE}.dataset_names does not match the declared bundle inventory"
            )

        manifest_columns = _column_names(connection, MANIFEST_TABLE)
        missing_manifest_columns = sorted(_MANIFEST_COLUMNS - manifest_columns)
        if missing_manifest_columns:
            raise BundleReadinessError(
                f"{MANIFEST_TABLE} is missing columns: {missing_manifest_columns}"
            )
        manifest_datasets = [
            str(row[0])
            for row in connection.execute(f'select dataset from "{MANIFEST_TABLE}"').fetchall()
        ]
        if len(manifest_datasets) != len(set(manifest_datasets)):
            raise BundleReadinessError(f"{MANIFEST_TABLE} contains duplicate dataset rows")
        if set(manifest_datasets) != expected_datasets:
            raise BundleReadinessError(
                f"{MANIFEST_TABLE} does not match the declared bundle inventory"
            )

        for dataset in sorted(expected_datasets):
            connection.execute(f'select * from "{dataset}" limit 0')
    except BundleReadinessError:
        raise
    except Exception as error:
        raise BundleReadinessError(
            f"demo bundle could not be validated as read-only DuckDB: {type(error).__name__}: {error}"
        ) from error
    finally:
        if connection is not None:
            connection.close()

    return BundleReadiness(
        path=resolved,
        sha256=observed_sha256,
        datasets=tuple(sorted(expected_datasets)),
        git_commit=str(build_rows[0][0]),
    )


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
        metadata = path.stat()
        artifact_sha256 = _sha256_bounded(path, metadata.st_size)

        import duckdb

        self.path = path
        self.artifact_sha256 = artifact_sha256
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
