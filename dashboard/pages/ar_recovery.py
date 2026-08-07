"""Page 3 — A/R & recovery.

Accounts-receivable aging, payer-level performance, and what appeals recovered.

THIS IS THE §3.5 PAGE
---------------------
Medicare fee-for-service has exactly ONE payer. The five payer archetypes compared
below are 100% invented by this project, are named after no real insurer, and
carry no relationship to how Medicare Advantage, commercial or Medicaid plans
behave. docs/project_rules.md §3.5 requires the banner on every payer-level analysis, and the
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
    NO_LABEL_TRUNCATION,
    control_query,
    data_source_caption,
    dataframe,
    kpi_row,
    money,
    percent,
    provenance_note,
    render_page_header,
    summary_with_detail,
    render_synthetic_data_banner,
    required_disclosures,
)
from dashboard.provenance import emitter_for  # noqa: E402

PAGE_EMITTER = emitter_for("dashboard/pages/ar_recovery.py")

render_page_header(
    "A/R & recovery",
    "Aging of simulated open receivables, payer performance, and appeal recovery.",
    emitter=PAGE_EMITTER,
)
# §3.5 makes the payer note part of THIS page's banner rather than a separate block
# beside it: Medicare FFS has one payer, so a reader who takes the payer comparison
# and leaves the caveat behind has taken the one thing this page cannot survive.
render_synthetic_data_banner(extra=disclosures.PAYER_DIMENSION_NOTE, short=True)
data_source_caption()

try:
    aging = data.load("vw_ar_aging")
    payers = data.load("vw_payer_performance")
    executive = data.load("vw_executive_rcm_summary")
except data.DashboardDataError as error:
    st.error(str(error))
    st.stop()


def _unavailable_reason(
    frame: pd.DataFrame,
    *,
    label: str,
    required: set[str],
    non_null: set[str] | None = None,
) -> str | None:
    """Explain why a section cannot make honest metrics from its frame."""
    missing = sorted(required - set(frame.columns))
    if missing:
        return f"{label} is unavailable because required columns are missing: {', '.join(missing)}."
    if frame.empty:
        return f"{label} is unavailable because this warehouse contains no rows for it."
    empty_values = sorted(column for column in (non_null or set()) if frame[column].dropna().empty)
    if empty_values:
        return (
            f"{label} is unavailable because required values are null: {', '.join(empty_values)}."
        )
    return None


# ---------------------------------------------------------------------------
# A/R aging
# ---------------------------------------------------------------------------

st.subheader("Accounts receivable aging")

aging_issue = _unavailable_reason(
    aging,
    label="A/R aging data",
    required={
        "sim_aging_bucket",
        "bucket_sort",
        "sim_open_claims",
        "sim_denied_open_claims",
        "sim_ar_balance_amt",
        "sim_source_billed_at_risk_amt",
        "sim_avg_days_outstanding",
        "sim_max_days_outstanding",
    },
    non_null={
        "sim_open_claims",
        "sim_ar_balance_amt",
        "sim_source_billed_at_risk_amt",
        "sim_max_days_outstanding",
    },
)
if aging_issue:
    st.warning(
        f"{aging_issue} No A/R metrics or chart are shown; zero would imply a measured "
        "empty receivables book, which this unloaded warehouse does not establish.",
        icon=":material/warning:",
    )
else:
    kpi_row(
        [
            Kpi(
                "Open claims",
                f"{int(aging['sim_open_claims'].sum()):,}",
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
                money(float(aging["sim_source_billed_at_risk_amt"].sum())),
                # SOURCE dollars, but summed over a population the simulation put
                # at risk, so the figure is a statement about the simulated world.
                "SIMULATED",
                "The CMS billed charge on those claims — real published dollars attached to a "
                "simulated non-payment.",
            ),
            Kpi(
                "Oldest open claim",
                f"{int(aging['sim_max_days_outstanding'].max()):,} days",
                "SIMULATED",
                "Measured against a snapshot taken from the latest simulated activity date.",
            ),
        ]
    )

    summary_with_detail(
        disclosures.AR_AGING_NOTE_SUMMARY,
        disclosures.AR_AGING_NOTE,
        label="Full explanation — why the younger buckets are empty",
        icon=":material/info:",
    )

    aging_chart = (
        alt.Chart(aging.sort_values("bucket_sort"))
        .mark_bar()
        .encode(
            x=alt.X(
                "sim_aging_bucket:N",
                sort=list(aging.sort_values("bucket_sort")["sim_aging_bucket"]),
                title="Days outstanding",
            ),
            y=alt.Y("sim_ar_balance_amt:Q", title="Simulated A/R balance ($)"),
            tooltip=[
                alt.Tooltip("sim_aging_bucket:N", title="Bucket"),
                alt.Tooltip("sim_open_claims:Q", title="Open claims", format=","),
                alt.Tooltip("sim_denied_open_claims:Q", title="of which denied", format=","),
                alt.Tooltip("sim_ar_balance_amt:Q", title="A/R balance", format="$,.0f"),
                alt.Tooltip("sim_avg_days_outstanding:Q", title="Avg days", format=",.0f"),
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
    dataframe(aging, emitter=PAGE_EMITTER)

# ---------------------------------------------------------------------------
# Payer performance — §3.5
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Payer performance — every payer on this chart is invented")

st.error(disclosures.PAYER_DIMENSION_NOTE, icon=":material/person_off:")

payer_issue = _unavailable_reason(
    payers,
    label="Payer-performance data",
    required={
        "sim_payer_name",
        "sim_claims",
        "sim_realized_claim_share",
        "sim_denial_rate",
        "sim_clean_claim_rate",
        "sim_net_collection_rate",
        "sim_late_filing_rate",
        "sim_avg_days_to_payment",
        "sim_median_days_to_payment",
        "sim_appeal_overturn_rate",
        "sim_cost_to_collect",
    },
)
if payer_issue:
    st.warning(
        f"{payer_issue} No payer comparison is shown.",
        icon=":material/warning:",
    )
else:
    measure = st.selectbox(
        "Compare payers on",
        [
            ("Denial rate", "sim_denial_rate", "percent"),
            ("Clean-claim rate", "sim_clean_claim_rate", "percent"),
            ("Net collection rate", "sim_net_collection_rate", "percent"),
            ("Late filing rate", "sim_late_filing_rate", "percent"),
            ("Avg days to payment", "sim_avg_days_to_payment", "number"),
            ("Median days to payment", "sim_median_days_to_payment", "number"),
            ("Appeal overturn rate", "sim_appeal_overturn_rate", "percent"),
            ("Cost to collect ($)", "sim_cost_to_collect", "money"),
        ],
        format_func=lambda option: option[0],
    )
    measure_label, measure_column, measure_kind = measure

    payer_chart = (
        alt.Chart(payers)
        .mark_bar()
        .encode(
            y=alt.Y(
                "sim_payer_name:N",
                sort="-x",
                title="Simulated payer archetype",
                axis=alt.Axis(labelLimit=NO_LABEL_TRUNCATION),
            ),
            x=alt.X(
                f"{measure_column}:Q",
                title=f"{measure_label} (SIMULATED)",
                axis=alt.Axis(format="%" if measure_kind == "percent" else "~s"),
            ),
            tooltip=[
                alt.Tooltip("sim_payer_name:N", title="Payer (invented)"),
                alt.Tooltip("sim_claims:Q", title="Claims", format=","),
                alt.Tooltip("sim_realized_claim_share:Q", title="Share of book", format=".1%"),
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
    dataframe(payers, emitter=PAGE_EMITTER)

# ---------------------------------------------------------------------------
# Appeal recovery over time
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Appeal recovery")

executive_issue = _unavailable_reason(
    executive,
    label="Appeal-recovery data",
    required={
        "sim_month_start",
        "sim_appeal_recovered_amt",
        "sim_denied_amt",
        "sim_claims_appealed",
        "sim_claims_overturned",
    },
    non_null={"sim_appeal_recovered_amt", "sim_denied_amt"},
)
if executive_issue:
    st.warning(
        f"{executive_issue} No appeal-recovery metrics or chart are shown.",
        icon=":material/warning:",
    )
else:
    recovered = float(executive["sim_appeal_recovered_amt"].fillna(0).sum())
    denied = float(executive["sim_denied_amt"].fillna(0).sum())
    appealed = int(executive["sim_claims_appealed"].fillna(0).sum())
    overturned = int(executive["sim_claims_overturned"].fillna(0).sum())

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

    monthly_recovery = executive[
        ["sim_month_start", "sim_denied_amt", "sim_appeal_recovered_amt"]
    ].melt("sim_month_start", var_name="measure", value_name="amount")
    recovery_chart = (
        alt.Chart(monthly_recovery)
        .mark_area(opacity=0.6)
        .encode(
            x=alt.X("sim_month_start:T", title="Claim submission month"),
            y=alt.Y("amount:Q", title="Simulated dollars", stack=None),
            color=alt.Color("measure:N", title="Measure", scale=alt.Scale(scheme="set2")),
            tooltip=[
                alt.Tooltip("sim_month_start:T", title="Month"),
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
