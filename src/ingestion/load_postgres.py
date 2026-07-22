"""Load the star schema into PostgreSQL (idempotent), with reconciliation.

`make warehouse` runs the live load: it applies sql/ddl/ (which drops and
recreates every table, so the load is fully re-runnable), bulk-loads the
dimension and fact frames, and reconciles row counts post-load.

Because the DDL drop/recreate + reload is idempotent, and because a live
Postgres is not always available (e.g. no Docker), `--offline-check` builds the
same frames and runs the full integrity + reconciliation suite via DuckDB — the
same logical checks, no database required.
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from .logging_utils import get_logger, log_event
from .paths import DATA_VALIDATED, REPO_ROOT
from .star_transform import StarFrames, build_star
from .warehouse_checks import reconcile_to_source, run_integrity_checks

_LOGGER = get_logger("ingestion.warehouse")

_DDL_FILES = ("00_schema.sql", "10_dimensions.sql", "20_facts.sql")
_SCHEMA = "rcm"
_LOAD_ORDER_DIMS = (
    "dim_date",
    "dim_beneficiary",
    "dim_provider",
    "dim_drg",
    "dim_discharge_status",
)
_LOAD_ORDER_FACTS = (
    "fact_inpatient_claim",
    "fact_claim_revenue_line",
    "fact_claim_diagnosis",
)


def database_url() -> str | None:
    """Build the SQLAlchemy URL from env, or return None if unconfigured."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.environ.get("POSTGRES_USER")
    pwd = os.environ.get("POSTGRES_PASSWORD")
    db = os.environ.get("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    if not (user and db):
        return None
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"


def apply_ddl(engine) -> None:
    """Execute the DDL files in order (idempotent drop/recreate)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for fname in _DDL_FILES:
            sql = (REPO_ROOT / "sql" / "ddl" / fname).read_text()
            conn.execute(text(sql))
            log_event(_LOGGER, "warehouse.ddl_applied", file=fname)


def load_frames(engine, frames: StarFrames) -> None:
    """Bulk-load dimension then fact frames into the DDL-created tables.

    Dimensions already contain their Unknown member (key 0) from the DDL insert,
    so those rows are skipped here to avoid a duplicate-key collision.
    """
    for name in _LOAD_ORDER_DIMS:
        df = frames.dims[name]
        key = df.columns[0]
        df.loc[df[key] != 0].to_sql(
            name,
            engine,
            schema=_SCHEMA,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )
        log_event(_LOGGER, "warehouse.loaded", table=name, rows=int((df[key] != 0).sum()))
    for name in _LOAD_ORDER_FACTS:
        df = frames.facts[name]
        df.to_sql(
            name,
            engine,
            schema=_SCHEMA,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )
        log_event(_LOGGER, "warehouse.loaded", table=name, rows=int(len(df)))


def reconcile_postgres(engine, frames: StarFrames) -> bool:
    """Post-load: confirm warehouse counts match the transformed frames."""
    from sqlalchemy import text

    ok = True
    with engine.connect() as conn:
        for name in (*_LOAD_ORDER_DIMS, *_LOAD_ORDER_FACTS):
            expected = len(frames.dims.get(name, frames.facts.get(name)))
            actual = conn.execute(text(f"select count(*) from {_SCHEMA}.{name}")).scalar()
            match = actual == expected
            ok = ok and match
            log_event(
                _LOGGER,
                "warehouse.reconcile",
                table=name,
                expected=expected,
                actual=actual,
                match=match,
            )
    return ok


def run_offline_check(frames: StarFrames) -> int:
    """Run the integrity + reconciliation suite via DuckDB (no database)."""
    ip = pd.read_parquet(DATA_VALIDATED / "inpatient.parquet")
    checks = run_integrity_checks(frames) + reconcile_to_source(
        frames, raw_inpatient_lines=len(ip), source_distinct_claims=ip["CLM_ID"].nunique()
    )
    failed = [c for c in checks if not c.passed]
    for c in checks:
        log_event(
            _LOGGER,
            "warehouse.check",
            name=c.name,
            status="PASS" if c.passed else "FAIL",
            detail=c.detail,
        )
    log_event(
        _LOGGER,
        "warehouse.check_summary",
        total=len(checks),
        failed=len(failed),
        reconciliation=frames.reconciliation,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load star schema into PostgreSQL.")
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Skip Postgres; run integrity + reconciliation via DuckDB.",
    )
    args = parser.parse_args(argv)

    frames = build_star()

    if args.offline_check:
        return run_offline_check(frames)

    url = database_url()
    if url is None:
        log_event(
            _LOGGER,
            "warehouse.no_db",
            hint="set POSTGRES_* in .env and run `docker compose up`, "
            "or use --offline-check for a DB-free integrity run",
        )
        return 2

    from sqlalchemy import create_engine

    engine = create_engine(url)
    apply_ddl(engine)
    load_frames(engine, frames)
    ok = reconcile_postgres(engine, frames)
    log_event(_LOGGER, "warehouse.done", reconciled=ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
