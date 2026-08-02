"""Application-owned reconciliation outcome controls."""

from __future__ import annotations

import pandas as pd

from dashboard import reconcile


def _check(name: str, datasets: tuple[str, ...], dashboard, control) -> reconcile.Check:
    return reconcile.Check(
        figure=name,
        page="Control",
        dashboard=dashboard,
        control=control,
        control_sql="select 1;",
        datasets=datasets,
    )


def test_multiple_missing_datasets_keep_every_affected_check_visible() -> None:
    checks = (
        _check("Needs A", ("a",), lambda _: 1.0, lambda _: 1.0),
        _check("Needs B", ("b",), lambda _: 1.0, lambda _: 1.0),
        _check("Needs both", ("a", "b"), lambda _: 1.0, lambda _: 1.0),
    )

    results = reconcile.run({}, checks)

    assert [result.figure for result in results] == [check.figure for check in checks]
    assert [result.status for result in results] == ["MISSING_INPUT"] * len(checks)
    assert results[2].missing_inputs == ("a", "b")
    summary = reconcile.summarize(results)
    assert (summary.declared, summary.evaluated, summary.passed, summary.failed) == (3, 0, 0, 0)
    assert summary.not_evaluated == 3 and not summary.all_passed


def test_evaluated_failure_prevents_an_all_passed_summary() -> None:
    check = _check("Mismatch", ("frame",), lambda _: 1.0, lambda _: 2.0)

    results = reconcile.run({"frame": pd.DataFrame()}, (check,))

    assert results[0].status == "FAIL"
    summary = reconcile.summarize(results)
    assert (summary.declared, summary.evaluated, summary.passed, summary.failed) == (1, 1, 0, 1)
    assert not summary.all_passed


def test_unexpected_check_error_is_distinct_from_missing_input() -> None:
    def raises(_: reconcile.Frames) -> float:
        raise RuntimeError("control exploded")

    check = _check("Broken", ("frame",), raises, lambda _: 1.0)

    results = reconcile.run({"frame": pd.DataFrame()}, (check,))

    assert results[0].status == "ERROR"
    assert results[0].missing_inputs == ()
    assert results[0].detail == "RuntimeError: control exploded"
    summary = reconcile.summarize(results)
    assert summary.errors == 1 and summary.not_evaluated == 1 and not summary.all_passed


def test_every_available_passing_check_is_a_complete_success() -> None:
    checks = (
        _check("One", ("frame",), lambda _: 1.0, lambda _: 1.0),
        _check("Two", ("frame",), lambda _: 2.0, lambda _: 2.0),
    )

    results = reconcile.run({"frame": pd.DataFrame()}, checks)

    assert [result.status for result in results] == ["PASS", "PASS"]
    summary = reconcile.summarize(results)
    assert (summary.declared, summary.evaluated, summary.passed, summary.failed) == (2, 2, 2, 0)
    assert summary.not_evaluated == 0 and summary.all_passed
