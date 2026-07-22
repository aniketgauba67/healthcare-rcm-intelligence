"""Live Postgres acceptance test for the SIMULATED adjudication layer (Phase 2).

Applies sql/ddl/50_sim_adjudication.sql, loads the generated frames, and runs
the shared check SQL against real PostgreSQL 16 — the same statements the DuckDB
mirror runs, so the two engines cannot drift. Marked `integration` so CI unit
runs skip it:

    make validate-warehouse        # or: uv run pytest -m integration -q

Requires the star schema to be loaded first (`make warehouse`), because the sim
tables carry foreign keys into rcm.fact_inpatient_claim.
"""

from __future__ import annotations

import pytest

from src.ingestion.load_postgres import database_url
from src.ingestion.paths import DATA_VALIDATED
from src.simulation.config import load_config
from src.simulation.generator import generate
from src.simulation.load_sim import (
    WarehouseNotReady,
    _assert_warehouse_ready,
    apply_ddl,
    load_frames,
    validate_postgres,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_engine():
    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any driver/connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")
    return engine


@pytest.fixture(scope="module")
def sim_result():
    if not (DATA_VALIDATED / "inpatient.parquet").exists():
        pytest.skip("validated Parquet missing; run `make ingest && make stage` first")
    return generate(load_config())


def _load_once(engine, result):
    apply_ddl(engine)  # idempotent: drops + recreates every sim table
    load_frames(engine, result)
    return validate_postgres(engine, result)


def test_simulation_layer_acceptance_and_idempotency(pg_engine, sim_result):
    """Load into live Postgres, assert every check, then re-load and confirm the
    counts are unchanged."""
    try:
        _assert_warehouse_ready(pg_engine, sim_result)
    except WarehouseNotReady as exc:
        pytest.skip(f"star schema not loaded: {exc}")

    checks = _load_once(pg_engine, sim_result)
    failed = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    assert not failed, f"live-Postgres simulation checks failed: {failed}"
    # FKs + grain + coverage + money + temporal + outcome + cost + provenance + counts.
    assert len(checks) >= 40
    counts = {c.name: c.detail for c in checks if c.name.startswith("count:")}

    pg_engine.dispose()

    recheck = _load_once(pg_engine, sim_result)
    assert not [c.name for c in recheck if not c.passed]
    assert counts == {c.name: c.detail for c in recheck if c.name.startswith("count:")}, (
        "re-load changed row counts (not idempotent)"
    )


def test_loader_refuses_a_warehouse_it_does_not_match(pg_engine, sim_result):
    """The ordering guard, exercised rather than merely documented.

    `make warehouse` drops fact_inpatient_claim with CASCADE — dropping the sim
    tables' foreign keys and leaving orphan rows behind — and claim_sk is
    assigned positionally, so a source reload can renumber it. Both failures are
    silent, so the loader has to catch them.
    """
    from dataclasses import replace

    mismatched = replace(
        sim_result,
        tables={
            **sim_result.tables,
            "sim_claim_adjudication": sim_result.table("sim_claim_adjudication").iloc[:-1],
        },
    )
    with pytest.raises(WarehouseNotReady, match="does not match"):
        _assert_warehouse_ready(pg_engine, mismatched)
