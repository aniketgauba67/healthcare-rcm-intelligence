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
    load_frames,
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


def test_warehouse_load_and_acceptance_checks(pg_engine):
    if not (DATA_VALIDATED / "inpatient.parquet").exists():
        pytest.skip("validated Parquet missing; run `make ingest && make stage` first")

    frames = build_star()
    apply_ddl(pg_engine)  # idempotent: drops + recreates
    load_frames(pg_engine, frames)

    checks = validate_postgres(pg_engine, frames)
    failed = [c.name for c in checks if not c.passed]
    assert not failed, f"live-Postgres acceptance checks failed: {failed}"
    # Sanity: the full suite ran (FKs + uniqueness + date order + money + counts).
    assert len(checks) >= 30


def test_warehouse_load_is_idempotent(pg_engine):
    """A second full apply+load produces identical counts (no duplication)."""
    if not (DATA_VALIDATED / "inpatient.parquet").exists():
        pytest.skip("validated Parquet missing; run `make ingest && make stage` first")

    frames = build_star()
    apply_ddl(pg_engine)
    load_frames(pg_engine, frames)
    checks = validate_postgres(pg_engine, frames)
    assert not [c.name for c in checks if not c.passed]
