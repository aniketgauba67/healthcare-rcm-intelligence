"""Apply the analytics KPI views to the live warehouse (idempotent).

Owned by analytics-engineer (sql/views/). This runner reads the shared
connection helper `database_url()` from the ingestion package but does NOT
modify it — the import is read-only. It applies, in filename order, every
`vw_*.sql` file in this directory. Each view file is written `create or
replace view` (or `drop view if exists` + `create`) so re-running is safe.

Usage:
    uv run python sql/views/apply_views.py            # apply all vw_*.sql
    uv run python sql/views/apply_views.py --list     # list files, no DB

The warehouse is a shared single-writer Postgres. Creating/replacing a view is
metadata-only DDL and does not touch fact/sim rows, so this is safe to run
outside a quiet window. It never drops or reloads base tables.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

VIEWS_DIR = Path(__file__).resolve().parent
# Base/helper views must be created before the contract views that read them.
_ORDER_PREFIX = ("vw_claim_enriched",)


def _ordered_sql_files() -> list[Path]:
    files = sorted(VIEWS_DIR.glob("vw_*.sql"))
    base = [f for f in files if f.stem in _ORDER_PREFIX]
    rest = [f for f in files if f.stem not in _ORDER_PREFIX]
    return base + rest


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply analytics KPI views.")
    parser.add_argument("--list", action="store_true", help="list files only")
    args = parser.parse_args()

    files = _ordered_sql_files()
    if args.list:
        for f in files:
            print(f.name)
        return 0

    # Import here so --list works without DB deps configured.
    repo_root = VIEWS_DIR.parents[1]
    sys.path.insert(0, str(repo_root))
    from sqlalchemy import create_engine, text  # noqa: E402

    from src.ingestion.load_postgres import database_url  # noqa: E402

    url = database_url()
    if not url:
        print("ERROR: no database_url() — set POSTGRES_* / DATABASE_URL in .env", file=sys.stderr)
        return 2

    engine = create_engine(url)
    applied = 0
    with engine.begin() as conn:
        for f in files:
            conn.execute(text(f.read_text()))
            print(f"applied {f.name}")
            applied += 1
    print(f"done: {applied} view file(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
