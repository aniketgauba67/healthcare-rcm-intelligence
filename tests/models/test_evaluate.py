"""Metrics that are easy to get quietly wrong.

Each test here corresponds to a way a number in the model card could be
flattering rather than true: a tie-break that rewards row order, a threshold
that reports a share it does not deliver, a bootstrap interval that does not
widen when the evidence thins.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.evaluate import (
    apply_threshold,
    bootstrap_dollar_capture,
    bootstrap_interval,
    calibration_curve_points,
    dollars_at_risk_captured,
    evaluate_classifier,
    expected_calibration_error,
    flagged_share_by_multiplier,
    paired_bootstrap_difference,
    slice_metrics,
    threshold_at_capacity,
    threshold_from_cost_matrix,
)


def _fold(n: int = 800, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A fold with a genuinely predictive score and heavy-tailed amounts."""
    rng = np.random.default_rng(seed)
    score = rng.uniform(0, 1, n)
    y = (rng.uniform(0, 1, n) < 0.05 + 0.25 * score).astype(int)
    amounts = np.where(y == 1, rng.lognormal(7.0, 1.3, n), 0.0)
    return y, score, amounts


# --- dollars ---------------------------------------------------------------


def test_a_perfect_ranker_captures_the_denied_dollars_in_its_decile() -> None:
    y = np.array([1] * 10 + [0] * 90)
    score = np.concatenate([np.ones(10), np.zeros(90)])
    amounts = np.where(y == 1, 1000.0, 0.0)
    assert dollars_at_risk_captured(y, score, amounts, top_fraction=0.10) == pytest.approx(1.0)


def test_tied_scores_do_not_reward_being_early_in_the_frame() -> None:
    """The frame arrives in date order, so a stable sort is a time preference.

    Every claim scores identically here and all the denied dollars sit in the
    first decile. A stable descending sort takes the first 10 rows and reports a
    perfect 1.0 for a model that ranked nothing at all.
    """
    n = 1000
    y = np.zeros(n, dtype=int)
    y[:100] = 1
    amounts = np.where(y == 1, 500.0, 0.0)
    score = np.full(n, 0.3)

    captured = dollars_at_risk_captured(y, score, amounts, top_fraction=0.10, seed=11)
    assert captured < 0.35, f"a constant scorer captured {captured:.2%} of the dollars"

    naive = float(
        np.sum(amounts[np.argsort(-score, kind="stable")[:100]] * (y[:100] == 1))
    ) / float(amounts.sum())
    assert naive == pytest.approx(1.0), "the control: row order alone would have looked perfect"


def test_the_dollar_interval_is_wide_because_the_dollars_are_concentrated() -> None:
    y, score, amounts = _fold()
    interval = bootstrap_dollar_capture(y, score, amounts, n_resamples=200, seed=3)
    assert interval.low < interval.point < interval.high
    assert interval.high - interval.low > 0.05, (
        "a dollar-capture interval this narrow would misrepresent a heavy-tailed metric"
    )


def test_a_fold_with_no_denied_dollars_returns_nan_rather_than_zero() -> None:
    y = np.zeros(50, dtype=int)
    assert np.isnan(
        dollars_at_risk_captured(y, np.random.default_rng(1).uniform(size=50), np.zeros(50))
    )


# --- thresholds ------------------------------------------------------------


def test_capacity_threshold_flags_the_share_it_promises() -> None:
    y, score, amounts = _fold()
    choice = threshold_at_capacity(y, score, amounts, capacity_share=0.10, review_cost_usd=25.0)
    assert choice.flagged_share == pytest.approx(0.10, abs=0.01)


def test_capacity_threshold_beats_the_base_rate_on_precision() -> None:
    y, score, amounts = _fold()
    choice = threshold_at_capacity(y, score, amounts, capacity_share=0.10, review_cost_usd=25.0)
    assert choice.precision > float(y.mean())


def test_cost_optimal_threshold_flags_more_as_prevention_gets_more_valuable() -> None:
    """The sweep the model card uses to show the threshold is an assumption."""
    y, score, amounts = _fold()
    table = flagged_share_by_multiplier(y, score, amounts, 25.0, [0.001, 0.01, 0.1, 1.0])
    shares = table["flagged_share"].to_numpy()
    assert (np.diff(shares) >= -1e-9).all(), f"flagged share is not monotone: {shares}"


