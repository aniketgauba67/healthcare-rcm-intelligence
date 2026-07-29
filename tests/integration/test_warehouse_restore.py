"""Repair what the integration run CASCADE-dropped, before anything asserts on it.

Runs at rank 80 (see tests/integration/conftest.py): after the Phase 1 warehouse
rebuild and the Phase 2 sim_ reattach, and before the rank-90 end-state guard.

`apply_ddl` drop-CASCADEs the star schema twice per run. Two materializations
that live outside the DDL go with it and were never put back:

  1. all 9 `rcm.vw_*` analytics views, which read the star schema;
  2. `dim_drg.drg_desc`, the FY2023 MS-DRG enrichment (167 of 168 rows), because
     `dim_drg` is dropped and recreated with the column null.

Neither loss raises, so the suite reported green over a degraded warehouse — a
Phase 3 acceptance was signed off against a database that had silently lost both.
This module closes that by running the documented repair, `make reference-codes
&& make views`, as part of the suite instead of relying on the next human to
remember it.

Two deliberate choices:

  * The views are rebuilt by executing the SHIPPED runner, `sql/views/apply_views.py`
    — the same command `make views` runs. Re-applying the files (rather than
    replaying the definitions captured from the catalog) means the restored views
    match the repository, so a run cannot quietly preserve a stale view shape.
    The captured definitions are kept only as a fallback for a view that is in the
    catalog but has no file.
  * `dim_drg.drg_desc` is restored from the baseline snapshot rather than by
    re-running the reference loader, which needs the gitignored raw downloads. The
    snapshot is a write-back of the exact values that were there, so this stays
    test-harness bookkeeping and does not reimplement any of
    `src/ingestion/reference_codes.py` (that file is data-engineer's; CLAUDE.md §5).

Repair, then assert (tests/integration/test_end_state.py). Both halves are
needed: restoring without asserting moves the blind spot instead of closing it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
_APPLY_VIEWS = REPO_ROOT / "sql" / "views" / "apply_views.py"


def _restore_views_from_shipped_sql() -> subprocess.CompletedProcess[str]:
    """Run the shipped view runner exactly as `make views` does."""
    return subprocess.run(
        [sys.executable, str(_APPLY_VIEWS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _restore_views_from_snapshot(conn, baseline, already_present: set[str]) -> list[str]:
    """Recreate any baseline view the shipped SQL did not bring back.

    Dependency order is unknown here, so create in repeated passes and keep the
    ones that succeed; a view whose dependencies are not yet present fails on one
    pass and is retried on the next. Returns the names still missing.
    """
    pending = {n: b for n, b in baseline.views.items() if n not in already_present}
    for _ in range(len(pending)):
        if not pending:
            break
        progressed = False
        for name, body in list(pending.items()):
            try:
                with conn.begin_nested():
                    conn.exec_driver_sql(f"create or replace view rcm.{name} as {body}")
            except Exception:  # noqa: BLE001,PERF203 - dependency not ready yet; retry
                continue
            pending.pop(name)
            progressed = True
        if not progressed:
            break
    return sorted(pending)


def _live_views(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            text("select table_name from information_schema.views where table_schema = 'rcm'")
        )
    }


def test_the_suite_rebuilds_the_views_it_cascade_dropped(pg_engine_session, warehouse_baseline):
    """Put the analytics view layer back, from the repository's own SQL."""
    if not warehouse_baseline.reachable or not warehouse_baseline.views:
        pytest.skip("no view layer existed before this run — nothing was dropped to restore")

    proc = _restore_views_from_shipped_sql()
    with pg_engine_session.connect() as conn:
        present = _live_views(conn)
        still_missing = _restore_views_from_snapshot(conn, warehouse_baseline, present)
        conn.commit()

    assert not still_missing, (
        f"could not restore view(s) {still_missing} that existed before this run. "
        f"apply_views.py exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
    )

    # Three states, and the snapshot fallback makes two of them look alike.
    #
    # The fallback exists for a view that is in the catalog with no file behind it.
    # It also, silently, covers a repository whose own `make views` is BROKEN: the
    # runner raises, apply_views.py wraps all 9 views in ONE transaction so the
    # whole rebuild rolls back to zero, the snapshot then puts the pre-run
    # definitions back, and the assertion above passes. The warehouse is fine and
    # the repository is not, which is the worse of the two to leave unsaid.
    #
    # So the shipped runner's exit code is asserted on its own. This is the loud,
    # DISTINGUISHABLE failure qa-reviewer-p9 asked for: "restore succeeded",
    # "restore rescued a broken build" and "restore never ran" are now three
    # different outcomes instead of one green tick. It is also the exact shape of
    # the stale-branch hazard — pre-rename view SQL against a renamed crosswalk
    # raises here — which `branch_is_not_stale` now blocks earlier.
    assert proc.returncode == 0, (
        "the views were restored from the pre-run snapshot, but the SHIPPED view "
        f"runner FAILED (sql/views/apply_views.py exited {proc.returncode}). The live "
        "warehouse is fine; `make views` in this repository is not, and without this "
        "assertion the run would have reported green.\n"
        f"  stderr: {proc.stderr.strip() or '(none)'}\n"
        f"  stdout: {proc.stdout.strip()[-2000:] or '(none)'}\n"
        "apply_views.py builds all views in one transaction, so a single bad view file "
        "rolls the whole layer back to zero. Fix the view SQL; do not rely on the "
        "snapshot fallback, which only ever holds what happened to be there before."
    )


def test_the_suite_restores_the_reference_enrichment_it_discarded(
    pg_engine_session, warehouse_baseline
):
    """Write dim_drg.drg_desc back from the pre-run snapshot, keyed on drg_cd."""
    if not warehouse_baseline.reachable or not warehouse_baseline.drg_desc:
        pytest.skip("dim_drg carried no enrichment before this run — nothing to restore")

    rows = [
        {"cd": cd, "desc": desc, "prov": prov}
        for cd, (desc, prov) in warehouse_baseline.drg_desc.items()
    ]
    with pg_engine_session.begin() as conn:
        conn.execute(
            text(
                "update rcm.dim_drg set drg_desc = :desc, provenance = :prov "
                "where drg_cd = :cd and drg_desc is null"
            ),
            rows,
        )
        restored = conn.execute(
            text("select count(*) from rcm.dim_drg where drg_desc is not null")
        ).scalar()

    assert restored >= len(warehouse_baseline.drg_desc), (
        f"dim_drg.drg_desc holds {restored} enriched rows after restore, "
        f"expected at least the {len(warehouse_baseline.drg_desc)} present before the run"
    )
