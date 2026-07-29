"""Temporal splits: forward in time, atomic on the cut date (CLAUDE.md §4.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.leakage import load_model_config
from src.features.splits import (
    calibration_split,
    forward_chaining_folds,
    quantile_temporal_split,
    split_from_config,
)


@pytest.fixture
def frame() -> pd.DataFrame:
    dates = pd.date_range("2015-01-01", "2024-06-30", periods=1000)
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "sim_submission_date": dates,
            "sim_denial_flag": rng.random(1000) < 0.13,
            "x": rng.normal(size=1000),
        }
    )


def test_folds_partition_the_frame(frame) -> None:
    split = quantile_temporal_split(frame, "sim_submission_date", 0.8)
    assert not (split.train & split.test).any(), "a row is in both folds"
    assert (split.train | split.test).all(), "a row is in neither fold"


def test_test_fold_is_strictly_later_than_train(frame) -> None:
    split = quantile_temporal_split(frame, "sim_submission_date", 0.8)
    latest_train = frame.loc[split.train, "sim_submission_date"].max()
    earliest_test = frame.loc[split.test, "sim_submission_date"].min()
    assert earliest_test > latest_train


def test_the_cut_date_is_not_split_across_folds() -> None:
    """Two claims from the same day must never land on opposite sides.

    Splitting by row position would put one in train and one in test, and the
    training fold would then speak for a day the test fold is being scored on.
    """
    same_day = pd.DataFrame(
        {
            "sim_submission_date": pd.to_datetime(["2024-01-01"] * 40 + ["2024-06-01"] * 10),
            "sim_denial_flag": [False] * 50,
        }
    )
    split = quantile_temporal_split(same_day, "sim_submission_date", 0.8)
    train_dates = set(same_day.loc[split.train, "sim_submission_date"])
    test_dates = set(same_day.loc[split.test, "sim_submission_date"])
    assert not (train_dates & test_dates)


def test_config_split_reproduces_the_documented_geometry(frame) -> None:
    """§8 of the firewall document prescribes the 80/20 quantile cut."""
    config = load_model_config()
    assert config["split"]["strategy"] == "temporal"
    assert config["split"]["train_quantile"] == 0.80
    assert config["split"]["time_column"] == "sim_submission_date"
    split = split_from_config(frame, config)
    assert 0.15 < split.test.mean() < 0.25


def test_a_random_split_is_refused(frame) -> None:
    config = load_model_config()
    config["split"] = {**config["split"], "strategy": "random"}
    with pytest.raises(ValueError, match="temporal"):
        split_from_config(frame, config)


def test_calibration_fold_sits_between_fit_and_test(frame) -> None:
    """Isotonic must be fitted on rows the estimator did not see, and before test."""
    config = load_model_config()
    split = split_from_config(frame, config)
    inner = calibration_split(frame, split, config)

    assert not (inner.train & inner.test).any()
    # The two inner folds together are exactly the outer training fold.
    assert ((inner.train | inner.test) == split.train).all()
    assert not (inner.test & split.test).any(), "calibration fold overlaps the test fold"

    latest_calibration = frame.loc[inner.test, "sim_submission_date"].max()
    earliest_test = frame.loc[split.test, "sim_submission_date"].min()
    assert latest_calibration < earliest_test


def test_forward_chaining_folds_only_ever_train_on_the_past(frame) -> None:
    folds = forward_chaining_folds(frame, "sim_submission_date", n_folds=4)
    assert len(folds) >= 3
    for fold in folds:
        assert (
            frame.loc[fold.test, "sim_submission_date"].min()
            > frame.loc[fold.train, "sim_submission_date"].max()
        )
    # Expanding window: each fold trains on at least as much as the previous one.
    sizes = [int(f.train.sum()) for f in folds]
    assert sizes == sorted(sizes)


def test_nulls_in_the_time_column_are_refused(frame) -> None:
    frame.loc[5, "sim_submission_date"] = pd.NaT
    with pytest.raises(ValueError, match="nulls"):
        quantile_temporal_split(frame, "sim_submission_date", 0.8)


def test_describe_reports_the_evidence(frame) -> None:
    split = quantile_temporal_split(frame, "sim_submission_date", 0.8)
    info = split.describe(frame, label="sim_denial_flag")
    assert set(info) >= {
        "cut_date",
        "train_rows",
        "test_rows",
        "test_share",
        "train_range",
        "test_range",
        "train_base_rate",
        "test_base_rate",
    }
