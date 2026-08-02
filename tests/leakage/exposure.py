"""Which emitted columns are simulated — measured by perturbation, not by name.

QA-OWNED (tests/leakage/ is qa's). This is the instrument behind
`tests/leakage/test_output_surface_provenance.py`, built for [QUEUE-PREFIX] and
for the human's Phase 5 instruction that the provenance/exposure check which
*would* have caught it be re-run and extended so it cannot recur in Phase 5's
outputs — dashboard and API included.

WHY A NEW INSTRUMENT WAS NEEDED
-------------------------------
Two prefix guards already existed and neither could see this defect:

* `tests/contracts/test_view_sim_prefix.py` reads view SQL, so it stops at the
  warehouse boundary;
* `tests/leakage/test_feature_prefix_survival.py` reads the feature specs' DECLARED
  sources, so it covers `src/features/` and nothing downstream.

`src/models/work_queue.py` is downstream of both. It emits
`queue["recoverable_amt"] = frame[recoverable_column]` where `recoverable_column`
defaults to `"sim_denied_amount"` — the marker is stripped by a rename, and the
`sim_` name it came from lives in a FUNCTION SIGNATURE DEFAULT. No static scan of
the assignment can see it, which is precisely why this module measures instead of
reading.

THE MEASUREMENT
---------------
Perturb one declared simulated input; rebuild the surface; see which emitted
columns move. A column that moves is a function of a simulated quantity, whatever
it is called, however many renames and rescalings sit in between. This is the same
shape as the truncation-invariance probe in `test_point_in_time.py` — change the
input, assert on what the output does — and it is immune to the two things that
defeat name matching: a rename, and a name that never appears at the assignment.

The perturbation is applied to a COPY, per column, and a column is "moved" if the
rebuilt values differ anywhere (nulls compared as equal, so a column that is all
null before and after is correctly reported as unmoved).

TWO DEFECTS FOUND BY RUNNING THIS AGAINST THE WORK QUEUE, both fixed here, both
of the kind that would have made the gate green for the wrong reason:

* **Comparing by position measured the SORT, not the content.** `build_work_queue`
  ranks its output, so a perturbed dollar amount reorders every row and *every*
  column differs positionally — including `claim_sk`. That reports 100% dependence
  and proves nothing. Rows are now aligned on the surface's key before comparing,
  and `test_the_key_column_is_not_simulated_derived` is the standing control that
  this has not regressed.
* **A monotone perturbation cannot move a rank-based column.** Scaling an amount
  by a positive factor leaves `qcut` bands, quantile flags and orderings exactly
  where they were, so a derived column built from ranks reads as independent of
  the input it is entirely made of. The numeric perturbation is therefore jittered
  as well as shifted, which breaks the ordering; `test_the_probe_sees_through_a_
  transformation` is the control.

WHAT THE MEASUREMENT DOES NOT DECIDE
------------------------------------
Dependence is measured; the verdict is a judgement, and the judgement is written
down rather than inferred. `queue_position` moves when a simulated dollar moves,
and prefixing it `sim_queue_position` would be absurd — it is our ordering, not a
statement about the simulated world. That is team-lead's QA RULING C boundary
applied to outputs instead of to the training matrix: §3.2 governs simulated
VALUES; a rank, a tier, a recommendation and a build timestamp are metadata about
OUR process. Each surface therefore declares its process-metadata columns WITH A
REASON, in `test_output_surface_provenance.py`, and a test asserts every such
exemption is real and reasoned.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def _perturb(column: pd.Series, rng: np.random.Generator) -> pd.Series:
    """A different value in every row, of a type the column can hold.

    Numeric columns are shifted, scaled AND jittered. The jitter is not decoration:
    without it the perturbation is monotone, and a monotone change leaves every
    rank, quantile band and ordering derived from the column exactly where it was
    — so a column made entirely of the input would read as independent of it.

    Shuffling would be a weaker probe throughout: a shuffle of a constant column
    is the column, and a shuffle can land back where it started.
    """
    if pd.api.types.is_bool_dtype(column):
        return ~column.astype(bool)
    if pd.api.types.is_numeric_dtype(column):
        values = column.astype(float)
        scale = float(values.std(ddof=0)) or 1.0
        return values * 1.7 + 13.0 + rng.normal(0.0, scale, len(values))
    if pd.api.types.is_datetime64_any_dtype(column):
        offsets = rng.integers(11, 400, len(column))
        return pd.to_datetime(column) + pd.to_timedelta(offsets, unit="D")
    values = column.astype(str) + "_x"
    return pd.Series(values, index=column.index)


def _aligned(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Rows in a comparable order.

    Aligning on the key is what stops the probe measuring a SORT. A surface that
    ranks its output reorders every row when a simulated dollar changes, and a
    positional comparison then reports every column — the key included — as
    simulated-derived, which is a measurement of nothing.
    """
    if key in frame.columns:
        return frame.set_index(key).sort_index()
    return frame.reset_index(drop=True)


