"""Every read the dashboard makes, in one place.

No page opens a data source and no page writes SQL. A page asks for a named
dataset and gets the curated view back unchanged, because the bundle's tables are
`select * from rcm.vw_...` copies written by `src/demo/build.py` — that is what
makes CLAUDE.md §7's "dashboard totals reconcile to SQL control queries" a
property of the pipeline rather than a thing somebody checked once. The moment a
page recomputes a KPI in pandas there are two definitions of it and only one is
under the control queries.

WHY THE CACHE IS HERE AND NOT IN THE PAGES
------------------------------------------
Streamlit re-executes the whole script on every widget interaction. Reading nine
views plus five model tables out of DuckDB per click would spend the free-tier
load budget (§ hard constraint: the demo loads in under ~5 seconds) on work whose
answer cannot change — the bundle is opened read-only and nothing in the process
can write it. So the frames are cached at this layer and every page gets the same
object.

`st.cache_data` returns a copy per caller, which matters more than it looks: a
page that sorts a frame in place would otherwise reorder it for every other page.

TWO BACKENDS, AND THE DIFFERENCE IS ANNOUNCED
---------------------------------------------
`src/demo/source.py` resolves `bundle` (the default, and what a hosted demo runs
on) or `postgres`. The model datasets exist only in the bundle, so
`available()` is checked rather than assumed and a page missing one says which
command produces it instead of raising a table-not-found error at a reader.
"""

from __future__ import annotations

import functools
import json
from typing import Any

import pandas as pd
import streamlit as st

from src.demo import spec
from src.demo.bundle import BundleNotFoundError
from src.demo.source import DataSourceError, get_source

#: Datasets a page may ask for by name. Kept as the declaration in
#: `src/demo/spec.py` rather than a second list, so a dataset cannot be readable
#: here and unclassified there.
DATASETS = tuple(sorted(spec.DATASETS_BY_NAME))
MONITORING_COLUMNS = frozenset(
    {"submission_year_month", "feature_name", "metric_kind", "metric_value", "n_claims"}
)


class DashboardDataError(RuntimeError):
    """A read a page cannot serve, carrying the command that would fix it."""


@st.cache_resource(show_spinner=False)
def _source():  # noqa: ANN202 - DataSource protocol
    try:
        return get_source()
    except (DataSourceError, BundleNotFoundError) as error:
        raise DashboardDataError(str(error)) from error


@st.cache_data(show_spinner=False)
def load(dataset: str) -> pd.DataFrame:
    """One declared dataset, exactly as the curated view or the run produced it."""
    source = _source()
    if dataset not in source.available():
        raise DashboardDataError(
            f"{dataset!r} is not available from the {source.kind} data source. Model "
            "datasets are produced by a training run and ship in the demo bundle — run "
            "`make train && make train-appeal && make demo-extract`, or unset "
            "RCM_DATA_SOURCE to read the bundle."
        )
    return source.frame(dataset)


def available() -> set[str]:
    """What this process can actually answer, for a page that degrades politely."""
    try:
        return _source().available()
    except DashboardDataError:
        return set()


def has(dataset: str) -> bool:
    return dataset in available()


@st.cache_data(show_spinner=False)
def source_description() -> dict[str, Any]:
    """Which bundle (or database) this process is reading, and its build stamp."""
    return dict(_source().describe())


@st.cache_data(show_spinner=False)
def manifest() -> pd.DataFrame:
    """The bundle's own provenance register, one row per dataset.

    Rendered on the Model & data quality page. A reader who wants to know what a
    number on screen is made of should not have to open the repository.
    """
    source = _source()
    if source.kind != "bundle":
        return pd.DataFrame()
    return source.bundle.manifest()


@st.cache_data(show_spinner=False)
def model_metrics(model: str) -> dict[str, Any]:
    """One model's `metrics.json`, verbatim as the training run wrote it.

    Verbatim matters. The dashboard reports what the run reported; it does not
    recompute a headline figure from the shipped scores, because a recomputation
    that lands a thousandth away from `docs/model_card.md` gives a reader two
    numbers and no way to tell which is the model.
    """
    frame = load("model_metrics")
    match = frame.loc[frame["model"] == model]
    if match.empty:
        raise DashboardDataError(
            f"no metrics for model {model!r} in this data source. Available: "
            f"{sorted(frame['model'])}. Run `make train` / `make train-appeal` and rebuild "
            "the bundle with `make demo-extract`."
        )
    return json.loads(match.iloc[0]["metrics_json"])


def usable_model_monitoring(frame: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """Return chartable monitoring rows or an honest unavailable-data reason."""
    missing = sorted(MONITORING_COLUMNS - set(frame.columns))
    if missing:
        return frame.iloc[0:0].copy(), (
            "Model-monitoring data is unavailable because the warehouse view is missing "
            f"required columns: {', '.join(missing)}."
        )
    if frame.empty:
        return frame.copy(), (
            "Model-monitoring data is unavailable because this warehouse contains no "
            "monitoring cohorts yet."
        )

    usable = frame.dropna(
        subset=["submission_year_month", "feature_name", "metric_kind", "metric_value"]
    ).copy()
    if usable.empty:
        return usable, (
            "Model-monitoring data is unavailable because no rows contain the fields "
            "required to draw a monitoring series."
        )
    return usable, None


# ---------------------------------------------------------------------------
# Derived reads every page shares. Each is a projection of a view, never a
# recomputation of one of its measures.
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def executive_totals() -> dict[str, Any]:
    """Book-level KPIs, rolled up from the monthly executive view.

    Uses the API's own `build_executive_summary`, so the screen and the wire
    cannot answer the same question differently. Rates are recomputed from summed
    numerators and denominators rather than averaged across months — a mean of
    monthly denial rates weights a 40-claim month like a 400-claim one and stops
    equalling the control query.
    """
    from src.api.tables import build_executive_summary

    return build_executive_summary(load("vw_executive_rcm_summary"))


@st.cache_data(show_spinner=False)
def model_a_test_fold() -> pd.DataFrame:
    """Model A scores on the FORWARD TEST FOLD only.

    The bundle ships scores for all 20,867 claims because a claim lookup needs
    one, and 16,694 of those are in-sample. Every performance figure on this
    dashboard comes through this function, so no page can report a model's
    accuracy against rows it was fitted on by forgetting a filter.
    """
    scores = load("model_a_scores")
    return scores.loc[scores["fold"] == "test"].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def work_queue(queue_mode: str = "backtest") -> pd.DataFrame:
    """The Model C Expected Net Recovery worklist for one queue mode."""
    queue = load("model_c_work_queue")
    return queue.loc[queue["queue_mode"] == queue_mode].reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def queue_modes() -> tuple[str, ...]:
    return tuple(sorted(load("model_c_work_queue")["queue_mode"].unique()))