def test_a_threshold_chosen_elsewhere_is_measured_not_re_optimised() -> None:
    y, score, amounts = _fold()
    chosen = threshold_from_cost_matrix(y, score, amounts, review_cost_usd=25.0)
    other_y, other_score, other_amounts = _fold(seed=99)
    applied = apply_threshold(other_y, other_score, other_amounts, chosen)
    assert applied.threshold == chosen.threshold


# --- intervals and calibration ---------------------------------------------


def test_paired_difference_of_a_model_with_itself_is_zero() -> None:
    from sklearn.metrics import roc_auc_score

    y, score, _ = _fold()
    interval = paired_bootstrap_difference(roc_auc_score, y, score, score, n_resamples=100)
    assert interval.point == pytest.approx(0.0)
    assert interval.low == pytest.approx(0.0)
    assert interval.high == pytest.approx(0.0)


def test_intervals_widen_when_the_fold_gets_thinner() -> None:
    from sklearn.metrics import roc_auc_score

    y_big, score_big, _ = _fold(n=4000)
    y_small, score_small, _ = _fold(n=300)
    wide = bootstrap_interval(roc_auc_score, y_small, score_small, n_resamples=200)
    narrow = bootstrap_interval(roc_auc_score, y_big, score_big, n_resamples=200)
    assert (wide.high - wide.low) > (narrow.high - narrow.low)


def test_ece_is_zero_for_a_scorer_that_tells_the_truth() -> None:
    rng = np.random.default_rng(5)
    p = rng.uniform(0.02, 0.6, 20000)
    y = (rng.uniform(size=20000) < p).astype(int)
    assert expected_calibration_error(y, p) < 0.02


def test_ece_notices_a_scorer_that_doubles_every_probability() -> None:
    rng = np.random.default_rng(5)
    p = rng.uniform(0.02, 0.4, 20000)
    y = (rng.uniform(size=20000) < p).astype(int)
    assert expected_calibration_error(y, np.clip(p * 2, 0, 1)) > 0.1


def test_calibration_curve_uses_quantile_bins_so_none_are_empty() -> None:
    y, score, _ = _fold()
    points = calibration_curve_points(y, score, n_bins=10)
    assert len(points) == 10
    assert (points["n"] > 0).all()


# --- reporting -------------------------------------------------------------


def test_pr_auc_is_always_reported_next_to_its_base_rate() -> None:
    y, score, amounts = _fold()
    metrics = evaluate_classifier(y, score, amounts=amounts, n_resamples=50).as_dict()
    assert "base_rate" in metrics
    assert metrics["pr_auc_lift_over_base_rate"] == pytest.approx(
        metrics["pr_auc"] / metrics["base_rate"], rel=1e-2
    )


def test_thin_slices_are_marked_unscorable_rather_than_scored() -> None:
    y, score, _ = _fold(n=400)
    frame = pd.DataFrame({"payer": ["big"] * 380 + ["tiny"] * 20})
    table = slice_metrics(frame, y, score, by="payer", min_positives=20)
    tiny = table.loc[table["slice"] == "tiny"].iloc[0]
    assert tiny["too_thin_to_score"]
    assert pd.isna(tiny["roc_auc"]), "an unscorable slice must report no metric, not a number"
    assert tiny["n"] == 20, "volumes are reported even where the metric is not"


def test_the_report_is_valid_json_for_a_parser_that_is_not_python() -> None:
    """`json.dumps` writes bare NaN, which only Python reads back.

    The Phase 5 API and dashboard consume this file. A slice too thin to score
    produces a NaN legitimately, so it has to survive as `null`.
    """
    import json

    from src.models.train import json_safe

    report = {"slices": [{"roc_auc": float("nan"), "n": np.int64(20), "ok": np.True_}]}
    text = json.dumps(json_safe(report), allow_nan=False)
    reparsed = json.loads(
        text, parse_constant=lambda c: pytest.fail(f"non-standard JSON token: {c}")
    )
    assert reparsed["slices"][0]["roc_auc"] is None
    assert reparsed["slices"][0]["n"] == 20
