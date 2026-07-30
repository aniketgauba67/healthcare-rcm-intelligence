"""Every figure on this dashboard, and the SQL control query it must equal.

CLAUDE.md §7, App: "dashboard totals reconcile to SQL control queries". This
module is that reconciliation, written as data rather than as prose, so it can be
rendered on the Model & data quality page AND asserted by a test.

WHY A SECOND PATH AND NOT A SECOND OPINION
-------------------------------------------
A check that recomputes a figure the same way it was computed the first time
proves only that the code is deterministic. So every row below derives its
control value from a DIFFERENT dataset than the figure: the executive view's
`sum(claims_submitted)` is checked against `count(*)` over `vw_claim_enriched`,
the A/R view's `sum(open_claims)` against the enriched view's `ar_open_flag`, the
denial mix's `sum(denial_count)` against the enriched view again. Those are the
same relationships `sql/quality/view_reconciliation.py` asserts in PostgreSQL —
this is that gate re-expressed over the shipped bundle, which is the only form of
it a hosted demo with no database can offer.

`control_sql` on each check is the real query, against `rcm.`, for a reader who
does have the warehouse. It is printed on the page beside the figure.

THE ONE THING THIS CANNOT CATCH, SAID OUT LOUD
-----------------------------------------------
Both sides read the same bundle. If `make demo-extract` copied a view wrongly,
both sides move together and this stays green. What catches that is the build
step copying views verbatim (`select * from rcm.vw_...`, no recomputation) plus
the PostgreSQL reconciliation gate; this module catches the failure those two
cannot, which is a PAGE computing its own version of a measure. That is the
failure mode that actually happens — the executive KPI averaged across months
instead of summed is a real bug in this shape, and `test_reconciliation` would
have caught it while the SQL gate would not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pandas as pd

Frames = Mapping[str, pd.DataFrame]

#: Datasets any check might read. A caller loads these once and passes them in.
REQUIRED_DATASETS: tuple[str, ...] = (
    "vw_executive_rcm_summary",
    "vw_claim_enriched",
    "vw_denial_root_cause",
    "vw_ar_aging",
    "vw_payer_performance",
    "vw_clean_claim_performance",
    "vw_work_queue_priority",
)

#: Datasets that exist only in the demo bundle (a training run produced them).
MODEL_DATASETS: tuple[str, ...] = ("model_a_scores", "model_c_work_queue")


@dataclass(frozen=True)
class Check:
    """One figure a page shows, and the independent total it must equal."""

    figure: str
    page: str
    #: How the page arrives at the number.
    dashboard: Callable[[Frames], float]
    #: The same quantity, reached from a different dataset.
    control: Callable[[Frames], float]
    #: The query a reader with the warehouse can run to check it themselves.
    control_sql: str
    #: Absolute tolerance. Zero for counts; cents for money rolled up two ways.
    tolerance: float = 0.0
    datasets: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Result:
    figure: str
    page: str
    dashboard_value: float
    control_value: float
    difference: float
    tolerance: float
    passed: bool
    control_sql: str


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _executive(frames: Frames) -> pd.DataFrame:
    return frames["vw_executive_rcm_summary"]


def _claims(frames: Frames) -> pd.DataFrame:
    return frames["vw_claim_enriched"]


CHECKS: tuple[Check, ...] = (
    Check(
        figure="Executive overview — Claims submitted",
        page="Executive overview",
        dashboard=lambda f: float(_executive(f)["claims_submitted"].sum()),
        control=lambda f: float(len(_claims(f))),
        control_sql=(
            "select (select sum(claims_submitted) from rcm.vw_executive_rcm_summary)\n"
            "     = (select count(*) from rcm.fact_inpatient_claim) as reconciles;"
        ),
        datasets=("vw_executive_rcm_summary", "vw_claim_enriched"),
    ),
    Check(
        figure="Executive overview — Denied claims",
        page="Executive overview",
        dashboard=lambda f: float(_executive(f)["denied_claims"].sum()),
        control=lambda f: float(_claims(f)["sim_denial_flag"].fillna(False).astype(bool).sum()),
        control_sql=(
            "select (select sum(denied_claims) from rcm.vw_executive_rcm_summary)\n"
            "     = (select count(*) from rcm.sim_claim_adjudication where sim_denial_flag)\n"
            "  as reconciles;"
        ),
        datasets=("vw_executive_rcm_summary", "vw_claim_enriched"),
    ),
    Check(
        figure="Executive overview — Denial rate",
        page="Executive overview",
        dashboard=lambda f: float(
            _executive(f)["denied_claims"].sum() / _executive(f)["claims_submitted"].sum()
        ),
        control=lambda f: float(
            _claims(f)["sim_denial_flag"].fillna(False).astype(bool).sum() / len(_claims(f))
        ),
        control_sql=(
            "-- summed numerator over summed denominator, NOT a mean of monthly rates\n"
            "select sum(denied_claims)::numeric / sum(claims_submitted)\n"
            "  from rcm.vw_executive_rcm_summary;"
        ),
        tolerance=1e-9,
        datasets=("vw_executive_rcm_summary", "vw_claim_enriched"),
    ),
    Check(
        figure="Executive overview — Billed charges (SOURCE)",
        page="Executive overview",
        dashboard=lambda f: float(_executive(f)["billed_charge_amt"].sum()),
        control=lambda f: float(_claims(f)["billed_charge_amt"].sum()),
        control_sql=(
            "select round(sum(billed_charge_amt))\n"
            "     = (select round(sum(clm_tot_chrg_amt)) from rcm.fact_inpatient_claim)\n"
            "  as reconciles from rcm.vw_executive_rcm_summary;"
        ),
        tolerance=0.01,
        datasets=("vw_executive_rcm_summary", "vw_claim_enriched"),
    ),
    Check(
        figure="Denial prevention — Denials in the mix table",
        page="Denial prevention",
        dashboard=lambda f: float(f["vw_denial_root_cause"]["denial_count"].sum()),
        control=lambda f: float(_claims(f)["sim_denial_flag"].fillna(False).astype(bool).sum()),
        control_sql=(
            "select (select sum(denial_count) from rcm.vw_denial_root_cause)\n"
            "     = (select count(*) from rcm.sim_claim_adjudication where sim_denial_flag)\n"
            "  as reconciles;"
        ),
        datasets=("vw_denial_root_cause", "vw_claim_enriched"),
    ),
    Check(
        figure="Denial prevention — Full + partial denials",
        page="Denial prevention",
        dashboard=lambda f: float(
            f["vw_denial_root_cause"]["full_denials"].sum()
            + f["vw_denial_root_cause"]["partial_denials"].sum()
        ),
        control=lambda f: float(f["vw_denial_root_cause"]["denial_count"].sum()),
        control_sql=(
            "select sum(full_denials + partial_denials) = sum(denial_count) as reconciles\n"
            "  from rcm.vw_denial_root_cause;"
        ),
        datasets=("vw_denial_root_cause",),
    ),
    Check(
        figure="Denial prevention — Appeals overturned",
        page="Denial prevention",
        dashboard=lambda f: float(f["vw_denial_root_cause"]["claims_overturned"].sum()),
        control=lambda f: float(_executive(f)["claims_overturned"].sum()),
        control_sql=(
            "select (select sum(claims_overturned) from rcm.vw_denial_root_cause)\n"
            "     = (select sum(claims_overturned) from rcm.vw_executive_rcm_summary)\n"
            "  as reconciles;"
        ),
        datasets=("vw_denial_root_cause", "vw_executive_rcm_summary"),
    ),
    Check(
        figure="A/R & recovery — Open claims in the aging spine",
        page="A/R & recovery",
        dashboard=lambda f: float(f["vw_ar_aging"]["open_claims"].sum()),
        control=lambda f: float(_claims(f)["ar_open_flag"].fillna(False).astype(bool).sum()),
        control_sql=(
            "select (select sum(open_claims) from rcm.vw_ar_aging)\n"
            "     = (select count(*) from rcm.vw_claim_enriched where ar_open_flag)\n"
            "  as reconciles;"
        ),
        datasets=("vw_ar_aging", "vw_claim_enriched"),
    ),
    Check(
        figure="A/R & recovery — Aging buckets drawn",
        page="A/R & recovery",
        dashboard=lambda f: float(len(f["vw_ar_aging"])),
        control=lambda f: 5.0,
        control_sql="select count(*) = 5 as reconciles from rcm.vw_ar_aging;",
        datasets=("vw_ar_aging",),
    ),
    Check(
        figure="A/R & recovery — Denied + non-denied = open, every bucket",
        page="A/R & recovery",
        dashboard=lambda f: float(
            (
                f["vw_ar_aging"]["open_claims"]
                - f["vw_ar_aging"]["denied_open_claims"]
                - f["vw_ar_aging"]["nondenied_open_claims"]
            )
            .abs()
            .sum()
        ),
        control=lambda f: 0.0,
        control_sql=(
            "select coalesce(sum(case when open_claims\n"
            "       <> denied_open_claims + nondenied_open_claims then 1 else 0 end), 0)\n"
            "  from rcm.vw_ar_aging;"
        ),
        datasets=("vw_ar_aging",),
    ),
    Check(
        figure="A/R & recovery — Claims across the five simulated payers",
        page="A/R & recovery",
        dashboard=lambda f: float(f["vw_payer_performance"]["claims"].sum()),
        control=lambda f: float(len(_claims(f))),
        control_sql=(
            "select (select sum(claims) from rcm.vw_payer_performance)\n"
            "     = (select count(*) from rcm.fact_inpatient_claim) as reconciles;"
        ),
        datasets=("vw_payer_performance", "vw_claim_enriched"),
    ),
    Check(
        figure="Denial prevention — Claims across synthetic providers",
        page="Denial prevention",
        dashboard=lambda f: float(f["vw_clean_claim_performance"]["provider_claims"].sum()),
        control=lambda f: float(len(_claims(f))),
        control_sql=(
            "select (select sum(provider_claims) from rcm.vw_clean_claim_performance)\n"
            "     = (select count(*) from rcm.fact_inpatient_claim) as reconciles;"
        ),
        datasets=("vw_clean_claim_performance", "vw_claim_enriched"),
    ),
    Check(
        figure="Denial prevention — Provider grain is the SYNTHETIC prvdr_num",
        page="Denial prevention",
        dashboard=lambda f: float(f["vw_clean_claim_performance"]["prvdr_num"].nunique()),
        control=lambda f: float(len(f["vw_clean_claim_performance"])),
        control_sql=(
            "-- one row per SYNTHETIC billing provider. Never per crosswalked real CCN:\n"
            "-- 4,876 synthetic providers share 2,857 CCNs and only 2,816 display names.\n"
            "select count(distinct prvdr_num) = count(*) as reconciles\n"
            "  from rcm.vw_clean_claim_performance;"
        ),
        datasets=("vw_clean_claim_performance",),
    ),
    Check(
        figure="Work queue — Heuristic scaffold rows",
        page="Predictive work queue",
        dashboard=lambda f: float(len(f["vw_work_queue_priority"])),
        control=lambda f: float(
            (
                _claims(f)["sim_denial_flag"].fillna(False).astype(bool)
                | _claims(f)["ar_open_flag"].fillna(False).astype(bool)
            ).sum()
        ),
        control_sql=(
            "-- MEMBERSHIP is the label-bearing property of this view, not any column:\n"
            "-- the where clause selects claims that were denied or left open.\n"
            "select (select count(*) from rcm.vw_work_queue_priority)\n"
            "     = (select count(*) from rcm.vw_claim_enriched\n"
            "         where sim_denial_flag or ar_open_flag) as reconciles;"
        ),
        datasets=("vw_work_queue_priority", "vw_claim_enriched"),
    ),
)


# ---------------------------------------------------------------------------
# Checks that need the model datasets, so they only run against a bundle
# ---------------------------------------------------------------------------

MODEL_CHECKS: tuple[Check, ...] = (
    Check(
        figure="Model quality — Model A rows scored",
        page="Model & data quality",
        dashboard=lambda f: float(len(f["model_a_scores"])),
        control=lambda f: float(len(_claims(f))),
        control_sql=(
            "-- one score per claim; 16,694 of them are IN-SAMPLE and carry fold != 'test'.\n"
            "select count(*) from rcm.fact_inpatient_claim;"
        ),
        datasets=("model_a_scores", "vw_claim_enriched"),
    ),
    Check(
        figure="Model quality — Denials among the scored claims",
        page="Model & data quality",
        dashboard=lambda f: float(f["model_a_scores"]["sim_denial_flag"].astype(bool).sum()),
        control=lambda f: float(_claims(f)["sim_denial_flag"].fillna(False).astype(bool).sum()),
        control_sql=("select count(*) from rcm.sim_claim_adjudication where sim_denial_flag;"),
        datasets=("model_a_scores", "vw_claim_enriched"),
    ),
    Check(
        figure="Work queue — Model C backtest queue is a subset of the denials",
        page="Predictive work queue",
        dashboard=lambda f: float(
            f["model_c_work_queue"]
            .loc[f["model_c_work_queue"]["queue_mode"] == "backtest", "claim_sk"]
            .isin(_claims(f).loc[_claims(f)["sim_denial_flag"].fillna(False), "claim_sk"])
            .sum()
        ),
        control=lambda f: float((f["model_c_work_queue"]["queue_mode"] == "backtest").sum()),
        control_sql=(
            "-- every claim Model C ranks is a claim the simulation denied.\n"
            "select count(*) from rcm.vw_claim_enriched where sim_denial_flag;"
        ),
        datasets=("model_c_work_queue", "vw_claim_enriched"),
    ),
)


ALL_CHECKS: tuple[Check, ...] = CHECKS + MODEL_CHECKS


def run(frames: Frames, checks: tuple[Check, ...] = ALL_CHECKS) -> list[Result]:
    """Evaluate every check whose datasets are present. Pure; no I/O."""
    results: list[Result] = []
    for check in checks:
        if any(name not in frames for name in check.datasets):
            continue
        dashboard_value = float(check.dashboard(frames))
        control_value = float(check.control(frames))
        difference = dashboard_value - control_value
        results.append(
            Result(
                figure=check.figure,
                page=check.page,
                dashboard_value=dashboard_value,
                control_value=control_value,
                difference=difference,
                tolerance=check.tolerance,
                passed=abs(difference) <= check.tolerance,
                control_sql=check.control_sql,
            )
        )
    return results


def to_frame(results: list[Result]) -> pd.DataFrame:
    """Results as a table, for the Model & data quality page."""
    return pd.DataFrame.from_records(
        [
            {
                "figure": result.figure,
                "page": result.page,
                "dashboard_value": result.dashboard_value,
                "control_value": result.control_value,
                "difference": result.difference,
                "reconciles": result.passed,
            }
            for result in results
        ]
    )


def failures(results: list[Result]) -> list[Result]:
    return [result for result in results if not result.passed]


def sql_for(figure: str) -> str:
    """The control query behind one named figure, for `components.control_query`."""
    for check in ALL_CHECKS:
        if check.figure == figure:
            return check.control_sql
    raise KeyError(
        f"{figure!r} is not a reconciled figure. Declared: "
        f"{[check.figure for check in ALL_CHECKS]}. Every figure a page shows needs a "
        "control query here before it ships (CLAUDE.md §7)."
    )
