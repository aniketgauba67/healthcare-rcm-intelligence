"""Final guard: the warehouse is left coherent after the integration run.

The integration suite is ordered by tests/integration/conftest.py so the Phase 1
warehouse test (which drop/recreates the star schema with CASCADE) runs BEFORE
the Phase 2 simulation test (which reattaches the sim_ layer with foreign keys
into that schema). If that ordering ever regresses, the simulation test runs
first and the warehouse test then CASCADE-drops the sim_ foreign keys, leaving
orphan rows behind — and every test still reports pass, so the damage is
invisible in the pytest output.

This module runs last (rank 90 in the conftest) and asserts the end state
directly: the sim_ layer is present, its foreign keys survived, and no sim_
adjudication row is orphaned from the fact table. It turns "a green run means the
database is fine" from an assumption into something the suite actually checks.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_SIM_TABLES = {
    "sim_payer": 5,
    "sim_service_line": 11,
    "sim_authorization_eligibility": None,
    "sim_documentation_coding": None,
    "sim_claim_adjudication": None,
    "sim_appeals": None,
    "sim_workflow_events": None,
    "sim_operating_costs": None,
}


def test_sim_layer_is_present_after_the_ordered_run(pg_engine_session):
    """Every sim_ table exists and is non-empty once the suite has finished."""
    with pg_engine_session.connect() as conn:
        for table, expected in _SIM_TABLES.items():
            count = conn.execute(text(f"select count(*) from rcm.{table}")).scalar()
            assert count and count > 0, f"rcm.{table} is empty — sim layer did not survive the run"
            if expected is not None:
                assert count == expected, f"rcm.{table}: {count} rows, expected {expected}"


def test_sim_foreign_keys_survived_the_star_schema_rebuild(pg_engine_session):
    """The CASCADE-drop corruption shows up here as missing FK constraints.

    The claim-grain sim_ tables and the appeals table each carry a foreign key
    into the schema the Phase 1 test rebuilds. If the ordering regressed, those
    constraints would have been dropped and never recreated.
    """
    required = {
        "sim_authorization_eligibility",
        "sim_documentation_coding",
        "sim_claim_adjudication",
        "sim_appeals",
        "sim_workflow_events",
        "sim_operating_costs",
    }
    with pg_engine_session.connect() as conn:
        rows = conn.execute(
            text(
                "select distinct table_name from information_schema.table_constraints "
                "where constraint_type = 'FOREIGN KEY' and table_schema = 'rcm' "
                "and table_name like 'sim_%'"
            )
        ).fetchall()
    with_fks = {r[0] for r in rows}
    missing = required - with_fks
    assert not missing, f"sim_ tables missing foreign keys (CASCADE-drop corruption): {missing}"


def test_no_orphan_sim_adjudication_rows(pg_engine_session):
    """Every simulated claim still points at a real fact_inpatient_claim row."""
    with pg_engine_session.connect() as conn:
        orphans = conn.execute(
            text(
                "select count(*) from rcm.sim_claim_adjudication a "
                "left join rcm.fact_inpatient_claim f on a.claim_sk = f.claim_sk "
                "where f.claim_sk is null"
            )
        ).scalar()
    assert orphans == 0, f"{orphans} sim_claim_adjudication rows orphaned from the fact table"
