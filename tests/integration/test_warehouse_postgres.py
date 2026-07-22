"""Live Postgres warehouse acceptance test (Phase 1 DoD).

Applies the DDL, loads the star schema, and runs the shared acceptance checks
against a real PostgreSQL 16 instance (`docker compose up -d`). Marked
`integration` so CI unit runs skip it; run explicitly with:

    make validate-warehouse        # or: uv run pytest -m integration -q
"""

from __future__ import annotations

import pytest

from src.ingestion.load_postgres import (
    apply_ddl,
    database_url,
    load_crosswalk,
    load_frames,
    load_quarantine,
    validate_crosswalk,
    validate_postgres,
)
from src.ingestion.paths import DATA_VALIDATED
from src.ingestion.star_transform import build_star

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


def _load_once(engine, frames):
    apply_ddl(engine)  # idempotent: drops + recreates every table
    load_frames(engine, frames)
    xwalk = load_crosswalk(engine)
    load_quarantine(engine)
    return validate_postgres(engine, frames) + validate_crosswalk(engine, xwalk)


def test_warehouse_acceptance_and_idempotency(pg_engine):
    """Load into live Postgres, assert all acceptance checks, then re-load and
    confirm identical counts (idempotent) with all checks still green."""
    if not (DATA_VALIDATED / "inpatient.parquet").exists():
        pytest.skip("validated Parquet missing; run `make ingest && make stage` first")

    frames = build_star()

    checks = _load_once(pg_engine, frames)
    failed = [c.name for c in checks if not c.passed]
    assert not failed, f"live-Postgres acceptance checks failed: {failed}"
    # Full suite ran: FKs + uniqueness + date order + money + counts + reconcile.
    assert len(checks) >= 30
    counts = {c.name: c.detail for c in checks if c.name.startswith("count:")}

    # Release pooled connections before the drop/recreate to avoid lock contention.
    pg_engine.dispose()

    checks2 = _load_once(pg_engine, frames)
    assert not [c.name for c in checks2 if not c.passed]
    counts2 = {c.name: c.detail for c in checks2 if c.name.startswith("count:")}
    assert counts == counts2, "re-load changed row counts (not idempotent)"
