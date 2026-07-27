"""Point-in-time safety, tested behaviourally rather than structurally.

Reading the code and agreeing that it looks backwards is not evidence. These
tests change the future and assert the past does not move:

* **Truncation invariance** — recompute the features on a dataset that has been
  cut off at time *t*. Every feature for every row before *t* must be bit-for-bit
  what it was when the later data was present. If a feature changes, it was
  reading data from after the moment it claims to describe.
* **Future-label blindness** — scramble every label after *t*. Features for rows
  before *t* must not move.
* **Own-label blindness** — flip one claim's own outcome. That claim's own
  features must not move.

Truncation invariance is the strongest of the three: it catches any forward
reach, not only forward reach through the label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.historical import add_prior_period_rates

_ENTITIES = ("P0", "P1", "P2", "P3", "P4")
_DEFINITIONS = {"sim_provider": "prvdr_num", "sim_payer": "sim_payer_id"}
_KWARGS = dict(
    date_column="sim_submission_date",
    outcome_column="sim_denial_flag",
    definitions=_DEFINITIONS,
    embargo_days=60,
    smoothing=20.0,
)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    """A decade of claims with uneven volume per provider, like the real thing."""
    rng = np.random.default_rng(20260727)
    n = 3000
    dates = pd.to_datetime("2015-01-01") + pd.to_timedelta(
        np.sort(rng.integers(0, 3400, size=n)), unit="D"
    )
    providers = rng.choice(_ENTITIES, size=n, p=[0.5, 0.2, 0.15, 0.1, 0.05])
    payers = rng.choice(["PAYER_A", "PAYER_B"], size=n)
    return pd.DataFrame(
        {
            "claim_sk": np.arange(n),
            "sim_submission_date": dates,
            "prvdr_num": providers,
            "sim_payer_id": payers,
            "sim_denial_flag": rng.random(n) < 0.13,
        }
    )


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    built = add_prior_period_rates(frame, **_KWARGS)
    generated = [c for c in built.columns if c.endswith(("_prior_denial_rate", "_prior_claims"))]
    assert generated, "no prior-period columns were produced"
    return built.set_index("claim_sk")[generated]


def _identical(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """Equal including where both are null (NaN != NaN would pass vacuously)."""
    aligned = right.loc[left.index, left.columns]
    return bool(((left == aligned) | (left.isna() & aligned.isna())).all().all())


def test_truncating_the_future_does_not_change_the_past(frame) -> None:
    cut = pd.Timestamp("2021-12-28")
    full = _features(frame)
    truncated = _features(frame.loc[frame["sim_submission_date"] <= cut].copy())
    assert len(truncated) < len(full), "the cut removed nothing; the test would be vacuous"
    assert _identical(truncated, full)


@pytest.mark.parametrize("cut", ["2017-06-01", "2019-01-01", "2021-12-28"])
def test_truncation_invariance_holds_at_several_cuts(frame, cut: str) -> None:
    boundary = pd.Timestamp(cut)
    full = _features(frame)
    truncated = _features(frame.loc[frame["sim_submission_date"] <= boundary].copy())
    assert _identical(truncated, full)


def test_scrambling_future_outcomes_does_not_change_past_features(frame) -> None:
    cut = pd.Timestamp("2021-12-28")
    full = _features(frame)

    scrambled = frame.copy()
    future = scrambled["sim_submission_date"] > cut
    rng = np.random.default_rng(0)
    scrambled.loc[future, "sim_denial_flag"] = rng.permutation(
        scrambled.loc[future, "sim_denial_flag"].to_numpy()
    )
    after = _features(scrambled)

    past = frame.loc[frame["sim_submission_date"] <= cut, "claim_sk"]
    assert _identical(full.loc[past], after)


def test_flipping_a_claims_own_outcome_does_not_change_its_own_features(frame) -> None:
    full = _features(frame)
    target = int(frame["claim_sk"].iloc[1500])

    flipped = frame.copy()
    row = flipped["claim_sk"] == target
    flipped.loc[row, "sim_denial_flag"] = ~flipped.loc[row, "sim_denial_flag"]
    after = _features(flipped)

    assert _identical(full.loc[[target]], after)


def test_the_invariance_tests_can_actually_fail(frame) -> None:
    """A leaky feature must break these checks — otherwise they prove nothing.

    The control is the feature everyone writes by accident: a whole-dataset
    denial rate per provider, computed with no time window at all.
    """
    leaky = frame.assign(
        provider_denial_rate_all_time=frame.groupby("prvdr_num")["sim_denial_flag"].transform(
            "mean"
        )
    ).set_index("claim_sk")[["provider_denial_rate_all_time"]]

    cut = pd.Timestamp("2021-12-28")
    truncated_source = frame.loc[frame["sim_submission_date"] <= cut]
    truncated = truncated_source.assign(
        provider_denial_rate_all_time=truncated_source.groupby("prvdr_num")[
            "sim_denial_flag"
        ].transform("mean")
    ).set_index("claim_sk")[["provider_denial_rate_all_time"]]

    assert not _identical(truncated, leaky), (
        "the whole-dataset rate survived truncation, so these tests are not sensitive"
    )


def test_prior_features_are_null_before_any_history_exists(frame) -> None:
    """The opening rows must say "unknown", not "clean"."""
    built = add_prior_period_rates(frame, **_KWARGS)
    earliest = built.nsmallest(5, "sim_submission_date")
    assert earliest["sim_provider_prior_denial_rate"].isna().all()
    assert (earliest["sim_provider_prior_claims"] == 0).all()
