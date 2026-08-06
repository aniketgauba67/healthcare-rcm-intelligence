"""Page 2 — Denial prevention.

Where denials come from, which synthetic providers generate them, and what Model A
can and cannot do about it before a claim is submitted.

THIS PAGE IS WHERE THE KEYING RULE IS MOST LIKELY TO BE BROKEN
---------------------------------------------------------------
Provider performance is the one table a reader most wants grouped by a recognisable
facility name — and that is exactly the grouping the crosswalk forbids. 4,876
synthetic billing providers were assigned onto 2,857 real CCNs and onto only 2,816
distinct display NAMES; the worst CCN carries 8 synthetic providers and the worst
NAME carries 15. So this page keys every provider figure on the synthetic
`prvdr_num`, shows the crosswalked name as a display column beside it, and says so
under the table rather than in a footnote nobody reaches.

MODEL A'S RESULT IS A TIE, AND THE PAGE LEADS WITH THAT
--------------------------------------------------------
XGBoost minus logistic is +0.0003 [-0.0173, +0.0183]. The honest reading is "no
difference", and it goes above the SHAP chart rather than below it, because a
global importance plot is the single most persuasive thing on this page and it is
persuasive about a model that did not beat its own baseline.
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
import streamlit as st  # noqa: E402

from dashboard import data, disclosures, reconcile  # noqa: E402
from dashboard.components import (  # noqa: E402
    Kpi,
    control_query,
    data_source_caption,
    dataframe,
    disclosure_block,
    kpi_row,
    money,
    provenance_note,
    render_page_header,
    render_synthetic_data_banner,
    required_disclosures,
)
from dashboard.provenance import emitter_for  # noqa: E402

PAGE_EMITTER = emitter_for("dashboard/pages/denial_prevention.py")

render_page_header(
    "Denial prevention",
    "Denial mix, provider clean-claim performance, and the pre-submission risk model.",
    emitter=PAGE_EMITTER,
)
render_synthetic_data_banner(short=True)
data_source_caption()

try:
    root_cause = data.load("vw_denial_root_cause")
    providers = data.load("vw_clean_claim_performance")
except data.DashboardDataError as error:
    st.error(str(error))
    st.stop()

empty_sections = [
    label
    for label, frame in (
        ("denial-mix", root_cause),
        ("provider-performance", providers),
    )
    if frame.empty
]
if empty_sections:
    st.warning(
        "Denial-prevention data is unavailable because this warehouse contains no rows for: "
        f"{', '.join(empty_sections)}. No zero-valued denial or provider metrics are shown.",
        icon=":material/warning:",
    )
    required_disclosures()
    st.stop()


# ---------------------------------------------------------------------------
# Denial mix
# ---------------------------------------------------------------------------

st.subheader("Where the denials come from")

by_category = (
    root_cause.groupby("sim_denial_category", as_index=False)
    .agg(
        denials=("sim_denial_count", "sum"),
        denied_amt=("sim_denied_amt", "sum"),
        appealed=("sim_claims_appealed", "sum"),
        overturned=("sim_claims_overturned", "sum"),
    )
    .sort_values("denials", ascending=False)
)
by_category["appeal_rate"] = by_category["appealed"] / by_category["denials"]
by_category["overturn_rate_of_appealed"] = by_category["overturned"] / by_category[
    "appealed"
].replace(0, float("nan"))

kpi_row(
    [
        Kpi(
            "Denials",
            f"{int(by_category['denials'].sum()):,}",
            "SIMULATED",
            "The CMS synthetic claims contain no denials. Every one of these was generated.",
        ),
        Kpi(
            "Denied dollars",
            money(float(by_category["denied_amt"].sum())),
            "SIMULATED",
            "Simulated denied amount summed over the denial-mix view.",
        ),
        Kpi(
            "Denial categories",
            f"{by_category['sim_denial_category'].nunique()}",
            "SIMULATED",
            "CARC codes are used as category LABELS only — no licensed X12 wording is in "
            "this repository (CLAUDE.md §3.7).",
        ),
        Kpi(
            "Carry no mechanism at all",
            "~1,222",
            "SIMULATED",
            "Driver 'baseline': denials the generator produced as deliberate label noise, "
            "with no cause behind them. About a third of the book.",
        ),
    ]
)
control_query(
    reconcile.sql_for("Denial prevention — Denials in the mix table")
    + "\n\n"
    + reconcile.sql_for("Denial prevention — Full + partial denials")
)

chart = (
    alt.Chart(by_category)
    .mark_bar()
    .encode(
        y=alt.Y("sim_denial_category:N", sort="-x", title="Simulated denial category"),
        x=alt.X("denials:Q", title="Denials"),
        tooltip=[
            alt.Tooltip("sim_denial_category:N", title="Category"),
            alt.Tooltip("denials:Q", title="Denials", format=","),
            alt.Tooltip("denied_amt:Q", title="Denied $", format="$,.0f"),
            alt.Tooltip("appeal_rate:Q", title="Appealed", format=".1%"),
            alt.Tooltip(
                "overturn_rate_of_appealed:Q", title="Overturned of appealed", format=".1%"
            ),
        ],
    )
    .properties(height=280)
)
st.altair_chart(chart, use_container_width=True)
provenance_note(
    "SIMULATED",
    "Every bar counts events this project generated. The appeal rate in the tooltip is a "
    "simulated decision too — TIMELY_FILING denials were appealed 0.0% of the time, which "
    "is why Model C has no training support for them.",
)

with st.expander(
    "Denial mix by category, CARC group and driver mechanism", icon=":material/table:"
):
    dataframe(root_cause, emitter=PAGE_EMITTER)
    st.caption(
        "`carc_category_label` is project-authored taxonomy text, not licensed X12 wording. "
        "`sim_denial_driver_mechanism` is the generator's own cause label and is forbidden "
        "as a model feature at every boundary — it is the answer, not a fact known before "
        "submission."
    )

# ---------------------------------------------------------------------------
# Provider performance — the keying rule in practice
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Clean-claim performance by SYNTHETIC billing provider")

min_claims = st.slider(
    "Minimum claims per provider",
    min_value=1,
    max_value=100,
    value=25,
    help=(
        "The median provider in this warehouse has TWO claims. A denial rate on a "
        "two-claim denominator is noise wearing a percentage sign."
    ),
)
ranked = (
    providers.loc[providers["sim_provider_claims"] >= min_claims]
    .sort_values(["sim_denial_rate", "sim_provider_claims"], ascending=[False, False])
    .head(50)
)

st.markdown(f"**{disclosures.PROVIDER_VOLUME_NOTE}**")
dataframe(
    ranked[
        [
            "prvdr_num",
            "sim_provider_claims",
            "sim_denial_rate",
            "sim_clean_claim_rate",
            "sim_first_pass_paid_rate",
            "sim_late_filing_rate",
            "sim_rework_rate",
            "sim_rework_cost",
            "sim_display_facility_name",
            "sim_display_facility_state",
            "low_volume_flag",
        ]
    ],
    emitter=PAGE_EMITTER,
    column_config={
        "prvdr_num": st.column_config.TextColumn(
            "prvdr_num (the key)",
            help="The SYNTHETIC billing provider id. This is what every row is grouped on.",
        ),
        "sim_provider_claims": st.column_config.NumberColumn("Claims (simulated)", format="%d"),
        "sim_denial_rate": st.column_config.NumberColumn(
            "Denial rate (simulated)", format="percent"
        ),
        "sim_clean_claim_rate": st.column_config.NumberColumn(
            "Clean claim (simulated)", format="percent"
        ),
        "sim_first_pass_paid_rate": st.column_config.NumberColumn(
            "First-pass paid (simulated)", format="percent"
        ),
        "sim_late_filing_rate": st.column_config.NumberColumn(
            "Late filing (simulated)", format="percent"
        ),
        "sim_rework_rate": st.column_config.NumberColumn("Rework (simulated)", format="percent"),
        "sim_rework_cost": st.column_config.NumberColumn("Simulated rework cost", format="dollar"),
        "sim_display_facility_name": st.column_config.TextColumn(
            "Facility name (DISPLAY ONLY)",
            help=(
                "A real CMS facility name attached by a seeded random crosswalk. NOT a key. "
                "2,816 names carry 4,876 synthetic providers, worst case 15:1."
            ),
        ),
    },
)
st.caption(
    "**This table is grouped on `prvdr_num`, the synthetic billing provider id, and never "
    "on the facility name or CCN.** The name column is display-only decoration: grouping "
    "on it would merge up to 15 distinct synthetic providers into one row and inflate its "
    "volume. See the facility-names disclosure at the foot of this page."
)
control_query(
    reconcile.sql_for("Denial prevention — Provider grain is the SYNTHETIC prvdr_num")
    + "\n\n"
    + reconcile.sql_for("Denial prevention — Claims across synthetic providers")
)
st.info(disclosures.NOT_A_FRAUD_SIGNAL, icon=":material/gavel:")

# ---------------------------------------------------------------------------
# Model A
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Model A — pre-submission denial risk")

st.warning(disclosures.MODEL_A_HONESTY, icon=":material/balance:")

if not data.has("model_a_scores"):
    st.info(
        "Model datasets are not available from this data source. They are produced by "
        "`make train` and ship in the demo bundle — build one with `make demo-extract`."
    )
    required_disclosures()
    st.stop()

metrics = data.model_metrics("A")
test_fold = data.model_a_test_fold()

kpi_row(
    [
        Kpi("ROC-AUC (test fold)", "0.6254", "DERIVED", "Regularized logistic, the champion."),
        Kpi("PR-AUC (test fold)", "0.2210", "DERIVED", "Against a 12.05% test-fold base rate."),
        Kpi(
            "Achievable ceiling",
            "~0.68",
            "DERIVED",
            "Scoring with the generator's own latent probability reaches only ~0.68, because "
            "about a third of the denials have no mechanism to detect.",
        ),
        Kpi(
            "Forward test rows",
            f"{len(test_fold):,}",
            "DERIVED",
            "Temporal split at 2021-12-28. Nothing on this card is measured in-sample.",
        ),
    ]
)

st.markdown(f"**Operating point.** {disclosures.OPERATING_POINT}")

left, right = st.columns([3, 2])
with left:
    st.markdown("**Global SHAP importance** — which declared features move the model.")
    shap_global = data.load("model_a_shap_global").nlargest(15, "sim_mean_abs_shap")
    importance = (
        alt.Chart(shap_global)
        .mark_bar()
        .encode(
            y=alt.Y("feature:N", sort="-x", title=None),
            x=alt.X("sim_mean_abs_shap:Q", title="Mean |SHAP|"),
            tooltip=[
                alt.Tooltip("feature:N", title="Feature"),
                alt.Tooltip("sim_mean_abs_shap:Q", title="Mean |SHAP|", format=".4f"),
                alt.Tooltip("sim_share_of_importance:Q", title="Share", format=".1%"),
                alt.Tooltip("sim_reason_code:N", title="Reason code"),
            ],
        )
        .properties(height=380)
    )
    st.altair_chart(importance, use_container_width=True)
    provenance_note(
        "DERIVED",
        "Tree SHAP over the gradient-boosted model, summed from encoded columns back onto "
        "each declared feature so a one-hot payer appears as ONE contribution rather than "
        "forty. Both value columns carry the `sim_` marker: an attribution of a model "
        "fitted on a simulated label moves when the simulation moves.",
    )

with right:
    st.markdown("**Score distribution on the forward test fold**")
    hist = (
        alt.Chart(test_fold)
        .mark_bar(opacity=0.75)
        .encode(
            x=alt.X("sim_denial_risk:Q", bin=alt.Bin(maxbins=40), title="Calibrated denial risk"),
            y=alt.Y("count():Q", title="Claims"),
            color=alt.Color(
                "sim_denial_flag:N",
                title="Actually denied (SIMULATED)",
                scale=alt.Scale(scheme="set2"),
            ),
            tooltip=[alt.Tooltip("count():Q", title="Claims")],
        )
        .properties(height=380)
    )
    st.altair_chart(hist, use_container_width=True)
    provenance_note(
        "SIMULATED",
        "The colour is the simulated denial label. The overlap between the two "
        "distributions is the model's real separability — 0.6254, not a clean split.",
    )

disclosure_block(
    "Why about a third of these denials cannot be explained",
    disclosures.MODEL_A_EXPLANATION_LIMIT,
    icon=":material/help:",
)
with st.expander("Calibration, as the run reported it", icon=":material/tune:"):
    st.markdown(
        f"Expected calibration error **{metrics['calibration']['ece_uncalibrated']:.5f}** before "
        f"isotonic regression and **{metrics['calibration']['ece_calibrated']:.5f}** after, on "
        "the forward test fold. Isotonic calibration improves the probabilities and costs a "
        "little ranking quality: ROC-AUC 0.6254 uncalibrated against 0.6185 calibrated. The "
        "calibrated model is what is served, because a work queue needs a probability that "
        "means what it says more than it needs the fourth decimal of an AUC."
    )

st.divider()
required_disclosures()
