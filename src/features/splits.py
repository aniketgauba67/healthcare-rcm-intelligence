"""Temporal splits (CLAUDE.md §4.3: never random, wherever time matters).

The obvious split — hold out the last calendar year — does not work on this
warehouse. Submission dates run 2015-2024 but the tail is thin: 2023 holds about
3.4% of claims and 2024 a handful, so a hold-out-last-year test fold is both tiny
and unrepresentative, and every metric computed on it would come with an
interval too wide to say anything. `docs/simulated_forbidden_columns.md` §8
prescribes a quantile cut instead; at the 80th percentile that lands near
2021-12-28 and yields a forward test fold of roughly 4,170 claims.

The cut is on the date, not on row position: every claim submitted on the cut
date lands on the same side. Splitting mid-day would put two claims from the
same morning in different folds and let the training fold speak for the test one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TemporalSplit:
    """Row masks for one forward-chaining split, plus the boundary that made it."""

    train: np.ndarray
    test: np.ndarray
    cut_date: pd.Timestamp
    time_column: str

    def describe(self, frame: pd.DataFrame, label: str | None = None) -> dict[str, object]:
        """Fold sizes, date ranges and base rates — the evidence for a report."""
        info: dict[str, object] = {
            "cut_date": str(self.cut_date.date()),
            "train_rows": int(self.train.sum()),
            "test_rows": int(self.test.sum()),
            "test_share": round(float(self.test.mean()), 4),
            "train_range": (
                str(frame.loc[self.train, self.time_column].min().date()),
                str(frame.loc[self.train, self.time_column].max().date()),
            ),
            "test_range": (
                str(frame.loc[self.test, self.time_column].min().date()),
                str(frame.loc[self.test, self.time_column].max().date()),
            ),
        }
        if label is not None:
            info["train_base_rate"] = round(float(frame.loc[self.train, label].mean()), 4)
            info["test_base_rate"] = round(float(frame.loc[self.test, label].mean()), 4)
        return info


def quantile_temporal_split(
    frame: pd.DataFrame, time_column: str, train_quantile: float = 0.80
) -> TemporalSplit:
    """Split forward in time at the `train_quantile` of `time_column`.

    Everything on the cut date goes to train, so the test fold is strictly later
    than anything the model was fitted on.
    """
    if not 0.0 < train_quantile < 1.0:
        raise ValueError(f"train_quantile must be in (0, 1), got {train_quantile}")
    times = pd.to_datetime(frame[time_column])
    if times.isna().any():
        raise ValueError(f"{time_column} has nulls; a temporal split cannot place those rows")

    cut = pd.Timestamp(times.quantile(train_quantile)).normalize()
    train = (times <= cut).to_numpy()
    test = ~train
    if not train.any() or not test.any():
        raise ValueError(f"cut at {cut.date()} leaves an empty fold")
    return TemporalSplit(train=train, test=test, cut_date=cut, time_column=time_column)


def forward_chaining_folds(
    frame: pd.DataFrame,
    time_column: str,
    n_folds: int = 4,
    first_train_quantile: float = 0.40,
) -> list[TemporalSplit]:
    """Expanding-window CV: train on the past, validate on the next slice.

    Used for model selection and for bootstrap-free stability checks. Each fold's
    training window is a prefix of time, so no fold is ever fitted on data that
    postdates its own validation slice.
    """
    if n_folds < 2:
        raise ValueError("need at least two folds")
    quantiles = np.linspace(first_train_quantile, 1.0, n_folds + 1)[:-1]
    times = pd.to_datetime(frame[time_column])
    folds: list[TemporalSplit] = []
    for i, q in enumerate(quantiles):
        cut = pd.Timestamp(times.quantile(q)).normalize()
        upper = (
            pd.Timestamp(times.quantile(quantiles[i + 1])).normalize()
            if i + 1 < len(quantiles)
            else times.max()
        )
        train = (times <= cut).to_numpy()
        test = ((times > cut) & (times <= upper)).to_numpy()
        if train.any() and test.any():
            folds.append(
                TemporalSplit(train=train, test=test, cut_date=cut, time_column=time_column)
            )
    return folds


def split_from_config(frame: pd.DataFrame, config: dict) -> TemporalSplit:
    """The project's one canonical train/test split."""
    spec = config["split"]
    if spec.get("strategy") != "temporal":
        raise ValueError(
            f"split.strategy is {spec.get('strategy')!r}; CLAUDE.md §4.3 requires 'temporal'"
        )
    return quantile_temporal_split(
        frame, time_column=spec["time_column"], train_quantile=float(spec["train_quantile"])
    )


def calibration_split(frame: pd.DataFrame, split: TemporalSplit, config: dict) -> TemporalSplit:
    """Carve a calibration fold off the END of the training window.

    Isotonic regression fitted on the same rows the model was fitted on would
    calibrate to the training fit rather than to held-out behaviour. The
    calibration fold is taken temporally, from the late training period, so it is
    both unseen by the estimator and still strictly earlier than the test fold.
    """
    quantile = float(config["split"]["calibration_quantile_of_train"])
    train_frame = frame.loc[split.train]
    inner = quantile_temporal_split(train_frame, split.time_column, quantile)

    fit = np.zeros(len(frame), dtype=bool)
    calibrate = np.zeros(len(frame), dtype=bool)
    train_positions = np.flatnonzero(split.train)
    fit[train_positions[inner.train]] = True
    calibrate[train_positions[inner.test]] = True
    return TemporalSplit(
        train=fit, test=calibrate, cut_date=inner.cut_date, time_column=split.time_column
    )
