"""Live-Postgres regression guard for the §3.2 crosswalk column-prefix rule.

Both crosswalk tables are classified SIMULATED, so CLAUDE.md §3.2 requires every
column to carry the `sim_` prefix. This read-only test introspects
information_schema against the live database and fails if any column of either
crosswalk table is missing the prefix. It never writes, so it is safe to run in
any order (unranked) and does not disturb the shared warehouse state.

It also asserts the prefix survives the VIEW boundary (team-lead ruling
2026-07-27): `vw_claim_enriched` is the flattened matrix the Phase 4 feature store
consumes and the §4 leakage blacklist is column-name based, so a simulated-linkage
column arriving there under a bare name would lose its provenance marker. The
static equivalent that runs without a database is
`tests/contracts/test_view_sim_prefix.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_CROSSWALK_TABLES = ("sim_facility_crosswalk", "sim_provider_crosswalk")

# Bare (pre-§3.2) simulated-linkage output names that must no longer exist on any
# view, mapped to the view that used to expose them.
_FORBIDDEN_VIEW_COLUMNS = {
    "vw_claim_enriched": ("facility_ccn", "facility_name", "facility_state", "facility_type"),
    "vw_clean_claim_performance": (
        "display_facility_ccn",
        "display_facility_name",
        "display_facility_state",
    ),
    "vw_work_queue_priority": ("facility_name",),
}

# The prefixed names those views must expose instead.
_REQUIRED_VIEW_COLUMNS = {
    "vw_claim_enriched": (
        "sim_facility_ccn",
        "sim_facility_name",
        "sim_facility_state",
        "sim_facility_type",
    ),
    "vw_clean_claim_performance": (
        "sim_display_facility_ccn",
        "sim_display_facility_name",
        "sim_display_facility_state",
    ),
    "vw_work_queue_priority": ("sim_facility_name",),
}


def _columns(conn, relation: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            text(
                "select column_name from information_schema.columns "
                "where table_schema = 'rcm' and table_name = :t "
                "order by ordinal_position"
            ),
            {"t": relation},
        )
    ]


def test_every_live_crosswalk_column_is_sim_prefixed(pg_engine_session):
    engine = pg_engine_session
    with engine.connect() as conn:
        for table in _CROSSWALK_TABLES:
            cols = _columns(conn, table)
            assert cols, f"{table} not found in rcm schema (crosswalk not loaded?)"
            offenders = [c for c in cols if not c.startswith("sim_")]
            assert not offenders, f"{table} has non-sim_-prefixed columns: {offenders}"


def test_live_views_carry_the_prefix_across_the_view_boundary(pg_engine_session):
    """No view may re-expose a simulated-linkage column under its bare name.

    SKIPS when the views are absent, which is the DESIGNED end state of a full
    integration run: `test_warehouse_postgres` (rank 10) calls `apply_ddl`, which
    drop-CASCADEs the star schema and takes all 9 `vw_*` views with it, and nothing
    in the suite recreates them — see tests/integration/conftest.py and
    tests/integration/test_end_state.py, which asserts the sim_ layer survives but
    deliberately says nothing about views. So this assertion is live after
    `make views` (how qa re-runs the gate) and skipped inside a full `pytest -q`.
    The DB-free enforcement that always runs is
    tests/contracts/test_view_sim_prefix.py.
    """
    engine = pg_engine_session
    with engine.connect() as conn:
        for view, forbidden in _FORBIDDEN_VIEW_COLUMNS.items():
            cols = set(_columns(conn, view))
            if not cols:
                pytest.skip(
                    f"rcm.{view} not present — the integration suite CASCADE-drops "
                    f"the views and does not recreate them. Run `make views` and "
                    f"re-run this module to exercise the live check."
                )
            present = sorted(cols & set(forbidden))
            assert not present, (
                f"{view} exposes unprefixed simulated-linkage column(s) {present}; "
                f"§3.2 requires the sim_ prefix to survive the view boundary"
            )
            missing = sorted(set(_REQUIRED_VIEW_COLUMNS[view]) - cols)
            assert not missing, f"{view} is missing prefixed column(s) {missing}"
