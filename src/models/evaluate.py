"""Metrics, intervals, slices and the operating threshold.

Three things here are easy to get subtly wrong and are therefore spelled out.

**PR-AUC has no fixed floor.** ROC-AUC 0.5 is a coin; PR-AUC 0.5 could be
excellent or terrible depending on prevalence. Every PR-AUC reported here is
accompanied by the base rate, which is the value a random scorer achieves.

**Intervals matter more than points.** The test fold is 4,173 claims with about
500 denials, so the difference between two models at the third decimal place is
noise. Bootstrap intervals are resampled at the *claim* level on the test fold;
a difference whose interval spans zero is reported as no difference.

**Dollars, not just ranking.** A denial-risk model that ranks well but only
finds cheap denials is worth nothing. `dollars_at_risk_captured` measures the
share of denied dollars sitting in the top decile of predicted risk, which is
what a work queue actually delivers.

Outcome amounts used below are evaluation quantities, not features: they are
read from the warehouse only inside these functions, and the leakage guard keeps
them out of the matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)


@dataclass(frozen=True)
class Interval:
    """A point estimate with a percentile bootstrap interval."""

    point: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.point:.4f} [{self.low:.4f}, {self.high:.4f}]"

    def as_dict(self) -> dict[str, float]:
        return {"point": self.point, "ci_low": self.low, "ci_high": self.high}


@dataclass(frozen=True)
class ClassificationMetrics:
    """The headline numbers for one model on one fold."""

    n: int
    positives: int
    base_rate: float
    roc_auc: float
    pr_auc: float
    brier: float
    pr_auc_lift_over_base_rate: float
    dollars_at_risk_captured_top_decile: float | None = None
    intervals: dict[str, Interval] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "n": self.n,
            "positives": self.positives,
            "base_rate": round(self.base_rate, 4),
            "roc_auc": round(self.roc_auc, 4),
            "pr_auc": round(self.pr_auc, 4),
            "pr_auc_lift_over_base_rate": round(self.pr_auc_lift_over_base_rate, 3),
            "brier": round(self.brier, 5),
        }
        if self.dollars_at_risk_captured_top_decile is not None:
            out["dollars_at_risk_captured_top_decile"] = round(
                self.dollars_at_risk_captured_top_decile, 4
            )
        for name, interval in self.intervals.items():
            out[f"{name}_ci"] = [round(interval.low, 4), round(interval.high, 4)]
        return out


def dollars_at_risk_captured(
    y_true: np.ndarray, y_score: np.ndarray, amounts: np.ndarray, top_fraction: float = 0.10
) -> float:
    """Share of denied dollars falling in the top `top_fraction` by predicted risk.

    The denominator is all denied dollars in the fold, so the number answers "if
    the team can only work 10% of the queue, how much of the money at risk do
    they see?". A perfect ranker does not reach 1.0 unless the denied dollars fit
    inside the decile, which is the honest ceiling.
    """
    if not 0 < top_fraction <= 1:
        raise ValueError("top_fraction must be in (0, 1]")
    total_at_risk = float(np.sum(amounts * (y_true == 1)))
    if total_at_risk <= 0:
        return float("nan")
    k = max(1, int(round(len(y_score) * top_fraction)))
    top = np.argsort(-y_score, kind="stable")[:k]
    captured = float(np.sum(amounts[top] * (y_true[top] == 1)))
    return captured / total_at_risk


def bootstrap_interval(
    metric_fn,
    y_true: np.ndarray,
    y_score: np.ndarray,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 1337,
) -> Interval:
    """Percentile bootstrap over claims.

    Resamples that contain only one class are skipped rather than scored: AUC is
    undefined there, and counting them as 0.5 would drag every interval toward
    the middle.
    """
    rng = np.random.default_rng(seed)
    n = len(y_true)
    point = float(metric_fn(y_true, y_score))
    draws: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        sample_y = y_true[idx]
        if sample_y.min() == sample_y.max():
            continue
        draws.append(float(metric_fn(sample_y, y_score[idx])))
    if not draws:  # pragma: no cover - only with a degenerate fold
        return Interval(point, float("nan"), float("nan"))
    low, high = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return Interval(point, float(low), float(high))


def evaluate_classifier(
    y_true: np.ndarray,
    y_score: np.ndarray,
    amounts: np.ndarray | None = None,
    n_resamples: int = 1000,
    seed: int = 1337,
    with_intervals: bool = True,
) -> ClassificationMetrics:
    """The headline metric block for one model on one fold."""
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    base_rate = float(y_true.mean())
    pr_auc = float(average_precision_score(y_true, y_score))

    intervals: dict[str, Interval] = {}
    if with_intervals:
        intervals["roc_auc"] = bootstrap_interval(
            roc_auc_score, y_true, y_score, n_resamples, seed=seed
        )
        intervals["pr_auc"] = bootstrap_interval(
            average_precision_score, y_true, y_score, n_resamples, seed=seed
        )

    return ClassificationMetrics(
        n=len(y_true),
        positives=int(y_true.sum()),
        base_rate=base_rate,
        roc_auc=float(roc_auc_score(y_true, y_score)),
        pr_auc=pr_auc,
        brier=float(brier_score_loss(y_true, y_score)),
        pr_auc_lift_over_base_rate=pr_auc / base_rate if base_rate > 0 else float("nan"),
        dollars_at_risk_captured_top_decile=(
            dollars_at_risk_captured(y_true, y_score, np.asarray(amounts, dtype=float))
            if amounts is not None
            else None
        ),
        intervals=intervals,
    )


def paired_bootstrap_difference(
    metric_fn,
    y_true: np.ndarray,
    score_a: np.ndarray,
    score_b: np.ndarray,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 1337,
) -> Interval:
    """Interval for metric(a) − metric(b) on the SAME resampled claims.

    Paired, because the two models are scored on identical rows and an unpaired
    interval would attribute their shared sampling noise to the difference. An
    interval spanning zero means the data cannot tell the two models apart, which
    is a finding, not a failure.
    """
    rng = np.random.default_rng(seed)
    n = len(y_true)
    point = float(metric_fn(y_true, score_a)) - float(metric_fn(y_true, score_b))
    draws: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        sample_y = y_true[idx]
        if sample_y.min() == sample_y.max():
            continue
        draws.append(
            float(metric_fn(sample_y, score_a[idx])) - float(metric_fn(sample_y, score_b[idx]))
        )
    low, high = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return Interval(point, float(low), float(high))


def calibration_curve_points(
    y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10
) -> pd.DataFrame:
    """Predicted vs observed rate per quantile bin, with counts.

    Quantile bins rather than equal-width: predictions cluster near the base rate,
    and equal-width bins would leave most of the range empty and the plot
    dominated by two bins holding almost every claim.
    """
    frame = pd.DataFrame({"y": np.asarray(y_true).astype(int), "p": np.asarray(y_score, float)})
    frame["bin"] = pd.qcut(frame["p"].rank(method="first"), q=n_bins, labels=False)
    grouped = frame.groupby("bin", observed=True).agg(
        n=("y", "size"),
        mean_predicted=("p", "mean"),
        observed_rate=("y", "mean"),
    )
    grouped["gap"] = grouped["mean_predicted"] - grouped["observed_rate"]
    return grouped.reset_index()


def expected_calibration_error(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10) -> float:
    """Count-weighted mean absolute gap between predicted and observed rates."""
    points = calibration_curve_points(y_true, y_score, n_bins)
    weights = points["n"] / points["n"].sum()
    return float((weights * points["gap"].abs()).sum())


def slice_metrics(
    frame: pd.DataFrame,
    y_true: np.ndarray,
    y_score: np.ndarray,
    by: str,
    min_positives: int = 20,
) -> pd.DataFrame:
    """Per-slice metrics with volumes, and an explicit too-thin-to-score marker.

    Volumes are always shown alongside rates. Per-service-line denial ranking in
    this data is weak and noisy — the source DRG mix is concentrated and several
    service lines are small — so a rate without its denominator invites a
    confident conclusion the data does not support.
    """
    work = pd.DataFrame(
        {
            "slice": frame[by].astype("string"),
            "y": np.asarray(y_true).astype(int),
            "p": np.asarray(y_score, dtype=float),
        }
    )
    rows: list[dict[str, object]] = []
    for value, group in work.groupby("slice", observed=True):
        positives = int(group["y"].sum())
        scorable = positives >= min_positives and positives < len(group)
        rows.append(
            {
                "slice": value,
                "n": len(group),
                "positives": positives,
                "base_rate": round(float(group["y"].mean()), 4),
                "mean_predicted": round(float(group["p"].mean()), 4),
                "roc_auc": (
                    round(float(roc_auc_score(group["y"], group["p"])), 4) if scorable else None
                ),
                "pr_auc": (
                    round(float(average_precision_score(group["y"], group["p"])), 4)
                    if scorable
                    else None
                ),
                "brier": round(float(brier_score_loss(group["y"], group["p"])), 5),
                "too_thin_to_score": not scorable,
            }
        )
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


@dataclass(frozen=True)
class ThresholdChoice:
    """An operating point and the economics behind it."""

    threshold: float
    expected_net_benefit: float
    flagged: int
    flagged_share: float
    denials_caught: int
    recall: float
    precision: float
    review_cost_usd: float


def threshold_from_cost_matrix(
    y_true: np.ndarray,
    y_score: np.ndarray,
    amounts: np.ndarray,
    review_cost_usd: float,
    prevented_value_multiplier: float = 1.0,
    candidates: int = 200,
) -> ThresholdChoice:
    """Pick the score threshold that maximises realised net benefit.

    Net benefit of reviewing a flagged claim is
    `prevented_value_multiplier × amount_at_risk − review_cost_usd` when the
    claim would have been denied, and `−review_cost_usd` when it would not.

    The multiplier is the assumption to argue with: at 1.0 it says a pre-submission
    review always prevents the denial, which is an optimistic ceiling rather than
    a forecast. It is a config knob (`cost_matrix.prevented_denial_value_multiplier`)
    for exactly that reason, and the model card states it.

    Chosen on the calibration fold and then applied unchanged to the test fold —
    picking it on test would be choosing the operating point with the answers in
    hand.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    amounts = np.asarray(amounts, dtype=float)
    value = np.where(
        y_true == 1, prevented_value_multiplier * amounts - review_cost_usd, -review_cost_usd
    )

    grid = np.unique(np.quantile(y_score, np.linspace(0.0, 1.0, candidates)))
    best = max(
        grid, key=lambda t: float(value[y_score >= t].sum()) if (y_score >= t).any() else 0.0
    )

    flagged = y_score >= best
    caught = int(y_true[flagged].sum())
    return ThresholdChoice(
        threshold=float(best),
        expected_net_benefit=float(value[flagged].sum()),
        flagged=int(flagged.sum()),
        flagged_share=float(flagged.mean()),
        denials_caught=caught,
        recall=float(caught / max(1, int(y_true.sum()))),
        precision=float(caught / max(1, int(flagged.sum()))),
        review_cost_usd=float(review_cost_usd),
    )


def apply_threshold(
    y_true: np.ndarray,
    y_score: np.ndarray,
    amounts: np.ndarray,
    choice: ThresholdChoice,
    prevented_value_multiplier: float = 1.0,
) -> ThresholdChoice:
    """Re-measure a threshold chosen elsewhere on this fold."""
    y_true = np.asarray(y_true).astype(int)
    flagged = np.asarray(y_score, dtype=float) >= choice.threshold
    amounts = np.asarray(amounts, dtype=float)
    value = np.where(
        y_true == 1,
        prevented_value_multiplier * amounts - choice.review_cost_usd,
        -choice.review_cost_usd,
    )
    caught = int(y_true[flagged].sum())
    return ThresholdChoice(
        threshold=choice.threshold,
        expected_net_benefit=float(value[flagged].sum()),
        flagged=int(flagged.sum()),
        flagged_share=float(flagged.mean()),
        denials_caught=caught,
        recall=float(caught / max(1, int(y_true.sum()))),
        precision=float(caught / max(1, int(flagged.sum()))),
        review_cost_usd=choice.review_cost_usd,
    )
