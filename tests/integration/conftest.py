"""Integration-test ordering.

These tests share ONE live Postgres database and they are not independent: the
Phase 1 warehouse test calls `apply_ddl`, which drops rcm.fact_inpatient_claim
with CASCADE. That also drops the foreign keys pointing at it from every sim_
table, leaving the simulated layer holding orphan rows with no referential
integrity. Nothing raises when this happens — both tests still report pass — so
the damage is invisible in the pytest output and only shows up later, when
someone inspects a warehouse that a green `make validate-warehouse` said was
fine.

Default collection is alphabetical, which put test_simulation_postgres.py
(Phase 2) ahead of test_warehouse_postgres.py (Phase 1) and produced exactly
that corruption. Relying on filenames sorting into the correct dependency order
is not a property anyone can see or maintain, so the order is declared here
instead, and `test_end_state.py` asserts afterwards that the layer actually
survived. The hook is scoped to tests/integration/ and does not affect the unit
suite.
"""

from __future__ import annotations

import pytest

# Lower runs first. Ranks are spaced so a new module can be inserted between two
# existing ones without renumbering them.
_MODULE_ORDER: dict[str, int] = {
    # Phase 1. Rebuilds the star schema from scratch (drop/recreate, twice, for
    # its idempotency assertion). Anything that attaches to the star schema must
    # therefore run after it, never before.
    "test_warehouse_postgres": 10,
    # Phase 2. Attaches the sim_ layer, with foreign keys into the star schema
    # the module above rebuilds.
    "test_simulation_postgres": 20,
    # Final guard: the database is left coherent.
    "test_end_state": 90,
}

_UNRANKED = 50


def pytest_collection_modifyitems(session, config, items) -> None:  # noqa: ARG001
    """Order integration tests by declared dependency rank, not by filename."""

    def rank(item) -> int:
        return _MODULE_ORDER.get(item.module.__name__.rsplit(".", 1)[-1], _UNRANKED)

    items.sort(key=rank)


@pytest.fixture(scope="session")
def pg_engine_session():
    """A session-scoped engine for tests that inspect the end state of the run."""
    from src.ingestion.load_postgres import database_url

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
