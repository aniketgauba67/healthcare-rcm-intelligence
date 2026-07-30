"""§7: dashboard totals reconcile to SQL control queries — and a check that did not run says so.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

WHAT PASSES, MEASURED FIRST (qa-reviewer-p18, 2026-07-29)
---------------------------------------------------------
`dashboard/reconcile.py` declares 17 checks, each pairing a figure a page shows
with the same quantity reached from a DIFFERENT dataset, and each carrying the SQL
a reader can run. Against the committed bundle all 17 evaluate and all 17 pass.
That is the §7 criterion met on the path that ships, and it is good work.

THE GAP THIS FILE PINS
----------------------
`reconcile.run()` skips any check whose datasets are absent:

    if any(name not in frames for name in check.datasets):
        continue

and `dashboard/pages/model_data_quality.py:76` then renders

    f"**All {len(results)} reconciled figures match their control totals exactly.**"

where `len(results)` counts only what was EVALUATED. MEASURED on the Postgres path,
where the model outputs are not warehouse datasets:

    RCM_DATA_SOURCE=postgres -> 17 declared, 14 evaluated, 3 vanished,
    page reports "All 14 reconciled figures match", green tick, nothing missing.

The three that vanish are the model ones — Model A rows scored, denials among the
scored claims, and Model C's queue being a subset of the denials.

This is the [GUARD-DISARM] shape in a reporter rather than a guard, and the repo
already refuses it one layer down: `src/features/store.py::manifest_deviations`
emits "NULL RATES NOT COMPARED" rather than passing over a missing baseline block,
on the stated principle that a check which did not run must never read like a check
that passed. The reconciliation panel is the most load-bearing honesty surface in
the app — it is the page that tells a reader the numbers can be trusted — so it is
the last place a silent skip belongs.

Fix is app-engineer's and small: have `run()` return, or the page display, the
DECLARED count and the names of anything not evaluated.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "dashboard"

pytestmark = pytest.mark.skipif(
    not (DASHBOARD / "reconcile.py").is_file(),
    reason="dashboard/reconcile.py does not exist yet",
)


def _reconcile():
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from dashboard import reconcile

    return reconcile


def _frames(names: set[str]) -> dict:
    from dashboard import data

    return {name: data.load(name) for name in sorted(names)}


def _all_datasets() -> set[str]:
    reconcile = _reconcile()
    return {dataset for check in reconcile.ALL_CHECKS for dataset in check.datasets}


def test_every_declared_check_has_a_control_query() -> None:
    """A figure without a control query is a figure nobody can check (§7)."""
    reconcile = _reconcile()
    silent = [check.figure for check in reconcile.ALL_CHECKS if not check.control_sql.strip()]
    assert not silent, (
        "these reconciliation checks carry no control SQL, so the page can show a green tick "
        "beside a figure a reader cannot verify:\n  " + "\n  ".join(silent)
    )


# A CHECK QA WROTE, RAN, AND DELETED — recorded so it is not re-added.
# "every control total must come from a DIFFERENT dataset than the figure" sounds
# like the right rule and is not one. It reported two checks:
#   Denial prevention — Full + partial denials      (vw_denial_root_cause)
#   A/R & recovery — Denied + non-denied = open     (vw_ar_aging)
# Both are additivity identities WITHIN one view — full + partial = total denials,
# denied + non-denied open = open, in every bucket — and an invariant inside a view
# is a real check, not a second run of the same code. The rule was a false red of
# qa's own making, on the same day qa reported one in the disclosure gate, so it is
# removed rather than weakened. Cross-dataset is the norm here and 15 of the 17
# checks meet it; the exceptions are correct exceptions.


def test_every_figure_reconciles_against_the_bundle() -> None:
    """The path that ships. All 17 passed when this was written."""
    reconcile = _reconcile()
    frames = _frames(_all_datasets())
    results = reconcile.run(frames, reconcile.ALL_CHECKS)
    failures = reconcile.failures(results)
    assert not failures, (
        "figures that do not reconcile against the committed bundle:\n  "
        + "\n  ".join(
            f"{r.figure}: dashboard={r.dashboard_value} control={r.control_value} "
            f"difference={r.difference} tolerance={r.tolerance}"
            for r in failures
        )
    )


def test_the_bundle_carries_every_dataset_the_checks_need() -> None:
    """Otherwise the test above is green over however many checks happened to run."""
    reconcile = _reconcile()
    frames = _frames(_all_datasets())
    results = reconcile.run(frames, reconcile.ALL_CHECKS)
    assert len(results) == len(reconcile.ALL_CHECKS), (
        f"{len(reconcile.ALL_CHECKS) - len(results)} of {len(reconcile.ALL_CHECKS)} declared "
        "checks did not evaluate against the bundle, so the reconciliation pass above covers "
        "less than it claims. Missing datasets: "
        f"{sorted({d for c in reconcile.ALL_CHECKS for d in c.datasets} - set(frames))}"
    )


def test_a_check_that_could_not_run_is_reported_rather_than_dropped() -> None:
    """RED (qa-reviewer-p18). The silent skip described in this module's docstring.

    Reproduced without a database by handing `run()` a frame set with one dataset
    withheld — the same condition the Postgres path produces for the three model
    checks. The declared total has to survive into what a reader is shown; today
    the skipped check leaves no trace anywhere in the result set.
    """
    reconcile = _reconcile()
    every = _all_datasets()
    withheld = sorted(every)[0]
    frames = _frames(every - {withheld})

    results = reconcile.run(frames, reconcile.ALL_CHECKS)
    dropped = [
        check.figure
        for check in reconcile.ALL_CHECKS
        if withheld in check.datasets and check.figure not in {r.figure for r in results}
    ]
    assert dropped, f"withholding {withheld!r} dropped no check; pick a dataset a check needs"

    frame = reconcile.to_frame(results)
    reported = set(frame["figure"]) if not frame.empty else set()
    assert set(dropped) <= reported, (
        f"withholding the dataset {withheld!r} silently removed {len(dropped)} check(s) from the "
        "reconciliation result:\n  " + "\n  ".join(dropped) + "\n\n"
        f"`reconcile.run()` evaluated {len(results)} of {len(reconcile.ALL_CHECKS)} declared "
        "checks and the result set carries no record of the difference, so "
        "dashboard/pages/model_data_quality.py renders 'All N reconciled figures match' over a "
        "smaller N. MEASURED on the Postgres path: 17 declared, 14 evaluated, and the three "
        "model checks vanish behind a green tick.\n"
        "The repo already refuses this shape one layer down — "
        "src/features/store.py::manifest_deviations emits 'NULL RATES NOT COMPARED' rather than "
        "passing over a missing baseline, because a check that did not run must never read like "
        "a check that passed. Carry the unevaluated figures through (a NOT_CHECKED row, or a "
        "declared-vs-evaluated count the page prints) instead of dropping them."
    )
