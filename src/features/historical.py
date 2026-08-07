"""Prior-period historical rates (docs/project_rules.md §4.2).

A provider's clean-claim rate and a payer's denial propensity are the two
features a denials manager would actually ask for, and both are computed from
the outcome column. That makes them the most dangerous features in the store:
computed naively over the whole dataset, a provider's denial rate includes the
very claim being scored, and the model learns to read its own answer.

Two mechanisms keep them honest here.

**Prior period.** For a claim submitted on day *t*, the rate is computed only
from claims submitted strictly before *t*. §4.2 allows prior-period *or*
out-of-fold logic; prior period is the stronger of the two and the one that
matches production, because it excludes not just the row's own outcome but
everything contemporaneous with it.

**Embargo.** Submitted before *t* is not the same as *known* before *t*. A claim
submitted a week ago has not come back from the payer yet, so its outcome is not
on the screen when the next claim goes out. The window therefore ends at
*t − embargo_days*, with the embargo set from the payer adjudication and posting
cycle in `config/model.yaml`, not fitted to the data — deriving it from observed
adjudication timing would be reading a forbidden column to design a feature.

Rates are shrunk toward the prior-period *global* rate, which matters more than
usual here: the median provider in this warehouse has two claims, so most
provider rates are estimated from almost nothing and an unshrunk rate would be
mostly noise with a few 0.0/1.0 spikes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PriorPeriodResult:
    """The three columns a historical rate produces."""

    rate: pd.Series  # smoothed prior rate, aligned to the input index
    count: pd.Series  # how many prior claims it is based on
    raw_rate: pd.Series  # unsmoothed; NaN where there is no history at all


def _as_list(key: str | Sequence[str] | None) -> list[str]:
    if key is None:
        return []
    return [key] if isinstance(key, str) else list(key)


def _cumulative_by_date(
    frame: pd.DataFrame, keys: list[str], date_column: str, outcome_column: str
) -> pd.DataFrame:
    """Per (key, date): running totals INCLUSIVE of that date.

    Aggregating to the date before accumulating is what makes same-day claims
    invisible to one another. Accumulating row by row would let a claim see
    another claim submitted the same morning, whose outcome nobody has yet.
    """
    grouped = (
        frame.groupby([*keys, date_column], sort=True, observed=True)[outcome_column]
        .agg(["sum", "count"])
        .reset_index()
    )
    if keys:
        grouped[["cum_sum", "cum_count"]] = grouped.groupby(keys, observed=True)[
            ["sum", "count"]
        ].cumsum()
    else:
        grouped[["cum_sum", "cum_count"]] = grouped[["sum", "count"]].cumsum()
    return grouped[[*keys, date_column, "cum_sum", "cum_count"]]


def _lookup_at_cutoff(
    frame: pd.DataFrame,
    cumulative: pd.DataFrame,
    keys: list[str],
    date_column: str,
    embargo_days: int,
) -> pd.DataFrame:
    """For each row, the running totals as of its embargoed cutoff date."""
    left = pd.DataFrame(
        {
            "_row": np.arange(len(frame)),
            "_cutoff": frame[date_column] - pd.Timedelta(days=embargo_days),
        }
    )
    for key in keys:
        left[key] = frame[key].to_numpy()
    left = left.sort_values("_cutoff", kind="mergesort")
    right = cumulative.sort_values(date_column, kind="mergesort")

    merged = pd.merge_asof(
        left,
        right,
        left_on="_cutoff",
        right_on=date_column,
        by=keys or None,
        direction="backward",
        allow_exact_matches=True,
    )
    return merged.sort_values("_row", kind="mergesort").reset_index(drop=True)


def prior_period_rate(
    frame: pd.DataFrame,
    date_column: str,
    outcome_column: str,
    key: str | Sequence[str] | None = None,
    embargo_days: int = 60,
    smoothing: float = 20.0,
    global_rate: pd.Series | None = None,
) -> PriorPeriodResult:
    """Rate of `outcome_column` over claims strictly before each row's cutoff.

    `smoothing` is the m-estimate weight: an entity with `smoothing` prior claims
    gets half its own rate and half the global rate. With the default of 20 a
    two-claim provider is pulled almost entirely to the global rate, which is the
    honest thing to do with two observations.

    `global_rate` supplies the shrinkage target; when omitted it is computed the
    same prior-period way over the whole frame, so the target is never a
    full-dataset statistic.
    """
    if not np.issubdtype(frame[date_column].dtype, np.datetime64):
        raise TypeError(f"{date_column} must be datetime64, got {frame[date_column].dtype}")
    if embargo_days < 1:
        # At zero the cutoff is the row's own submission date, and the window
        # would include the row itself. The guard is here rather than in config
        # validation because this is the function that would leak.
        raise ValueError("embargo_days must be at least 1; a zero embargo includes the row itself")
    keys = _as_list(key)

    cumulative = _cumulative_by_date(frame, keys, date_column, outcome_column)
    matched = _lookup_at_cutoff(frame, cumulative, keys, date_column, embargo_days)

    count = matched["cum_count"].fillna(0.0).to_numpy()
    total = matched["cum_sum"].fillna(0.0).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(count > 0, total / np.where(count > 0, count, 1.0), np.nan)

    if global_rate is None:
        # An unkeyed rate IS the global rate, so it shrinks toward itself (a
        # no-op). A keyed rate shrinks toward the unkeyed one, computed over the
        # same embargoed window so the target is never a full-dataset statistic.
        global_rate = (
            prior_period_rate(
                frame,
                date_column=date_column,
                outcome_column=outcome_column,
                key=None,
                embargo_days=embargo_days,
                smoothing=0.0,
            ).raw_rate
            if keys
            else pd.Series(raw, index=frame.index)
        )

    target = np.asarray(global_rate, dtype=float)
    if smoothing > 0:
        # Where there is no global target yet (the very first claims in the
        # warehouse) the smoothed rate stays NaN rather than silently becoming
        # zero. The imputer, fitted on the training fold only, fills it.
        smoothed = (total + smoothing * target) / (count + smoothing)
    else:
        smoothed = raw

    index = frame.index
    return PriorPeriodResult(
        rate=pd.Series(smoothed, index=index, dtype=float),
        count=pd.Series(count, index=index, dtype=float),
        raw_rate=pd.Series(raw, index=index, dtype=float),
    )


def add_prior_period_rates(
    frame: pd.DataFrame,
    date_column: str,
    outcome_column: str,
    definitions: dict[str, str | Sequence[str]],
    embargo_days: int = 60,
    smoothing: float = 20.0,
) -> pd.DataFrame:
    """Attach a `{prefix}_prior_denial_rate` / `_prior_claims` pair per entity.

    All rates shrink toward one shared prior-period global rate, computed once so
    every entity is pulled toward the same moving target and emitted as
    `sim_overall_prior_denial_rate`.
    """
    global_rate = prior_period_rate(
        frame,
        date_column=date_column,
        outcome_column=outcome_column,
        key=None,
        embargo_days=embargo_days,
        smoothing=0.0,
    ).raw_rate

    out = frame.copy()
    # The book rate carries the `sim_` prefix (docs/project_rules.md §3.2) because it is an
    # aggregate of a SIMULATED outcome column, even though its keys are dates.
    # Every entity rate below inherits the prefix from its `definitions` key; this
    # one has no entity to inherit from, so the name states it directly. An
    # unprefixed "overall_prior_denial_rate" sitting in the committed matrix would
    # read to an outsider as a real Medicare denial rate, which is exactly the
    # misreading the prefix rule exists to prevent.
    out["sim_overall_prior_denial_rate"] = global_rate
    for prefix, key in definitions.items():
        result = prior_period_rate(
            frame,
            date_column=date_column,
            outcome_column=outcome_column,
            key=key,
            embargo_days=embargo_days,
            smoothing=smoothing,
            global_rate=global_rate,
        )
        out[f"{prefix}_prior_denial_rate"] = result.rate
        out[f"{prefix}_prior_claims"] = result.count
    return out
