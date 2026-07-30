"""Page 3 — A/R & recovery.

Accounts-receivable aging, payer-level performance, and what appeals recovered.

THIS IS THE §3.5 PAGE
---------------------
Medicare fee-for-service has exactly ONE payer. The five payer archetypes compared
below are 100% invented by this project, are named after no real insurer, and
carry no relationship to how Medicare Advantage, commercial or Medicaid plans
behave. CLAUDE.md §3.5 requires the banner on every payer-level analysis, and the
payer note is rendered INSIDE the banner block rather than beside it, so a reader
cannot take the comparison and leave the caveat behind.

THE EMPTY AGING BUCKETS ARE THE ANSWER, NOT A BUG
--------------------------------------------------
Every open claim in this book lands in the 120+ bucket, and the four younger
buckets are drawn empty. That is a true statement about the simulated book — every
unpaid claim is a never-paid full denial and the denials stop in 2023, so relative
to the 2024-07 snapshot the youngest open claim is about 481 days old. The empty
buckets are still drawn, because a chart that silently omitted them would look
like a normal aging curve rather than like a book with one bucket in it.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

from dashboard import data, disclosures, reconcile
from dashboard.components import (
    Kpi,
    control_query,
    data_source_caption,
    dataframe,
    kpi_row,
    money,
    percent,
    provenance_note,
    render_page_header,
    required_disclosures,
)

render_page_header(
    "A/R & recovery",
    "Aging of simulated open receivables, payer performance, and appeal recovery.",
    banner_extra=disclosures.PAYER_DIMENSION_NOTE,
)
data_source_caption()

try:
    aging = data.load("vw_ar_aging")
    payers = data.load("vw_payer_performance")
    executive = data.load("vw_executive_rcm_summary")
except data.DashboardDataError as error:
    st.error(str(error))
    st.stop()


# ---------------------------------------------------------------------------
# A/R aging
# ---------------------------------------------------------------------------

st.subheader("Accounts receivable aging")

kpi_row(
    [
        Kpi(
            "Open claims",
            f"{int(aging['open_claims'].sum()):,}",
            "SIMULATED",
            "A claim is open because the simulation never paid it.",
        ),
        Kpi(
            "A/R balance",
            money(float(aging["sim_ar_balance_amt"].sum())),
            "SIMULATED",
            "Simulated allowed less simulated paid.",
        ),
        Kpi(
            "Billed at risk",
            money(float(aging["source_billed_at_risk_amt"].sum())),
            "SOURCE",
            "The CMS billed charge on those claims — real published dollars attached to a "
            "simulated non-payment.",
        ),
        Kpi(
            "Oldest open claim",
            f"{int(aging['max_days_outstanding'].max()):,} days",
            "SIMULATED",
            "Measured against a snapshot taken from the latest simulated activity date.",
        ),
    ]
)

st.info(disclosures.AR_AGING_NOTE, icon=":material/info:")

aging_chart = (
    alt.Chart(aging.sort_values("bucket_sort"))
    .mark_bar()
    .encode(
        x=alt.X(
            "aging_bucket:N",
            sort=list(aging.sort_values("bucket_sort")["aging_bucket"]),
            title="Days outstanding",
        ),
        y=alt.Y("sim_ar_balance_amt:Q", title="Simulated A/R balance ($)"),
        tooltip=[
            alt.Tooltip("aging_bucket:N", title="Bucket"),
            alt.Tooltip("open_claims:Q", title="Open claims", format=","),
            alt.Tooltip("denied_open_claims:Q", title="of which denied", format=","),
            alt.Tooltip("sim_ar_balance_amt:Q", title="A/R balance", format="$,.0f"),
            alt.Tooltip("avg_days_outstanding:Q", title="Avg days", format=",.0f"),
        ],
    )
    .properties(height=280)
)
st.altair_chart(aging_chart, use_container_width=True)
provenance_note(
    "SIMULATED",
    "The bucket spine has five rows and all five are drawn even when four are zero. "
    "Hiding an empty bucket would turn 'this book has exactly one aging bucket' into a "
    "chart that looks like a normal aging curve.",
)
control_query(
    reconcile.sql_for("A/R & recovery — Open claims in the aging spine")
    + "\n\n"
    + reconcile.sql_for("A/R & recovery — Denied + non-denied = open, every bucket")
)
dataframe(aging)

# ---------------------------------------------------------------------------
# Payer performance — §3.5
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Payer performance — every payer on this chart is invented")

st.error(disclosures.PAYER_DIMENSION_NOTE, icon=":material/person_off:")

measure = st.selectbox(
    "Compare payers on",
    [
        ("Denial rate", "denial_rate", "percent"),
        ("Clean-claim rate", "clean_claim_rate", "percent"),
        ("Net collection rate", "sim_net_collection_rate", "percent"),
        ("Late filing rate", "late_filing_rate", "percent"),
        ("Avg days to payment", "avg_days_to_payment", "number"),
        ("Median days to payment", "median_days_to_payment", "number"),
        ("Appeal overturn rate", "appeal_overturn_rate", "percent"),
        ("Cost to collect ($)", "sim_cost_to_collect", "money"),
    ],
    format_func=lambda option: option[0],
)
measure_label, measure_column, measure_kind = measure

payer_chart = (
    alt.Chart(payers)
    .mark_bar()
    .encode(
        y=alt.Y("sim_payer_name:N", sort="-x", title="Simulated payer archetype"),
        x=alt.X(
            f"{measure_column}:Q",
            title=f"{measure_label} (SIMULATED)",
            axis=alt.Axis(format="%" if measure_kind == "percent" else "~s"),
        ),
        tooltip=[
            alt.Tooltip("sim_payer_name:N", title="Payer (invented)"),
            alt.Tooltip("claims:Q", title="Claims", format=","),
            alt.Tooltip("realized_claim_share:Q", title="Share of book", format=".1%"),
            alt.Tooltip(
                f"{measure_column}:Q",
                title=measure_label,
                format=".1%" if measure_kind == "percent" else ",.1f",
            ),
        ],
    )
    .properties(height=240)
)
st.altair_chart(payer_chart, use_container_width=True)
provenance_note(
    "SIMULATED",
    "The payer identity, the mix shares, the denial propensities and the timely-filing "
    "windows are all configuration in `config/simulation.yaml`. This chart shows how "
    "faithfully the generator hit its own targets — it is a check on our simulation, not "
    "a market comparison.",
)
control_query(reconcile.sql_for("A/R & recovery — Claims across the five simulated payers"))
dataframe(payers)

# ---------------------------------------------------------------------------
# Appeal recovery over time
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Appeal recovery")

recovered = float(executive["sim_appeal_recovered_amt"].fillna(0).sum())
denied = float(executive["sim_denied_amt"].fillna(0).sum())
appealed = int(executive["claims_appealed"].fillna(0).sum())
overturned = int(executive["claims_overturned"].fillna(0).sum())

kpi_row(
    [
        Kpi("Denied dollars", money(denied), "SIMULATED", "Total simulated denied amount."),
        Kpi(
            "Recovered on appeal",
            money(recovered),
            "SIMULATED",
            "Simulated recovery on overturned appeals.",
        ),
        Kpi(
            "Recovered share of denied",
            percent(recovered / denied if denied else 0.0),
            "SIMULATED",
            "Recovered / denied across the whole book.",
        ),
        Kpi(
            "Appeals filed / overturned",
            f"{appealed:,} / {overturned:,}",
            "SIMULATED",
            "Whether a denial was appealed is itself a simulated decision.",
        ),
    ]
)
control_query(reconcile.sql_for("Denial prevention — Appeals overturned"))

monthly_recovery = executive[["month_start", "sim_denied_amt", "sim_appeal_recovered_amt"]].melt(
    "month_start", var_name="measure", value_name="amount"
)
recovery_chart = (
    alt.Chart(monthly_recovery)
    .mark_area(opacity=0.6)
    .encode(
        x=alt.X("month_start:T", title="Claim submission month"),
        y=alt.Y("amount:Q", title="Simulated dollars", stack=None),
        color=alt.Color("measure:N", title="Measure", scale=alt.Scale(scheme="set2")),
        tooltip=[
            alt.Tooltip("month_start:T", title="Month"),
            alt.Tooltip("measure:N", title="Measure"),
            alt.Tooltip("amount:Q", title="Dollars", format="$,.0f"),
        ],
    )
    .properties(height=280)
)
st.altair_chart(recovery_chart, use_container_width=True)
provenance_note(
    "SIMULATED",
    "Both series are generated. The recovered series is a function of a simulated appeal "
    "decision and a simulated appeal outcome, so the gap between the two areas is not "
    "money any organisation left on the table.",
)

st.divider()
required_disclosures()
