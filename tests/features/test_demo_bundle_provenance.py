"""Rule 3, applied INSIDE the demo bundle instead of stopping at its extension.

`dashboard/demo_data/*.duckdb` is registered in `PUBLISHED_SURFACES` with an
`undeclared_reason`, because 16 heterogeneous tables cannot honestly share one
column schema. That satisfies rule 1 (coverage). It does NOT satisfy rule 3
(marking), and the reason is mechanical rather than a matter of trust:
`provenance.read_columns` returns None for a `.duckdb`, so the marking rule has
nothing to read and goes quiet — over the single most exposed file in the project,
committed and openable from a clean clone with no database.

A registration that silences a rule is the shape of the defect this whole module
exists for. So the registration is paired with this file, which opens the bundle
and checks the columns really in it.

WHAT IT CHECKS, and why these and not everything. Lineage for arbitrary bundle
columns is not declared per column, so a general "is this simulated" ruling cannot
be made here. Two things CAN be checked without inventing a declaration:

  * the Model C queue inside the bundle must carry the WORK_QUEUE_SCHEMA names,
    which are already declared — this is the [QUEUE-PREFIX] defect's own surface;
  * `vw_work_queue_priority`'s value columns must not arrive unmarked. These are
    a MEASURED finding, not a guess: the view computes `dollars_at_stake` from
    `sim_denied_amount`/`ar_balance_amt`, `heuristic_priority_score` as dollars x
    age, `priority_tier` as an ntile over that score, and `action_type` as a CASE
    on `sim_denial_flag` — which makes the last one the LABEL under a process
    name. `action_type` is on `forbidden_derived_features` for that reason
    (f18dfc7).

WHY THE BUNDLE AND THE API CAN DISAGREE, which is the trap this catches.
`src/api/tables.py` re-marks those columns on the way out, so the API is clean.
`src/demo/build.py: read_warehouse_datasets` copies the views UNMODIFIED — which
is a deliberate and good property, since it stops a dashboard figure diverging
from its SQL control query through a second implementation. The consequence is
that the fix applied at the API does not reach the bundle, and the two published
surfaces disagree about the same column. A check that ran only against the API
would report the whole thing clean.

FIXABLE IN TWO PLACES, and the choice is not this file's to make: analytics-
engineer marks the columns in `sql/views/vw_work_queue_priority.sql` (the better
fix — the warehouse and the screen then agree), or app-engineer re-marks on the
way into the bundle as `src/api/tables.py` already does. Either turns this green.

SKIPS when the bundle is absent, which is every worktree that has not run
`make demo-extract`. That is not a silent pass: the bundle is committed, so once
it lands this runs everywhere, including CI on a clean clone.
"""

from __future__ import annotations

import pathlib

import pytest

from src.features.provenance import (
    WORK_QUEUE_SCHEMA,
    REPO_ROOT,
    names_simulated,
    surface_for,
)

BUNDLE_DIR = REPO_ROOT / "dashboard" / "demo_data"

# Columns of rcm.vw_work_queue_priority that are computed from simulated money,
# simulated dates or the simulated denial flag, and carry no marker in the view.
# `age_days` is deliberately absent: it is the point-in-time boundary minus a
# global constant, the same reasoning that keeps it off forbidden_derived_features.
UNMARKED_HEURISTIC_COLUMNS = (
    "dollars_at_stake",
    "heuristic_priority_score",
    "priority_tier",
    "action_type",
)


def _bundles() -> list[pathlib.Path]:
    return sorted(BUNDLE_DIR.glob("*.duckdb")) if BUNDLE_DIR.is_dir() else []


@pytest.fixture(scope="module")
def bundle() -> pathlib.Path:
    found = _bundles()
    if not found:
        pytest.skip(
            f"no demo bundle under {BUNDLE_DIR.relative_to(REPO_ROOT)}; `make demo-extract`"
        )
    return found[0]


@pytest.fixture(scope="module")
def tables(bundle: pathlib.Path) -> dict[str, tuple[str, ...]]:
    """Every table in the bundle and its columns, read without loading rows."""
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect(str(bundle), read_only=True)
    try:
        names = [row[0] for row in connection.execute("show tables").fetchall()]
        return {
            name: tuple(
                row[1] for row in connection.execute(f'pragma table_info("{name}")').fetchall()
            )
            for name in names
        }
    finally:
        connection.close()


def test_the_bundle_is_covered_by_a_registered_surface() -> None:
    """Rule 1, restated at the path. The container registration must actually match."""
    for path in _bundles() or [BUNDLE_DIR / "rcm_demo.duckdb"]:
        relative = path.relative_to(REPO_ROOT).as_posix()
        assert surface_for(relative) is not None, (
            f"{relative} is under a published root and no registered surface covers it. "
            "Register it in src/features/provenance.py: PUBLISHED_SURFACES."
        )


def test_the_queue_table_carries_the_declared_marked_names(
    tables: dict[str, tuple[str, ...]],
) -> None:
    """The Model C queue in the bundle is WORK_QUEUE_SCHEMA's table, so it obeys it."""
    queue = "model_c_work_queue"
    if queue not in tables:
        pytest.skip(f"{queue} not in this bundle")

    declared = {column.name for column in WORK_QUEUE_SCHEMA.columns}
    present = set(tables[queue])
    missing = sorted(declared - present)
    assert not missing, (
        f"{queue} in the demo bundle is missing declared columns {missing}. If these are the "
        "pre-rename spellings, the bundle was built from stale artifacts — regenerate with "
        "`make train-appeal` and rebuild."
    )
    stripped = sorted(
        column
        for column in declared
        if names_simulated(column) and column.replace("sim_", "", 1) in present - declared
    )
    assert not stripped, (
        f"{queue} publishes {stripped} with the sim_ marker removed. That is the "
        "[QUEUE-PREFIX] defect on the hosted demo, which is the surface it was closed for."
    )


def test_the_heuristic_queue_view_does_not_ship_unmarked_simulated_columns(
    tables: dict[str, tuple[str, ...]],
) -> None:
    """The measured gap: the API re-marks these, the bundle copies the view verbatim."""
    view = "vw_work_queue_priority"
    if view not in tables:
        pytest.skip(f"{view} not in this bundle")

    present = set(tables[view])
    unmarked = sorted(column for column in UNMARKED_HEURISTIC_COLUMNS if column in present)
    assert not unmarked, (
        f"the demo bundle ships {view} with {unmarked} unmarked. Every one is computed from "
        "simulated money, a simulated date or sim_denial_flag — `action_type` is a CASE on "
        "the denial flag, i.e. the LABEL under a process name, and is on "
        "`forbidden_derived_features` for exactly that reason.\n"
        "This is NOT caught by the API's re-marking: src/api/tables.py rewrites these on the "
        "way out, while src/demo/build.py copies the views unmodified (deliberately, so a "
        "dashboard figure cannot diverge from its SQL control query). The two published "
        "surfaces therefore disagree about the same column, and the bundle is the one a "
        "reader opens without any of our code in front of it.\n"
        "Fix at the view (sql/views/vw_work_queue_priority.sql, analytics-engineer — the "
        "better fix, the warehouse and the screen then agree) or re-mark on the way into the "
        "bundle the way the API already does. Do not silence this by dropping the columns "
        "from the bundle without saying so."
    )
