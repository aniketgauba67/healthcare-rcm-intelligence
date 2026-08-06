"""Page 1 — Executive overview.

Headline RCM KPIs for the whole book, rolled up from `rcm.vw_executive_rcm_summary`
and never recomputed from claims. Every figure on this page has a control query
under it (CLAUDE.md §7), and the classification under every tile says whether the
number came from CMS or from our simulation.

THE ONE MIXED TILE ROW, AND WHY IT IS MIXED ON PURPOSE
-------------------------------------------------------
Billed charges are SOURCE — that is `clm_tot_chrg_amt` exactly as CMS published
it. Allowed, paid, denied and cost-to-collect are SIMULATED, because Medicare's
synthetic files contain no adjudication at all. Putting them in one row with
different badges is deliberate: an executive dashboard that showed only the
simulated money would let a reader assume the whole book was invented, and one
that showed only the source money would hide that the interesting half is not
real. The badges do the work the numbers cannot.
"""

from __future__ import annotations

# STREAMLIT CLOUD LOADS THIS FILE DIRECTLY. `app.py` prepends the repo root to
# sys.path, but that only runs when app.py is the entrypoint — and a page URL, a
# refresh, or the multipage router loads THIS module first, with only
# `dashboard/pages/` on the path. Without these four lines `from dashboard import
# ...` below raises ModuleNotFoundError in production while passing every local
# test, because the test harness sets PYTHONPATH and the platform does not.
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import data, disclosures, reconcile  # noqa: E402
from dashboard.components import (  # noqa: E402
    Kpi,
    control_query,
    data_source_caption,
    dataframe,
    kpi_row,
    money,
    percent,
    provenance_note,
    render_page_header,
    thin_volume_layer,
    summary_with_detail,
    render_synthetic_data_banner,
    required_disclosures,
)
from dashboard.provenance import emitter_for  # noqa: E402

PAGE_EMITTER = emitter_for("dashboard/pages/executive_overview.py")

render_page_header(
    "Executive overview",
    "Book-level revenue-cycle KPIs, 2015-03 through 2024-06.",
    emitter=PAGE_EMITTER,
)
render_synthetic_data_banner()
data_source_caption()

try:
    monthly = data.load("vw_executive_rcm_summary")
except data.DashboardDataError as error:
    st.error(str(error))
    st.stop()

if monthly.empty:
    st.warning(
        "Executive metrics are unavailable because this warehouse contains no monthly "
        "summary rows. No zero-valued operational KPIs or empty trend are shown: an unloaded "
        "warehouse does not establish a zero-volume revenue-cycle book.",
        icon=":material/warning:",
    )
    required_disclosures()
    st.stop()

totals = data.executive_totals()


# ---------------------------------------------------------------------------
# Volume and denial performance
# ---------------------------------------------------------------------------

st.subheader("Volume and first-pass performance")
kpi_row(
    [
        Kpi(
            "Claims submitted",
            f"{totals['sim_claims_submitted']:,}",
            "SIMULATED",
            "Counted by SIMULATED submission month, which is why it carries the marker. "
            "The book-level total still reconciles exactly to count(*) over the CMS "
            "synthetic fact — every inpatient claim in the published file, counted once.",
        ),
        Kpi(
            "Denial rate",
            percent(totals["sim_denial_rate"], 2),
            "SIMULATED",
            "Denied claims / claims submitted, summed across months rather than averaged.",
        ),
        Kpi(
            "Clean-claim rate",
            percent(totals["sim_clean_claim_rate"], 2),
            "SIMULATED",
            "Claims that adjudicated with no denial and no rework.",
        ),
        Kpi(
            "First-pass paid rate",
            percent(totals["sim_first_pass_paid_rate"], 2),
            "SIMULATED",
            "Paid on first submission, no appeal, no resubmission.",
        ),
    ]
)
control_query(
    reconcile.sql_for("Executive overview — Claims submitted")
    + "\n\n"
    + reconcile.sql_for("Executive overview — Denial rate")
)
st.caption(
    "**Rates are computed from summed numerators over summed denominators, never as a "
    "mean of monthly rates.** A mean would weight a 40-claim month like a 400-claim one "
    "and would stop equalling the control query above."
)

# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

st.subheader("The money")
kpi_row(
    [
        Kpi(
            "Billed charges",
            money(totals["source_billed_charge_amt"]),
            "SOURCE",
            "clm_tot_chrg_amt from the CMS file, unmodified.",
        ),
        Kpi(
            "Allowed",
            money(totals["sim_allowed_amt"]),
            "SIMULATED",
            "Simulated payer allowed amount. There is no allowed amount in the CMS files.",
        ),
        Kpi(
            "Paid",
            money(totals["sim_paid_amt"]),
            "SIMULATED",
            "Simulated payment.",
        ),
        Kpi(
            "Denied",
            money(totals["sim_denied_amt"]),
            "SIMULATED",
            "Simulated denied dollars. Nobody lost this money.",
        ),
        Kpi(
            "Net collection rate",
            percent(totals["sim_net_collection_rate"], 2),
            "SIMULATED",
            "Simulated paid / simulated allowed.",
        ),
    ]
)
control_query(reconcile.sql_for("Executive overview — Billed charges (SOURCE)"))
st.caption(
    "Billed charges are the only figure in this row that came from CMS. Everything to "
    "the right of it is generated by this project's simulation layer — the synthetic "
    "claims carry no allowed amount, no payment and no denial."
)

# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

st.subheader("Monthly trend")

trend = monthly.copy()
trend["month"] = pd.to_datetime(trend["sim_month_start"])

metric_choice = st.selectbox(
    "Measure",
    [
        ("Denial rate (SIMULATED)", "sim_denial_rate", True),
        ("Clean-claim rate (SIMULATED)", "sim_clean_claim_rate", True),
        ("First-pass paid rate (SIMULATED)", "sim_first_pass_paid_rate", True),
        # Per MONTH this is a count over simulated submission dates, not a SOURCE
        # fact — only the book-level total equals count(*) over the CMS fact.
        ("Claims submitted (SIMULATED)", "sim_claims_submitted", False),
        ("Billed charges (SOURCE)", "billed_charge_amt", False),
        ("Simulated denied dollars", "sim_denied_amt", False),
        ("Avg days to payment (SIMULATED)", "sim_avg_days_to_payment", False),
        ("Cost to collect (SIMULATED)", "sim_cost_to_collect", False),
    ],
    format_func=lambda option: option[0],
)
label, column, is_rate = metric_choice

chart_frame = trend[["month", column, "sim_claims_submitted"]].dropna(subset=[column])
chart = (
    alt.Chart(chart_frame)
    .mark_line(point=alt.OverlayMarkDef(size=18))
    .encode(
        x=alt.X("month:T", title="Claim submission month"),
        y=alt.Y(
            f"{column}:Q",
            title=label,
            axis=alt.Axis(format="%" if is_rate else "~s"),
            scale=alt.Scale(zero=not is_rate),
        ),
        tooltip=[
            alt.Tooltip("month:T", title="Month"),
            alt.Tooltip(f"{column}:Q", title=label, format=".3f" if is_rate else ",.0f"),
            alt.Tooltip("sim_claims_submitted:Q", title="Claims that month", format=","),
        ],
    )
    .properties(height=320)
)
# Shade the months too thin to read as a trend, ON the chart. The caption below
# says the same thing and stays — but it is read after the spike has landed.
_thin = thin_volume_layer(chart_frame, month_column="month", count_column="sim_claims_submitted")
st.altair_chart(_thin + chart if _thin is not None else chart, use_container_width=True)
provenance_note(
    "SIMULATED" if column.startswith("sim_") or is_rate else "SOURCE",
    "One point per claim-submission month. Thin early months are real thinness in the "
    "CMS extract, not a rendering artefact — the claim count is in the tooltip beside "
    "every rate so a 3-claim month cannot be read as a trend.",
)

with st.expander("The monthly view as a table", icon=":material/table:"):
    dataframe(monthly, emitter=PAGE_EMITTER)

# ---------------------------------------------------------------------------
# Cost to collect and appeals
# ---------------------------------------------------------------------------

st.subheader("Cost to collect and appeal recovery")
kpi_row(
    [
        Kpi(
            "Cost to collect",
            money(totals["sim_cost_to_collect"]),
            "SIMULATED",
            "Simulated touch time, rework and appeal handling costs.",
        ),
        Kpi(
            "Appeals filed",
            f"{totals['sim_claims_appealed']:,}",
            "SIMULATED",
            "Of 2,663 simulated denials, 967 were appealed.",
        ),
        Kpi(
            "Overturn rate of appealed",
            percent(totals["sim_appeal_overturn_rate"], 1),
            "SIMULATED",
            "Overturned / appealed. Not overturned / denied.",
        ),
        Kpi(
            "Avg days to payment",
            f"{totals['sim_avg_days_to_payment']:.1f}",
            "SIMULATED",
            "Volume-weighted across months.",
        ),
    ]
)
st.caption(
    "**Whether a denial was appealed is itself a simulated decision.** The overturn rate "
    "is measured over the 967 denials the simulation chose to appeal, not over all 2,663 "
    "— and those are not the same claims. The Predictive work queue page carries the "
    "selection measurement in full."
)

st.divider()
required_disclosures()
with st.expander("How dollars at risk should and should not be quoted", icon=":material/warning:"):
    summary_with_detail(
        disclosures.DOLLARS_AT_RISK_SUMMARY,
        disclosures.DOLLARS_AT_RISK,
        label="Technical detail — how that range was measured",
    )
