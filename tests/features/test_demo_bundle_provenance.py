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

A MISSING TABLE IS NOT A MISSING BUNDLE, and the first draft of this file treated
them the same. Both column checks below looked their table up by name and
`pytest.skip`ped when it was absent — so a bundle that was PRESENT, openable, and
carrying sixteen tables reported nothing to check the moment one of them was
renamed. Measured on a copy of the committed bundle with
`vw_work_queue_priority` renamed and nothing else touched: the unmarked-column
check goes from a correct RED naming all four columns to SKIPPED, and in a suite
summary a skip is indistinguishable from a pass.

That is the same defect this file was written to close, one level up — an
instrument weaker than the green it produces, passing because it cannot see
rather than because there is nothing to see. It matters here specifically because
a view rename is in flight for Phase 5 ([BLACKLIST-LOCKSTEP]), so the exact
condition that disarms the check is the one being planned. `_require_table`
therefore FAILS on absence and names the tables the bundle really holds. A rename
costs one line here; a drop costs a sentence saying so. Neither costs silence.
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

# Columns the BUNDLE's copy of the work queue adds on top of WORK_QUEUE_SCHEMA.
# Rule 2 in src/features/provenance.py is two-directional — exactly the declared
# columns, no more and no fewer — because "an undeclared column is one that
# skipped rule 3". The first draft of the check below compared one direction only
# (declared minus present), so a column added on the way into the bundle was
# invisible to it. One exists: measured, not supposed.
#
# The allowance is a sentence rather than a boolean, the same cost `marker_exempt`
# charges. Anything not named here fails.
BUNDLE_ONLY_COLUMNS: dict[str, str] = {
    "queue_mode": (
        "Discriminates the two queue builds the bundle unions into one table: "
        "`live_snapshot` (the as-of-now queue) and `backtest` (the monthly replay). "
        "Measured in the committed bundle as 1 live_snapshot row and 468 backtest rows. "
        "It is a parameter of OUR build, not an attribute of any claim — the `as_of` and "
        "`split` class under QA ruling C — and carries no dollar, rate or date reading, so "
        "it cannot be misread as a claim about real money. It is absent from the per-run "
        "CSVs, which is why WORK_QUEUE_SCHEMA does not declare it: the CSVs are written one "
        "queue at a time and `assert_publishable` holds them to the declaration exactly. "
        "FLAGGED for app-engineer and qa rather than settled by me — the column is theirs, "
        "and this allowance is a written record that the check now SEES it, not a ruling "
        "that it is fine."
    ),
}


def _bundles() -> list[pathlib.Path]:
    return sorted(BUNDLE_DIR.glob("*.duckdb")) if BUNDLE_DIR.is_dir() else []


def _require_table(tables: dict[str, tuple[str, ...]], name: str, why: str) -> tuple[str, ...]:
    """The bundle's columns for `name`, or a FAILURE — never a skip.

    The bundle being present and the table being in it are different facts, and
    only the first one justifies going quiet. See the module docstring.
    """
    if name in tables:
        return tables[name]
    raise AssertionError(
        f"the demo bundle is present and holds {len(tables)} tables, but `{name}` is not one "
        f"of them: {sorted(tables)}.\n"
        f"That table is checked here because {why}\n"
        "This is a FAILURE and not a skip on purpose. Two things put us here and both are "
        "changes to a published surface:\n"
        "  * RENAMED — re-point the constant in this file, in the same commit as the rename. "
        "That is the lockstep the [BLACKLIST-LOCKSTEP] item exists for, moved to the commit "
        "that causes the drift instead of a later review.\n"
        "  * DROPPED from the bundle — say so here in a sentence. Dropping the table is a "
        "legitimate fix for the finding below, but silently is not: dropping it and letting "
        "this go quiet is indistinguishable from fixing it."
    )


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
    columns = _require_table(
        tables,
        queue,
        "it is WORK_QUEUE_SCHEMA's own table on the hosted demo — the surface the "
        "[QUEUE-PREFIX] defect was closed for.",
    )

    declared = {column.name for column in WORK_QUEUE_SCHEMA.columns}
    present = set(columns)
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

    undeclared = sorted(present - declared - set(BUNDLE_ONLY_COLUMNS))
    assert not undeclared, (
        f"{queue} in the demo bundle publishes {undeclared}, which WORK_QUEUE_SCHEMA does not "
        "declare. Rule 2 in src/features/provenance.py is two-directional for a reason: an "
        "undeclared column is one that skipped the marking rule, and this table is the most "
        "exposed copy of the queue we ship.\n"
        "Either declare it in WORK_QUEUE_SCHEMA (if it is a real queue column) or add it to "
        "BUNDLE_ONLY_COLUMNS in this file with a written justification (if it is a parameter "
        "of the bundle build, the way `queue_mode` is). A sentence, not a boolean."
    )


def test_the_heuristic_queue_view_does_not_ship_unmarked_simulated_columns(
    tables: dict[str, tuple[str, ...]],
) -> None:
    """The measured gap: the API re-marks these, the bundle copies the view verbatim."""
    view = "vw_work_queue_priority"
    present = set(
        _require_table(
            tables,
            view,
            "the bundle copies it out of the warehouse unmodified, so it is the one place "
            "these four columns reach a reader with none of our code in front of them.",
        )
    )
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


def test_a_missing_table_fails_instead_of_skipping() -> None:
    """Negative control on the instrument itself, so it cannot regress to a skip.

    Runs with no bundle: the point is the reaction to absence, not the data. Without
    this, the two checks above could quietly return to `pytest.skip` on a missing
    table and every suite would stay green — which is the property being fixed.
    """
    with pytest.raises(AssertionError) as raised:
        _require_table({"some_other_table": ("a",)}, "vw_work_queue_priority", "of a reason.")
    message = str(raised.value)
    assert "not one of them" in message
    assert "some_other_table" in message, "the failure must name what the bundle DOES hold"
    assert "RENAMED" in message and "DROPPED" in message

    with pytest.raises(pytest.skip.Exception):
        pytest.skip("control: a skip is a different outcome and must stay distinguishable")


def test_every_bundle_only_allowance_is_a_written_justification() -> None:
    """An allowance costs a sentence, the same price `marker_exempt` charges."""
    for column, reason in BUNDLE_ONLY_COLUMNS.items():
        assert len(reason.split()) >= 20, (
            f"BUNDLE_ONLY_COLUMNS[{column!r}] is too short to be a justification. A boolean "
            "with extra steps lets the next column through on the strength of this one."
        )
