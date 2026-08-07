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

That guard originally covered the sim_ layer ONLY, and the same CASCADE took two
other things with it that nothing here looked at: the 9 `rcm.vw_*` analytics
views and the `dim_drg.drg_desc` REFERENCE enrichment. Both were lost by a
routine `pytest -q`, both restored by hand on 2026-07-27, and a Phase 3
acceptance had already been signed off against the degraded database in between.
The two tests at the bottom of this module close that hole. They compare against
`warehouse_baseline` (captured before the first integration test ran), so they
enforce "no worse than we found it" rather than "fully materialised" — a fresh
clone with no views is not a failure, losing views that were there is.
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


_REPAIR = "make reference-codes && make views  # then re-check 21/21"


def test_the_analytics_view_layer_survived_the_run(pg_engine_session, warehouse_baseline):
    """Every view that existed before the run still exists after it.

    `apply_ddl` CASCADE-drops these because they read the star schema it
    recreates. Nothing raises when it happens, so before this assertion existed a
    full `pytest -q` could delete the entire analytics layer and still print
    "passed". Restoration is rank 80; this is the check that it worked.
    """
    if not warehouse_baseline.reachable:
        pytest.skip("no database was reachable to snapshot before the run")
    if not warehouse_baseline.views:
        pytest.skip("no views existed before the run — nothing could be lost")

    with pg_engine_session.connect() as conn:
        live = {
            r[0]
            for r in conn.execute(
                text("select table_name from information_schema.views where table_schema = 'rcm'")
            )
        }
    lost = sorted(set(warehouse_baseline.views) - live)
    assert not lost, (
        f"the integration run destroyed {len(lost)} analytics view(s) and did not "
        f"restore them: {lost}. The suite must not leave the warehouse degraded. "
        f"Repair: {_REPAIR}"
    )


def test_the_run_did_not_strip_the_sim_prefix_from_any_column(
    pg_engine_session, warehouse_baseline
):
    """No column that carried the §3.2 `sim_` prefix before the run has lost it.

    Not hypothetical — I did this to the shared warehouse while proving the guard
    above. `apply_ddl` rewrites the crosswalk tables from the DDL of whichever
    BRANCH the suite runs from, so running it from a branch that predates the §3.2
    prefix rename silently reverts the live crosswalk to the bare column names,
    taking the provenance marker with it. Everything else about the warehouse looks
    healthy afterwards, which is what makes it worth an assertion.

    This is not something the restore can fix — re-applying the branch's own SQL is
    the correct behaviour for that branch — so the guard makes it visible instead.
    The rule it protects is docs/project_rules.md §3.2, and the §4 blacklist matches on column
    names, so a stripped prefix is a leakage-surface change, not cosmetics.
    """
    if not warehouse_baseline.reachable or not warehouse_baseline.sim_table_columns:
        pytest.skip("no sim_ tables were present before the run")

    with pg_engine_session.connect() as conn:
        live: dict[str, set[str]] = {}
        for table, column in conn.execute(
            text(
                "select table_name, column_name from information_schema.columns "
                "where table_schema = 'rcm' and table_name like 'sim\\_%'"
            )
        ):
            live.setdefault(table, set()).add(column)

    regressions = {}
    for table, before in warehouse_baseline.sim_table_columns.items():
        after = live.get(table)
        if after is None:
            continue  # table absence is a different failure, covered above
        lost_prefix = {c for c in before if c.startswith("sim_")} - after
        if lost_prefix:
            regressions[table] = sorted(lost_prefix)

    assert not regressions, (
        f"columns that carried the `sim_` prefix before this run no longer exist "
        f"after it: {regressions}. docs/project_rules.md §3.2 requires every simulated column to "
        f"be prefixed, and the §4 leakage blacklist matches on column names. The "
        f"usual cause is running the integration suite from a branch whose "
        f"sql/ddl/ predates a rename: apply_ddl rewrote the table into the older "
        f"shape. Re-apply the current DDL and reload before handing the warehouse on."
    )


def test_the_reference_enrichment_survived_the_run(pg_engine_session, warehouse_baseline):
    """dim_drg.drg_desc still carries every FY2023 MS-DRG title it carried before.

    `dim_drg` is dropped and recreated by `apply_ddl` with a null `drg_desc`, so
    the REFERENCE enrichment loaded by `make reference-codes` is discarded on
    every run. Checked on the natural key, because `drg_key` is a surrogate that
    the reload is free to reassign.
    """
    if not warehouse_baseline.reachable:
        pytest.skip("no database was reachable to snapshot before the run")
    if not warehouse_baseline.drg_desc:
        pytest.skip("dim_drg carried no enrichment before the run — nothing could be lost")

    with pg_engine_session.connect() as conn:
        live = {
            r[0]
            for r in conn.execute(text("select drg_cd from rcm.dim_drg where drg_desc is not null"))
        }
    lost = sorted(set(warehouse_baseline.drg_desc) - live)
    assert not lost, (
        f"the integration run discarded the REFERENCE drg_desc enrichment for "
        f"{len(lost)} of {len(warehouse_baseline.drg_desc)} DRG code(s) "
        f"(e.g. {lost[:5]}) and did not restore it. Repair: {_REPAIR}"
    )
