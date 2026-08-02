"""One read layer for the dashboard and the API.

Two backends, one interface:

* **bundle** (the default) — the committed DuckDB extract. No database, no
  network, no credentials. This is what a hosted demo and a clean-clone
  `docker compose up` both run on, and CLAUDE.md §2 locks the hosted demo to it.
* **postgres** — the live warehouse, for a developer who has loaded one and wants
  the app pointed at it.

`RCM_DATA_SOURCE` selects, defaulting to `bundle`. The default matters: an app
that silently prefers a database it can find would behave differently on the
maintainer's machine than on the deployment, and the deployment is the surface
everybody else sees.

WHY THE TWO BACKENDS CANNOT DRIFT
---------------------------------
The bundle's tables are `select * from` copies of the same `vw_` views, written
by `src/demo/build.py`, so both backends answer with the same columns from the
same definition. The dashboard never recomputes a KPI, which is what makes §7's
"dashboard totals reconcile to SQL control queries" true by construction rather
than by anybody remembering to check.

The one real difference is that the bundle also holds model datasets that have no
warehouse equivalent (model scores, reason codes, the Model C queue, the metrics
each run wrote). `available()` reports what a source can answer, so a page can
say "this needs the demo bundle" rather than raising.
"""

from __future__ import annotations

import functools
import os
from typing import Protocol

import pandas as pd

from src.demo import spec
from src.demo.bundle import Bundle, bundle_exists, open_bundle

SOURCE_ENV = "RCM_DATA_SOURCE"
BUNDLE = "bundle"
POSTGRES = "postgres"


class DataSourceError(RuntimeError):
    """A read that cannot be served, said with what would fix it."""


class DataSource(Protocol):
    """What a page or an endpoint is allowed to ask for."""

    kind: str

    def frame(self, dataset: str) -> pd.DataFrame: ...

    def available(self) -> set[str]: ...

    def describe(self) -> dict[str, object]: ...


class BundleSource:
    """Reads the committed DuckDB extract."""

    kind = BUNDLE

    def __init__(self, bundle: Bundle | None = None) -> None:
        self._bundle = bundle or open_bundle()

    @property
    def bundle(self) -> Bundle:
        return self._bundle

    def frame(self, dataset: str) -> pd.DataFrame:
        return self._bundle.table(dataset)

    def available(self) -> set[str]:
        return set(self._bundle.table_names()) & set(spec.DATASETS_BY_NAME)

    def describe(self) -> dict[str, object]:
        info = self._bundle.build_info()
        return {
            "kind": self.kind,
            "path": str(self._bundle.path),
            "artifact_sha256": self._bundle.artifact_sha256,
            "git_commit": info.get("git_commit", "unknown"),
            "git_tree_dirty": bool(info.get("git_tree_dirty", False)),
            "built_at_utc": info.get("built_at_utc", "unknown"),
            "source_vintages": info.get("source_vintages", {}),
        }


class PostgresSource:
    """Reads the live warehouse views directly.

    Only the warehouse-backed datasets are answerable here; the model datasets
    are products of a training run and live in the bundle. That is stated by
    `available()` rather than discovered by a page as a missing-table error.
    """

    kind = POSTGRES

    def __init__(self, url: str | None = None) -> None:
        from sqlalchemy import create_engine

        from src.ingestion.load_postgres import database_url

        resolved = url or database_url()
        if not resolved:
            raise DataSourceError(
                f"{SOURCE_ENV}={POSTGRES} but no database URL is configured. Set POSTGRES_* or "
                f"DATABASE_URL in .env, or unset {SOURCE_ENV} to read the bundled demo extract."
            )
        self._engine = create_engine(resolved)

    def frame(self, dataset: str) -> pd.DataFrame:
        declared = spec.dataset(dataset)
        if not declared.from_warehouse:
            raise DataSourceError(
                f"{dataset!r} is produced by a training run, not by the warehouse, so it is "
                f"only available from the demo bundle. Build one with `make demo-extract` and "
                f"unset {SOURCE_ENV}."
            )
        return pd.read_sql(declared.query, self._engine)

    def available(self) -> set[str]:
        return {dataset.name for dataset in spec.WAREHOUSE_DATASETS}

    def describe(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": str(self._engine.url).replace(self._engine.url.password or "", "***")
            if self._engine.url.password
            else str(self._engine.url),
            "artifact_sha256": "",
            "git_commit": "n/a (live warehouse)",
            "git_tree_dirty": False,
            "built_at_utc": "n/a (live warehouse)",
            "source_vintages": {},
        }


def configured_kind() -> str:
    """Which backend this process is configured for."""
    return (os.environ.get(SOURCE_ENV) or BUNDLE).strip().lower()


def build_source(kind: str | None = None) -> DataSource:
    """Construct the configured backend, with an error that says what to do."""
    resolved = (kind or configured_kind()).lower()
    if resolved == POSTGRES:
        return PostgresSource()
    if resolved != BUNDLE:
        raise DataSourceError(
            f"{SOURCE_ENV}={resolved!r} is not a data source. Use {BUNDLE!r} (the committed "
            f"DuckDB extract, the default) or {POSTGRES!r} (a loaded local warehouse)."
        )
    if not bundle_exists():
        raise DataSourceError(
            "no demo bundle to read. A clean clone ships one; build a fresh one with "
            f"`make demo-extract`, or set {SOURCE_ENV}={POSTGRES} to read a loaded warehouse."
        )
    return BundleSource()


@functools.lru_cache(maxsize=2)
def _cached(kind: str) -> DataSource:
    return build_source(kind)


def get_source() -> DataSource:
    """The process-wide data source. Cached: Streamlit reruns the script per click."""
    return _cached(configured_kind())


def reset_cache() -> None:
    """Drop the memoised source. For tests that switch backends."""
    _cached.cache_clear()
