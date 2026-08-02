"""Expected Net Recovery, and the rules that are allowed to override it.

The score and the queue are deliberately two different things.

**The score** is Expected Net Recovery:
`P(overturn) x recoverable x recovery_ratio - appeal_processing_cost`.
It is an estimate of what appealing a denial is worth, and it is the right way
to rank claims that are otherwise equivalent.

**The queue** is what a denials team actually works, and it is not just the
score sorted descending. Two things outrank money, and they are applied as
explicit tiers rather than folded into the number:

1. **Deadline urgency.** An appeal filed after the payer's window is worth
   nothing no matter how large. A claim a fortnight from its deadline has to be
   worked now even if a bigger claim scores higher, because the bigger claim will
   still be appealable next week and this one will not.
2. **Mandatory review.** Some denial categories get human eyes regardless of
   expected value — writing off a coverage or coordination-of-benefits denial can
   push balance onto the patient, and a medical-necessity denial is a clinical
   determination, not an arithmetic one.

Folding either of those into the score as a weight or a bonus would be the
easier implementation and the wrong one. A weight can always be outvoted by a
large enough dollar figure, silently, with no way to see it happened — and the
failure mode is a time-barred appeal that the model quietly ranked 400th. As
tiers, the guarantee is structural and testable: no deadline-critical claim can
ever sort below a non-critical one, whatever the scores are.

The tiers, in queue order:

| Tier | Name | Ordered within tier by |
|---|---|---|
| 0 | `DEADLINE_CRITICAL` | days remaining, ascending |
| 1 | `MANDATORY_REVIEW` | expected net recovery, descending |
| 2 | `VALUE` | expected net recovery, descending |
| 3 | `BELOW_COST` | expected net recovery, descending |
| 4 | `EXPIRED` | not appealable; do not work |

`BELOW_COST` claims are kept in the queue rather than dropped. A negative ENR is
a recommendation not to appeal, and the difference between "we decided not to"
and "it never appeared" matters when someone asks later why a claim was written
off.

**Every column this builds is declared** in `src/features/provenance.py:
WORK_QUEUE_SCHEMA`, and `build_work_queue` checks itself against that declaration
before returning. The reason is specific rather than procedural: this table used
to publish `recoverable_amt`, which is `sim_denied_amount` copied out under a
name with the simulated marker removed — one row per claim, next to a
`recommended_action` telling an analyst the recovery was worth chasing. A
dashboard renders those column names as its headers, so an unmarked simulated
dollar amount becomes a user-visible claim about real money. Nothing caught it
because nothing was checking this file; now the check runs inside the builder, so
a column cannot leave here undeclared.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.features.provenance import WORK_QUEUE_SCHEMA, assert_publishable

TIER_ORDER: tuple[str, ...] = (
    "DEADLINE_CRITICAL",
    "MANDATORY_REVIEW",
    "VALUE",
    "BELOW_COST",
    "EXPIRED",
)


@dataclass(frozen=True)
class AppealEconomics:
    """The assumptions the score rests on, in one object so they travel together."""

    processing_cost_usd: float
    recovery_ratio: float
    filing_window_days: int
    deadline_critical_days: int
    mandatory_review_categories: tuple[str, ...]

    @classmethod
    def from_config(cls, config: dict, recovery_ratio: float) -> AppealEconomics:
        """`recovery_ratio` is measured on the training fold, never configured."""
        block = config["appeal_economics"]
        return cls(
            processing_cost_usd=float(block["appeal_processing_cost_usd"]),
            recovery_ratio=float(recovery_ratio),
            filing_window_days=int(block["filing_window_days"]),
            deadline_critical_days=int(block["deadline_critical_days"]),
            mandatory_review_categories=tuple(block["mandatory_review_categories"]),
        )


def fit_recovery_ratio(
    recovered: pd.Series, recoverable: pd.Series, overturned: np.ndarray
) -> float:
    """Mean recovered-to-denied ratio among appeals that were won, on one fold.

    An overturned appeal does not return the whole denied amount. On this
    warehouse the ratio is 0.81 with a standard deviation of 0.12, and it is
    flat: by denial category it runs 0.74-0.83, by denial type 0.809 vs 0.811,
    and its correlation with log denied amount is 0.02. A regression on those
    features would be fitting noise with more parameters, so the expected
    recovery uses the fold mean and the model card reports the spread instead of
    implying a precision the data does not support.
    """
    won = np.asarray(overturned).astype(bool)
    if not won.any():
        raise ValueError("no overturned appeals in the fold; cannot estimate a recovery ratio")
    ratio = (
        recovered[won].astype(float) / recoverable[won].astype(float).replace(0, np.nan)
    ).dropna()
    return float(ratio.mean())


def expected_net_recovery(
    probability: np.ndarray, recoverable: np.ndarray, economics: AppealEconomics
) -> np.ndarray:
    """P(overturn) x recoverable x recovery ratio - processing cost, in dollars."""
    expected = np.asarray(probability, float) * np.asarray(recoverable, float)
    return expected * economics.recovery_ratio - economics.processing_cost_usd


def days_to_deadline(
    denial_review_date: pd.Series, as_of: pd.Timestamp, economics: AppealEconomics
) -> pd.Series:
    """Days left to file, counted from the denial posting, not from today's mood.

    The appeal clock starts at the payer's determination. `as_of` is the moment
    the queue is being built, so this is exactly the number an analyst sees on
    the worklist.
    """
    deadline = pd.to_datetime(denial_review_date) + pd.Timedelta(days=economics.filing_window_days)
    return (deadline - pd.Timestamp(as_of)).dt.days


def assign_tier(
    days_left: pd.Series, denial_category: pd.Series, economics: AppealEconomics
) -> pd.Series:
    """Tier per claim. Order of the checks is the policy; do not reorder casually."""
    tier = pd.Series("VALUE", index=days_left.index, dtype="object")
    tier[np.asarray(days_left) < 0] = "EXPIRED"
    mandatory = denial_category.isin(economics.mandatory_review_categories).to_numpy()
    tier[mandatory & (np.asarray(days_left) >= 0)] = "MANDATORY_REVIEW"
    critical = (np.asarray(days_left) >= 0) & (
        np.asarray(days_left) <= economics.deadline_critical_days
    )
    tier[critical] = "DEADLINE_CRITICAL"
    return tier


def build_work_queue(
    frame: pd.DataFrame,
    probability: np.ndarray,
    economics: AppealEconomics,
    as_of: pd.Timestamp | None = None,
    recoverable_column: str = "sim_denied_amount",
    date_column: str = "sim_denial_review_date",
    category_column: str = "sim_denial_category",
) -> pd.DataFrame:
    """The ranked worklist: score, tier, deadline and reason, one row per denial.

    `as_of` decides what "days left" means, and the two sensible answers are
    genuinely different questions rather than a default and an override.

    * **A date** builds the queue as it stood on that day: a live worklist, where
      a claim denied four months earlier really is out of time. This is the
      snapshot a dashboard shows, and it should be given a frame of currently
      open claims — handing it nine years of history produces a queue that is
      99% `EXPIRED`, which is arithmetically correct and operationally useless.

    * **`None`** builds the queue AT ARRIVAL: every claim is triaged the day its
      denial posts, so each has the full filing window ahead of it. This is the
      right convention for a backtest, where the question is "in what order
      should these have been worked", not "which of them are still alive today".
      Reading a historical backtest through a single as-of date would mark every
      claim expired and answer nothing.
    """
    at_arrival = as_of is None
    as_of = pd.Timestamp(as_of or pd.to_datetime(frame[date_column]).max())
    queue = pd.DataFrame(index=frame.index)
    queue["claim_sk"] = frame["claim_sk"].to_numpy()
    queue["sim_p_overturn"] = np.asarray(probability, float)
    queue["sim_recoverable_amt"] = frame[recoverable_column].astype(float).to_numpy()
    queue["sim_expected_recovery_amt"] = (
        queue["sim_p_overturn"] * queue["sim_recoverable_amt"] * economics.recovery_ratio
    )
    queue["sim_expected_net_recovery"] = expected_net_recovery(
        queue["sim_p_overturn"], queue["sim_recoverable_amt"], economics
    )
    queue["sim_days_to_deadline"] = (
        np.full(len(frame), economics.filing_window_days)
        if at_arrival
        else days_to_deadline(frame[date_column], as_of, economics).to_numpy()
    )
    queue["sim_denial_category"] = frame[category_column].to_numpy()
    queue["tier"] = assign_tier(
        queue["sim_days_to_deadline"], frame[category_column], economics
    ).to_numpy()
    queue.loc[queue["sim_expected_net_recovery"] <= 0, "tier"] = np.where(
        queue.loc[queue["sim_expected_net_recovery"] <= 0, "tier"].isin(
            ["DEADLINE_CRITICAL", "MANDATORY_REVIEW", "EXPIRED"]
        ),
        queue.loc[queue["sim_expected_net_recovery"] <= 0, "tier"],
        "BELOW_COST",
    )

    queue["tier_rank"] = queue["tier"].map({name: i for i, name in enumerate(TIER_ORDER)})
    # Within the deadline tier the ordering is time, not money: the claim about
    # to expire goes first even if it is small. Everywhere else, money.
    queue["_within"] = np.where(
        queue["tier"] == "DEADLINE_CRITICAL",
        queue["sim_days_to_deadline"],
        -queue["sim_expected_net_recovery"],
    )
    queue = queue.sort_values(["tier_rank", "_within"], kind="mergesort").drop(columns=["_within"])
    queue["queue_position"] = np.arange(1, len(queue) + 1)
    queue["recommended_action"] = queue["tier"].map(
        {
            "DEADLINE_CRITICAL": "File now — appeal window closing.",
            "MANDATORY_REVIEW": "Human review required by policy before any write-off.",
            "VALUE": "Appeal — expected recovery exceeds the cost of working it.",
            "BELOW_COST": "Do not appeal on value grounds; expected recovery below cost.",
            "EXPIRED": "Appeal window has closed. Do not work; write off or escalate.",
        }
    )
    queue["as_of"] = as_of
    # Inside the builder, not in a test that has to remember to look: every
    # caller — model_c.py's two CSVs, the deadline-pressure sweep, a Phase 5
    # dashboard — gets the guarantee by construction.
    assert_publishable(queue.columns, WORK_QUEUE_SCHEMA)
    return queue.reset_index(drop=True)


def assert_no_deadline_claim_was_outranked(queue: pd.DataFrame) -> None:
    """The guarantee the tiering exists to provide, as an assertion.

    Cheap enough to run on every build. If it ever fails, the queue has started
    trading a time-barred appeal against a large one, which is the exact failure
    the tiers were built to make impossible.
    """
    positions = queue.groupby("tier", observed=True)["queue_position"].agg(["min", "max"])
    present = [tier for tier in TIER_ORDER if tier in positions.index]
    for earlier, later in zip(present, present[1:], strict=False):
        if positions.loc[earlier, "max"] > positions.loc[later, "min"]:
            raise AssertionError(
                f"{earlier} claim at position {positions.loc[earlier, 'max']} sorts below a "
                f"{later} claim at {positions.loc[later, 'min']} — the tier guarantee is broken"
            )


def priority_score(queue: pd.DataFrame, claim_sk: pd.Series) -> np.ndarray:
    """The tiered queue re-expressed as a score, aligned to `claim_sk`.

    The point is that everything comparable then becomes a score vector, and the
    headline table and its confidence interval can be computed from the SAME
    object. They were not, and it showed: the table compared the TIERED queue
    against a size sort while the paired bootstrap beside it compared the raw
    Expected Net Recovery against the same size sort. Those are different
    rankings, so the reported point difference (-2.2 points) and the reported
    interval's centre (0.0) disagreed, and nothing in the output said why.

    `-queue_position` reproduces the queue exactly, tiers included, and has no
    ties. Claims absent from the queue score NaN rather than silently sorting
    last.
    """
    lookup = pd.Series(
        -queue["queue_position"].to_numpy(dtype=float), index=queue["claim_sk"].to_numpy()
    )
    return pd.Series(claim_sk).map(lookup).to_numpy(dtype=float)


def capture_at_capacity(
    score: np.ndarray, recovered: np.ndarray, capacity_share: float, seed: int = 1337
) -> float:
    """Share of realised recovered dollars sitting in the top `capacity_share` by score.

    Ties break on a seeded shuffle rather than row order, for the same reason as
    the Model A dollar metric: the frame arrives in date order, so a stable sort
    silently means "oldest first" whenever a ranking is flat.
    """
    score = np.asarray(score, float)
    recovered = np.nan_to_num(np.asarray(recovered, float))
    total = float(recovered.sum())
    if total <= 0:
        return float("nan")
    k = max(1, int(round(len(score) * capacity_share)))
    shuffled = np.random.default_rng(seed).permutation(len(score))
    top = shuffled[np.argsort(-score[shuffled], kind="stable")[:k]]
    return float(recovered[top].sum()) / total


def bootstrap_capture_difference(
    score_a: np.ndarray,
    score_b: np.ndarray,
    recovered: np.ndarray,
    capacity_share: float,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 1337,
):  # noqa: ANN201 - returns evaluate.Interval
    """Paired interval for capture(a) - capture(b) on the same resampled claims.

    Without this, "the model queue captured 63.5% and the size-ordered queue
    captured 65.7%" reads as a finding, when on a fold this size and dollars this
    concentrated it is very likely to be noise in either direction. Paired,
    because both rankings are evaluated on identical claims.
    """
    from src.models.evaluate import Interval

    rng = np.random.default_rng(seed)
    score_a, score_b = np.asarray(score_a, float), np.asarray(score_b, float)
    recovered = np.asarray(recovered, float)
    point = capture_at_capacity(score_a, recovered, capacity_share, seed) - capture_at_capacity(
        score_b, recovered, capacity_share, seed
    )
    draws: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, len(score_a), size=len(score_a))
        a = capture_at_capacity(score_a[idx], recovered[idx], capacity_share, seed)
        b = capture_at_capacity(score_b[idx], recovered[idx], capacity_share, seed)
        if not (np.isnan(a) or np.isnan(b)):
            draws.append(a - b)
    low, high = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return Interval(point, float(low), float(high))


def realised_recovery_at_capacity(
    score: np.ndarray,
    recovered: np.ndarray,
    capacity_share: float,
    seed: int = 1337,
) -> dict[str, float]:
    """What the top of a ranking would actually have recovered.

    Ranking metrics say the order is sensible; this says what the order is worth.
    Recovered dollars are an outcome quantity and are joined in at evaluation
    time, never in the feature store.

    Takes a SCORE rather than an already-ordered queue, so that this and
    `capture_at_capacity` — the function the bootstrap resamples — are the same
    computation on the same input. When they were two computations on two
    differently-ordered objects, the table and its interval disagreed.
    """
    score = np.asarray(score, dtype=float)
    realised = np.nan_to_num(np.asarray(recovered, dtype=float))
    k = max(1, int(round(len(score) * capacity_share)))
    shuffled = np.random.default_rng(seed).permutation(len(score))
    top = shuffled[np.argsort(-score[shuffled], kind="stable")[:k]]
    total = float(realised.sum())
    captured = float(realised[top].sum())
    return {
        "queue_rows": int(len(score)),
        "worked": k,
        "capacity_share": capacity_share,
        "recovered_dollars_captured": round(captured, 2),
        "recovered_dollars_total": round(total, 2),
        "share_of_recoverable_captured": round(captured / total, 4) if total > 0 else float("nan"),
    }
