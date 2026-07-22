"""Load the SIMULATED adjudication layer into the warehouse.

`make simulate-warehouse` applies sql/ddl/50_sim_adjudication.sql (drop/recreate,
so it is fully re-runnable) and bulk-loads the generated frames, then runs the
shared acceptance checks against live Postgres.

`--offline-check` runs the identical check SQL against a DuckDB mirror holding
the star schema plus the generated frames, so simulation integrity is covered in
CI without a database. Live Postgres remains the acceptance authority.

This module is deliberately separate from `src/ingestion/load_postgres.py`:
CLAUDE.md §5 gives that file to data-engineer, and the sim layer owns its own
load path. The cost of that separation is an ordering dependency, enforced
below rather than left to a README line — see `_assert_warehouse_ready`.
"""

from __future__ import annotations

import argparse

import pandas as pd

from src.ingestion.load_postgres import database_url
from src.ingestion.logging_utils import get_logger, log_event
from src.ingestion.paths import REPO_ROOT
from src.ingestion.star_transform import build_star
from src.ingestion.warehouse_checks import duckdb_star_connection
from src.ingestion.warehouse_sql_checks import CheckResult, run_count_reconciliation

from .config import load_config
from .generator import SimulationResult, generate
from .sim_sql_checks import SIM_TABLES, run_sim_violation_checks

_LOGGER = get_logger("simulation.warehouse")
_DDL_FILE = "50_sim_adjudication.sql"
_SCHEMA = "rcm"
# Load order respects the FKs: dimensions, then the hub, then its dependents.
_LOAD_ORDER = (
    "sim_payer",
    "sim_service_line",
    "sim_claim_adjudication",
    "sim_authorization_eligibility",
    "sim_documentation_coding",
    "sim_appeals",
    "sim_workflow_events",
    "sim_operating_costs",
)


class WarehouseNotReady(RuntimeError):
    """The star schema the sim layer attaches to is missing or has moved."""


def _assert_warehouse_ready(engine, result: SimulationResult) -> None:
    """Refuse to load against a warehouse the generator did not see.

    `make warehouse` drops rcm.fact_inpatient_claim with CASCADE, which also
    drops the FK constraints pointing at it from the sim tables — leaving them
    silently holding orphan rows. And because claim_sk is assigned positionally
    by star_transform, a reload of different source data can renumber it, so
    matching row counts alone would not prove the keys still line up. Both
    failure modes are quiet, so check for them loudly here.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "select count(*) from information_schema.tables "
                "where table_schema = :s and table_name = 'fact_inpatient_claim'"
            ),
            {"s": _SCHEMA},
        ).scalar()
        if not exists:
            raise WarehouseNotReady(
                f"{_SCHEMA}.fact_inpatient_claim not found — run `make warehouse` first, "
                "then `make simulate-warehouse`."
            )
        warehouse_keys = conn.execute(
            text(f"select count(*), coalesce(sum(claim_sk), 0) from {_SCHEMA}.fact_inpatient_claim")
        ).one()

    adjudication = result.table("sim_claim_adjudication")
    generated = (len(adjudication), int(adjudication["claim_sk"].sum()))
    if tuple(int(v) for v in warehouse_keys) != generated:
        raise WarehouseNotReady(
            "claim_sk in the warehouse does not match the generated layer "
            f"(warehouse count/checksum {tuple(int(v) for v in warehouse_keys)} vs generated "
            f"{generated}). The star schema was rebuilt after this simulation ran — "
            "re-run `make simulate`, then `make simulate-warehouse`."
        )


def apply_ddl(engine) -> None:
    from sqlalchemy import text  # noqa: F401  (kept for symmetry with the ingestion loader)

    sql = (REPO_ROOT / "sql" / "ddl" / _DDL_FILE).read_text()
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    log_event(_LOGGER, "simulation.ddl_applied", file=_DDL_FILE)


def _for_sql(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce pandas nullable extension dtypes psycopg2 cannot bind directly."""
    out = df.copy()
    for column, dtype in out.dtypes.items():
        if str(dtype) in {"Int64", "Int32", "Int16"}:
            out[column] = out[column].astype("object").where(out[column].notna(), None)
        elif str(dtype) == "string":
            out[column] = out[column].astype("object").where(out[column].notna(), None)
    return out


def load_frames(engine, result: SimulationResult) -> None:
    for name in _LOAD_ORDER:
        df = _for_sql(result.table(name))
        df.to_sql(
            name,
            engine,
            schema=_SCHEMA,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )
        log_event(_LOGGER, "simulation.loaded", table=name, rows=int(len(df)))


def validate_postgres(engine, result: SimulationResult) -> list[CheckResult]:
    from sqlalchemy import text

    with engine.connect() as conn:

        def scalar(sql: str) -> int:
            return conn.execute(text(sql)).scalar()

        checks = run_sim_violation_checks(scalar, f"{_SCHEMA}.")
        checks += run_count_reconciliation(
            scalar,
            {name: int(len(result.table(name))) for name in SIM_TABLES},
            f"{_SCHEMA}.",
        )
    return checks


def run_offline_check(result: SimulationResult) -> list[CheckResult]:
    """The identical check SQL against a DuckDB mirror — CI parity, no database."""
    con = duckdb_star_connection(build_star(), result.tables)

    def scalar(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    checks = run_sim_violation_checks(scalar, "rcm.")
    checks += run_count_reconciliation(
        scalar, {name: int(len(result.table(name))) for name in SIM_TABLES}, "rcm."
    )
    con.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the simulated layer into the warehouse.")
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Skip Postgres; run the same integrity SQL via DuckDB.",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    result = generate(cfg)
    log_event(_LOGGER, "simulation.generated", config_version=cfg.version, seed=cfg.seed)

    if args.offline_check:
        checks = run_offline_check(result)
    else:
        url = database_url()
        if url is None:
            log_event(
                _LOGGER,
                "simulation.no_db",
                hint="set POSTGRES_* in .env and run `docker compose up`, "
                "or use --offline-check for a DB-free integrity run",
            )
            return 2

        from sqlalchemy import create_engine

        engine = create_engine(url)
        try:
            _assert_warehouse_ready(engine, result)
        except WarehouseNotReady as exc:
            log_event(_LOGGER, "simulation.warehouse_not_ready", error=str(exc))
            return 2
        apply_ddl(engine)
        load_frames(engine, result)
        checks = validate_postgres(engine, result)

    failed = [c for c in checks if not c.passed]
    for check in checks:
        log_event(
            _LOGGER,
            "simulation.check",
            name=check.name,
            status="PASS" if check.passed else "FAIL",
            detail=check.detail,
        )
    log_event(
        _LOGGER,
        "simulation.warehouse_done",
        mode="duckdb" if args.offline_check else "postgres",
        checks=len(checks),
        failed=len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
