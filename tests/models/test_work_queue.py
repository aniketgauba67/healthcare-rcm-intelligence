"""The work queue's guarantees, tested as behaviour rather than as structure.

The one that matters is the deadline guarantee. Expected Net Recovery is a
dollar figure, and if urgency were folded into it as a weight or a bonus then a
large enough claim could always outvote a claim about to time out — silently,
with nothing in the output to show it happened, and the failure mode is a
time-barred appeal the model quietly ranked four hundredth. The tiers exist to
make that impossible rather than unlikely, so the tests below construct exactly
that adversarial case instead of checking that the tier column has the right
values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.work_queue import (
    AppealEconomics,
    assert_no_deadline_claim_was_outranked,
    build_work_queue,
    capture_at_capacity,
    expected_net_recovery,
    fit_recovery_ratio,
    priority_score,
    realised_recovery_at_capacity,
)

ECONOMICS = AppealEconomics(
    processing_cost_usd=45.0,
    recovery_ratio=0.8,
    filing_window_days=120,
    deadline_critical_days=14,
    mandatory_review_categories=("MEDICAL_NECESSITY",),
)


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_a_tiny_expiring_claim_outranks_a_huge_comfortable_one() -> None:
    """The adversarial case the tiering exists for.

    $200 with three days left against $500,000 with four months left. Any scheme
    that scores urgency as a weight puts the half-million first; the correct
    answer is that the half-million will still be appealable next week.
    """
    as_of = pd.Timestamp("2023-06-01")
    frame = _frame(
        [
            {
                "claim_sk": 1,
                "sim_denied_amount": 200.0,
                "sim_denial_review_date": as_of - pd.Timedelta(days=117),
                "sim_denial_category": "BUNDLING",
            },
            {
                "claim_sk": 2,
                "sim_denied_amount": 500_000.0,
                "sim_denial_review_date": as_of - pd.Timedelta(days=1),
                "sim_denial_category": "BUNDLING",
            },
        ]
    )
    queue = build_work_queue(frame, np.array([0.5, 0.9]), ECONOMICS, as_of=as_of)
    assert_no_deadline_claim_was_outranked(queue)

    first = queue.iloc[0]
    assert first["claim_sk"] == 1
    assert first["tier"] == "DEADLINE_CRITICAL"
    # And the big claim really did score far higher, so the tier is what moved it.
    assert (
        queue.set_index("claim_sk").loc[2, "sim_expected_net_recovery"]
        > first["sim_expected_net_recovery"]
    )


def test_the_guarantee_holds_however_extreme_the_scores() -> None:
    """Property-style: random claims, random probabilities, guarantee every time."""
    rng = np.random.default_rng(20260727)
    as_of = pd.Timestamp("2023-06-01")
    for _ in range(50):
        n = int(rng.integers(5, 60))
        frame = _frame(
            [
                {
                    "claim_sk": i,
                    "sim_denied_amount": float(rng.lognormal(6, 3)),
                    "sim_denial_review_date": as_of - pd.Timedelta(days=int(rng.integers(0, 200))),
                    "sim_denial_category": str(
                        rng.choice(["BUNDLING", "MEDICAL_NECESSITY", "CODING_INVALID"])
                    ),
                }
                for i in range(n)
            ]
        )
        queue = build_work_queue(frame, rng.uniform(size=n), ECONOMICS, as_of=as_of)
        assert_no_deadline_claim_was_outranked(queue)


def test_an_expired_claim_is_kept_and_marked_not_dropped() -> None:
    """ "We decided not to" and "it never appeared" are different answers later."""
    as_of = pd.Timestamp("2023-06-01")
    frame = _frame(
        [
            {
                "claim_sk": 1,
                "sim_denied_amount": 9_000.0,
                "sim_denial_review_date": as_of - pd.Timedelta(days=400),
                "sim_denial_category": "BUNDLING",
            }
        ]
    )
    queue = build_work_queue(frame, np.array([0.9]), ECONOMICS, as_of=as_of)
    assert queue.loc[0, "tier"] == "EXPIRED"
    assert "Do not work" in queue.loc[0, "recommended_action"]


def test_a_mandatory_review_category_survives_a_negative_score() -> None:
    """Policy outranks arithmetic: a $10 medical-necessity denial is still reviewed."""
    as_of = pd.Timestamp("2023-06-01")
    frame = _frame(
        [
            {
                "claim_sk": 1,
                "sim_denied_amount": 10.0,
                "sim_denial_review_date": as_of - pd.Timedelta(days=30),
                "sim_denial_category": "MEDICAL_NECESSITY",
            },
            {
                "claim_sk": 2,
                "sim_denied_amount": 10.0,
                "sim_denial_review_date": as_of - pd.Timedelta(days=30),
                "sim_denial_category": "BUNDLING",
            },
        ]
    )
    queue = build_work_queue(frame, np.array([0.5, 0.5]), ECONOMICS, as_of=as_of).set_index(
        "claim_sk"
    )
    assert queue.loc[1, "sim_expected_net_recovery"] < 0
    assert queue.loc[1, "tier"] == "MANDATORY_REVIEW"
    assert queue.loc[2, "tier"] == "BELOW_COST"


def test_processing_cost_moves_the_cutoff_and_not_the_order() -> None:
    """The claim config/model.yaml makes about the appeal cost, as an assertion."""
    probability = np.array([0.2, 0.5, 0.9])
    recoverable = np.array([1_000.0, 4_000.0, 2_000.0])
    cheap = expected_net_recovery(probability, recoverable, ECONOMICS)
    dear = expected_net_recovery(
        probability,
        recoverable,
        AppealEconomics(**{**vars(ECONOMICS), "processing_cost_usd": 500.0}),
    )
    assert list(np.argsort(-cheap)) == list(np.argsort(-dear))
    assert (dear < cheap).all()


def test_the_priority_score_reproduces_the_queue_exactly() -> None:
    """The fix for the table and its interval measuring different objects."""
    as_of = pd.Timestamp("2023-06-01")
    rng = np.random.default_rng(7)
    frame = _frame(
        [
            {
                "claim_sk": i,
                "sim_denied_amount": float(rng.lognormal(7, 2)),
                "sim_denial_review_date": as_of - pd.Timedelta(days=int(rng.integers(0, 130))),
                "sim_denial_category": str(rng.choice(["BUNDLING", "MEDICAL_NECESSITY"])),
            }
            for i in range(40)
        ]
    )
    queue = build_work_queue(frame, rng.uniform(size=40), ECONOMICS, as_of=as_of)
    score = priority_score(queue, frame["claim_sk"])
    ordered = frame["claim_sk"].to_numpy()[np.argsort(-score, kind="stable")]
    assert list(ordered) == list(queue["claim_sk"])


def test_capture_and_realised_recovery_agree_on_the_same_score() -> None:
    """The point estimate and the bootstrapped quantity must be one computation.

    They were two, on two differently-ordered objects, and the report showed a
    -2.2 point difference next to an interval centred on zero.
    """
    rng = np.random.default_rng(11)
    score = rng.normal(size=200)
    recovered = rng.lognormal(6, 1.5, size=200)
    reported = realised_recovery_at_capacity(score, recovered, 0.1)
    bootstrapped = capture_at_capacity(score, recovered, 0.1)
    assert reported["share_of_recoverable_captured"] == pytest.approx(bootstrapped, abs=1e-4)


def test_recovery_ratio_refuses_a_fold_with_no_wins() -> None:
    """Silently returning nan would put nan through every downstream dollar figure."""
    with pytest.raises(ValueError, match="no overturned appeals"):
        fit_recovery_ratio(
            pd.Series([0.0, 0.0]), pd.Series([100.0, 100.0]), np.array([False, False])
        )


def test_ties_do_not_secretly_mean_oldest_first() -> None:
    """A constant scorer must capture roughly its share, not the head of a sorted frame.

    The frame arrives in date order, so a stable sort on a flat ranking silently
    becomes "work the oldest claims" — which is a real strategy with a real
    (different) result, reported under the name of a random one.
    """
    n = 1_000
    recovered = np.zeros(n)
    recovered[:100] = 1_000.0  # all the money sits at the head of the frame
    captured = capture_at_capacity(np.ones(n), recovered, 0.1)
    assert captured < 0.5, "a constant score is behaving like oldest-first"
