"""Integration-test ordering, and a baseline snapshot of what the run destroys.

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

THE SAME FAILURE, ONE LAYER OUT (found 2026-07-27; the drift it caused was
repaired by hand that morning and had already survived a phase acceptance).
`apply_ddl` does not only cascade into the sim_ layer. It also:

  * CASCADE-drops all 9 `rcm.vw_*` analytics views, because they read the star
    schema it recreates, and
  * recreates `dim_drg` empty, discarding the REFERENCE enrichment that
    `make reference-codes` wrote into `dim_drg.drg_desc` (167 of 168 rows).

Nothing in the suite put either back, and `test_end_state.py` asserted only the
sim_ layer — so a full `pytest -q` degraded the warehouse and still reported
green. That is the precise shape of the bug: not a test that fails wrongly, but
a suite that succeeds over damage it caused.

The fix has two halves and needs both. `test_warehouse_restore.py` (rank 80)
puts the views and the enrichment back, so the suite is no longer destructive.
`test_end_state.py` (rank 90) then asserts they are actually back, so a restore
that silently fails is loud instead of invisible. Restoring without asserting
would just move the blind spot; asserting without restoring would turn
`make test` permanently red on any developer machine holding a populated
warehouse, and a guard that can never go green gets deleted.

Both halves compare against `warehouse_baseline` below, captured BEFORE the
first integration test runs. That is deliberate: the property being enforced is
"the suite left the warehouse no worse than it found it", not "the warehouse is
fully materialised". Starting with no views is a legitimate state (a fresh
clone) and must not fail. Starting with 9 views and ending with 8 is the defect
and must. Because the precondition is observed rather than assumed, this guard
cannot quietly excuse itself the way a bare "skip if absent" can.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    # Repair: rebuild the materializations the rebuild above CASCADE-dropped,
    # before anything asserts on them.
    "test_warehouse_restore": 80,
    # The live leakage boundary reads the FULL rcm catalog, views included: some
    # forbidden columns it resolves are DERIVED columns that exist only in
    # sql/views/. Run it after the repair, or it measures a half-built warehouse
    # and reports view-derived patterns as dead when they are merely dropped.
    "test_live_leakage": 85,
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


# ---------------------------------------------------------------------------
# Pre-run snapshot of the materializations that `apply_ddl` destroys.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WarehouseBaseline:
    """What the analytics and REFERENCE layers looked like before the suite ran.

    `reachable` is False when there is no database to snapshot (no `.env`, or the
    container is down). The restore and the end-state assertions then have
    nothing to compare against and stand down — with no database, the suite is
    not destroying anything it would need to put back.
    """

    reachable: bool = False
    # view name -> the SELECT body from pg_get_viewdef. Used only as a fallback
    # if the shipped sql/views/*.sql cannot be re-applied.
    views: dict[str, str] = field(default_factory=dict)
    # dim_drg natural key (drg_cd) -> (drg_desc, provenance). Keyed on drg_cd and
    # deliberately NOT on drg_key, which is a surrogate the reload reassigns.
    drg_desc: dict[str, tuple[str, str]] = field(default_factory=dict)
    # rcm table -> its column names, for the sim_-prefixed tables. Used to detect a
    # run that rewrote the warehouse into an older schema shape.
    sim_table_columns: dict[str, set[str]] = field(default_factory=dict)


_VIEW_SQL = """
select table_name, pg_get_viewdef(('rcm.' || quote_ident(table_name))::regclass, true)
from information_schema.views
where table_schema = 'rcm'
"""

_DRG_SQL = """
select drg_cd, drg_desc, provenance
from rcm.dim_drg
where drg_desc is not null
"""

_SIM_COLUMNS_SQL = """
select table_name, column_name
from information_schema.columns
where table_schema = 'rcm' and table_name like 'sim\\_%'
"""


@pytest.fixture(scope="session", autouse=True)
def warehouse_baseline() -> WarehouseBaseline:
    """Capture the view layer and the dim_drg enrichment before anything drops them.

    Autouse and session-scoped, so it runs once, ahead of the rank-10 module that
    does the dropping. Every failure mode here degrades to an empty baseline
    rather than an error: a snapshot that cannot be taken must not be able to
    fail the run it exists to protect.
    """
    try:
        from sqlalchemy import create_engine, text

        from src.ingestion.load_postgres import database_url

        url = database_url()
        if not url:
            return WarehouseBaseline()
        engine = create_engine(url)
        with engine.connect() as conn:
            views = {name: body for name, body in conn.execute(text(_VIEW_SQL))}
            drg = {cd: (desc, prov) for cd, desc, prov in conn.execute(text(_DRG_SQL))}
            sim_columns: dict[str, set[str]] = {}
            for table, column in conn.execute(text(_SIM_COLUMNS_SQL)):
                sim_columns.setdefault(table, set()).add(column)
        engine.dispose()
    except Exception:  # noqa: BLE001 - unreachable DB, missing schema, driver error
        return WarehouseBaseline()
    return WarehouseBaseline(
        reachable=True, views=views, drg_desc=drg, sim_table_columns=sim_columns
    )