def _moved(before: pd.Series, after: pd.Series) -> bool:
    if len(before) != len(after):
        return True
    left, right = before, after.reindex(before.index)
    try:
        same = (left == right) | (left.isna() & right.isna())
    except (TypeError, ValueError):
        return not left.astype(str).equals(right.astype(str))
    return not bool(same.all())


@dataclass(frozen=True)
class Surface:
    """A user-facing tabular output, and how to rebuild it with an input changed.

    `build` takes the input frame and returns the emitted table. `simulated_inputs`
    names the inputs whose provenance is SIMULATED: every `sim_`-prefixed column of
    the input frame is included automatically, and `extra_simulated_inputs` covers
    simulated inputs that are not frame columns at all — a model score of a
    simulated outcome, for instance, which arrives as a separate argument and is as
    simulated as anything in the frame.
    """

    name: str
    build: Callable[[pd.DataFrame], pd.DataFrame]
    frame: pd.DataFrame
    key: str = "claim_sk"
    process_metadata: Mapping[str, str] = field(default_factory=dict)
    extra_simulated_inputs: Mapping[str, Callable[[pd.DataFrame], pd.DataFrame]] = field(
        default_factory=dict
    )

    def simulated_input_names(self) -> list[str]:
        names = [c for c in self.frame.columns if c.startswith("sim_")]
        return names + list(self.extra_simulated_inputs)


def simulated_dependence(surface: Surface, seed: int = 20260728) -> dict[str, set[str]]:
    """Emitted column -> the simulated inputs it moves with.

    An empty set means the column did not move under any simulated input, i.e.
    nothing in it came from the simulation.
    """
    rng = np.random.default_rng(seed)
    baseline = _aligned(surface.build(surface.frame), surface.key)
    dependence: dict[str, set[str]] = {c: set() for c in baseline.columns}
    if surface.key in surface.build(surface.frame).columns:
        dependence.setdefault(surface.key, set())

    for name in surface.simulated_input_names():
        if name in surface.extra_simulated_inputs:
            rebuilt_raw = surface.extra_simulated_inputs[name](surface.frame)
        else:
            perturbed = surface.frame.copy()
            perturbed[name] = _perturb(perturbed[name], rng)
            rebuilt_raw = surface.build(perturbed)
        rebuilt = _aligned(rebuilt_raw, surface.key)

        # The key itself is checked as a column: a surface that rewrites its own
        # key under perturbation would make every alignment above meaningless, and
        # the caller's control test needs to be able to see that.
        if surface.key in dependence and not baseline.index.equals(rebuilt.index):
            dependence[surface.key].add(name)

        for column in baseline.columns:
            if column not in rebuilt.columns:
                dependence.setdefault(column, set()).add(name)
                continue
            if _moved(baseline[column], rebuilt[column]):
                dependence[column].add(name)
    return dependence


def unmarked_simulated_columns(
    surface: Surface, dependence: Mapping[str, Iterable[str]] | None = None
) -> dict[str, set[str]]:
    """Emitted columns that carry simulated content and no `sim_` marker.

    The marker test is ABSENCE anywhere in the name, not position — team-lead's
    2026-07-28 ruling on `log_sim_denied_amount`, which this reuses rather than
    reopening. Columns the surface declares as process metadata are excluded; see
    the module docstring for why that boundary exists and where it is recorded.
    """
    measured = dict(dependence or simulated_dependence(surface))
    return {
        column: set(inputs)
        for column, inputs in measured.items()
        if inputs and "sim_" not in column and column not in surface.process_metadata
    }
