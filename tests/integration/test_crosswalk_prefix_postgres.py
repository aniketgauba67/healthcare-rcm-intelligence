"""Live-Postgres regression guard for the §3.2 crosswalk column-prefix rule.

Both crosswalk tables are classified SIMULATED, so CLAUDE.md §3.2 requires every
column to carry the `sim_` prefix. This read-only test introspects
information_schema against the live database and fails if any column of either
crosswalk table is missing the prefix. It never writes, so it is safe to run in
any order (unranked) and does not disturb the shared warehouse state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_CROSSWALK_TABLES = ("sim_facility_crosswalk", "sim_provider_crosswalk")


def test_every_live_crosswalk_column_is_sim_prefixed(pg_engine_session):
    engine = pg_engine_session
    with engine.connect() as conn:
        for table in _CROSSWALK_TABLES:
            cols = [
                r[0]
                for r in conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'rcm' and table_name = :t "
                        "order by ordinal_position"
                    ),
                    {"t": table},
                )
            ]
            assert cols, f"{table} not found in rcm schema (crosswalk not loaded?)"
            offenders = [c for c in cols if not c.startswith("sim_")]
            assert not offenders, f"{table} has non-sim_-prefixed columns: {offenders}"
