"""Page 4 — Predictive work queue.

Two queues, and they answer different questions with different models.

* **Recovery queue (Model C).** Which already-denied claims are worth appealing.
  Its explanation is the policy **tier** and the **recommended action** — never
  SHAP, deliberately, for the reason printed on the page.
* **Prevention queue (Model A).** Which not-yet-submitted claims look risky, with
  per-claim **SHAP drivers** mapped to analyst reason codes and actions.

THE THING A WORK QUEUE HIDES BEST, SAID FIRST
----------------------------------------------
`vw_work_queue_priority`'s where clause is `where sim_denial_flag or ar_open_flag`.
**Membership in this list is the label.** Not a column in it — the membership. A
queue presented as a neutral roster of claims needing attention is presenting a
selection that already knows which claims went wrong, and no blacklist entry can
express that because it is not a column. Nothing about the heuristic queue below
is a prediction; it is a triage ordering over claims whose outcome is already
known. The Model C queue has the same property, restricted further to denials.

MODEL C IS A NEGATIVE RESULT AND THIS PAGE LEADS WITH IT
---------------------------------------------------------
At 10% capacity, sorting by **largest denial first captures 65.7%** of recoverable
dollars against **61.0%** for the Expected Net Recovery score and **59.8%** for the
tiered queue that ships. The probability does not earn its place in the ordering.
ENR ships for the *cutoff* and the *tiering*; a page that showed the ranked list
without that sentence would be selling the opposite of what was measured.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import data, disclosures, reconcile
from dashboard.components import (
    Kpi,
    control_query,
    data_source_caption,
    dataframe,
    disclosure_block,
    kpi_row,
    money,
    provenance_note,
    render_page_header,
    required_disclosures,
)
from src.api.tables import build_heuristic_queue_table

MEMBERSHIP_WARNING = (
    "**Membership in this queue is the label.** The view behind it selects claims "
    "`where sim_denial_flag or ar_open_flag` — every row is here BECAUSE the simulation "
    "already denied it or left it unpaid. That is not a column anything can blacklist; it "
    "is the selection itself. Read this as a triage ordering over known-bad claims, never "
    "as a prediction of which claims will go bad. The prevention queue further down is the "
    "one that predicts, and it is scored before submission on claims whose outcome the "
    "model has not seen."
)

render_page_header(
    "Predictive work queue",
    "Recovery worklist (Model C), prevention worklist (Model A), and what each one knows.",
    banner_extra=MEMBERSHIP_WARNING,
)
data_source_caption()

role = st.session_state.get("role", "Executive")
if role != "Analyst":
    st.info(
        "You are in the **Executive** role-like view, so claim identifiers are hidden and "
        "the queues are shown as distributions. Switch to **Analyst** in the sidebar for "
        "the per-claim worklist. This is a demonstration of a role boundary, not a "
        "security control — the data ships in the repository and nothing here is protected.",
        icon=":material/visibility_off:",
    )


# ---------------------------------------------------------------------------
# Model C — the recovery queue
# ---------------------------------------------------------------------------

st.subheader("Recovery queue — Model C, Expected Net Recovery")

st.error(disclosures.MODEL_C_HONESTY, icon=":material/trending_down:")

if not data.has("model_c_work_queue"):
    st.info(
        "The Model C queue is produced by `make train-appeal` and ships in the demo "
        "bundle. Build one with `make demo-extract`, or read the heuristic scaffold queue "
        "below, which needs no model artifacts."
    )
else:
    metrics_c = data.model_metrics("C")
    economics = metrics_c["economics"]
    queue_metrics = metrics_c["queue"]

    queue_mode = st.radio(
        "Queue mode",
        ["backtest", "live_snapshot"],
        horizontal=True,
        help=(
            "`backtest` triages every claim on the day its denial posted — 468 claims. "
            "`live_snapshot` is one instant, 2024-07-10, and is DEGENERATE on this "
            "warehouse: 1 claim is still inside its filing window and 467 are out of it. "
            "It is shown rather than dropped because the degeneracy is a true fact about "
            "the data."
        ),
    )
    queue = data.work_queue(queue_mode)

    if queue_mode == "live_snapshot":
        st.warning(
            f"**This snapshot is degenerate and is shown anyway.** {queue_metrics['live_snapshot']['caveat']}",
            icon=":material/warning:",
        )

    kpi_row(
        [
            Kpi(
                "Claims in queue",
                f"{len(queue):,}",
                "SIMULATED",
                "Every one is an already-denied claim.",
            ),
            Kpi(
                "Recoverable dollars",
                money(float(queue["sim_recoverable_amt"].sum())),
                "SIMULATED",
                "Simulated denied amount. This is sim_denied_amount under a "
                "task-oriented name, and it carries the marker for that reason.",
            ),
            Kpi(
                "Expected net recovery",
                money(float(queue["sim_expected_net_recovery"].sum())),
                "SIMULATED",
                "p(overturn) x recoverable x recovery ratio, less the appeal processing cost.",
            ),
            Kpi(
                "Recovery ratio",
                f"{economics['recovery_ratio_fit_fold']:.4f}",
                "DERIVED",
                economics["recovery_ratio_note"],
            ),
        ]
    )

    st.markdown(
        f"**How the ordering is built, and what it is not.** Claims are ordered by policy "
        f"**tier** first and by expected net recovery within tier, so a claim "
        f"{economics['deadline_critical_days']} days from its filing deadline outranks a "
        f"larger one by design. The appeal processing cost of "
        f"${economics['appeal_processing_cost_usd']:,.0f} is a constant subtracted from every "
        f"claim, so it decides which claims are worth working — the **cutoff** — and not the "
        f"order they are worked in. The filing window is "
        f"{economics['filing_window_days']} days."
    )

    tier_counts = queue["tier"].value_counts().rename_axis("tier").reset_index(name="claims")
    left, right = st.columns([2, 3])
    with left:
        tier_chart = (
            alt.Chart(tier_counts)
            .mark_arc(innerRadius=55)
            .encode(
                theta=alt.Theta("claims:Q"),
                color=alt.Color("tier:N", title="Policy tier", scale=alt.Scale(scheme="tableau10")),
                tooltip=[alt.Tooltip("tier:N"), alt.Tooltip("claims:Q", format=",")],
            )
            .properties(height=260)
        )
        st.altair_chart(tier_chart, use_container_width=True)
        provenance_note(
            "DERIVED",
            "The tier is a POLICY output — a statement about our process, computed from the "
            "simulated deadline and denial category by a rule we wrote. It is auditable and "
            "true by construction, which is precisely what the probability is not.",
        )
    with right:
        value_chart = (
            alt.Chart(queue)
            .mark_circle(opacity=0.55)
            .encode(
                x=alt.X(
                    "sim_recoverable_amt:Q",
                    title="Recoverable $ (SIMULATED)",
                    scale=alt.Scale(type="symlog"),
                ),
                y=alt.Y("sim_p_overturn:Q", title="p(overturn) (SIMULATED)"),
                size=alt.Size("sim_expected_net_recovery:Q", title="Expected net recovery"),
                color=alt.Color("tier:N", title="Tier", scale=alt.Scale(scheme="tableau10")),
                tooltip=[
                    alt.Tooltip("sim_denial_category:N", title="Category"),
                    alt.Tooltip("sim_recoverable_amt:Q", title="Recoverable", format="$,.0f"),
                    alt.Tooltip("sim_p_overturn:Q", title="p(overturn)", format=".3f"),
                    alt.Tooltip("sim_expected_net_recovery:Q", title="ENR", format="$,.0f"),
                    alt.Tooltip("sim_days_to_deadline:Q", title="Days to deadline"),
                ],
            )
            .properties(height=260)
        )
        st.altair_chart(value_chart, use_container_width=True)
        provenance_note(
            "SIMULATED",
            "The dollar axis spans four orders of magnitude and the ten largest denied "
            "claims hold 50.9% of the money at risk. That concentration is why sorting by "
            "size alone beats the score, and why no single capture percentage from this "
            "chart should be quoted as a result.",
        )

    st.markdown("**Capacity experiment — the measurement that makes this a negative result.**")
    capture = pd.DataFrame(
        [
            {"strategy": "largest denial first (no model)", "captured": 0.657},
            {"strategy": "Expected Net Recovery score", "captured": 0.610},
            {"strategy": "tiered queue (what ships)", "captured": 0.598},
            {"strategy": "random", "captured": 0.007},
        ]
    )
    capture_chart = (
        alt.Chart(capture)
        .mark_bar()
        .encode(
            y=alt.Y("strategy:N", sort="-x", title=None),
            x=alt.X(
                "captured:Q",
                title="Share of recoverable dollars captured at 10% capacity",
                axis=alt.Axis(format="%"),
            ),
            tooltip=[alt.Tooltip("strategy:N"), alt.Tooltip("captured:Q", format=".1%")],
        )
        .properties(height=170)
    )
    st.altair_chart(capture_chart, use_container_width=True)
    provenance_note(
        "DERIVED",
        "Read the top bar first. A rule that needs no model beats both model-based "
        "orderings, and the tiered queue that ships is the LOWEST of the three. It ships "
        "anyway because tiering encodes a filing deadline the dollar sort ignores — a "
        "policy choice, made in the open, not a performance claim.",
    )

    disclosure_block(
        "Why there is no SHAP for Model C",
        disclosures.MODEL_C_NO_SHAP,
        icon=":material/psychology_alt:",
    )
    disclosure_block(
        "Model C is fitted on the denials somebody chose to appeal",
        disclosures.MODEL_C_SELECTION_LIMIT,
        expanded=True,
        icon=":material/filter_alt:",
    )

    if role == "Analyst":
        st.markdown("**The worklist, in the order the shipped queue ranks it.**")
        limit = st.slider("Rows", 10, min(500, len(queue)), min(50, len(queue)), step=10)
        worklist = queue.sort_values(["tier_rank", "queue_position"], kind="mergesort").head(limit)
        dataframe(
            worklist[
                [
                    "queue_position",
                    "claim_sk",
                    "tier",
                    "recommended_action",
                    "sim_denial_category",
                    "sim_recoverable_amt",
                    "sim_p_overturn",
                    "sim_expected_net_recovery",
                    "sim_days_to_deadline",
                ]
            ],
            column_config={
                "queue_position": st.column_config.NumberColumn("#", format="%d"),
                "recommended_action": st.column_config.TextColumn(
                    "Recommended action",
                    help="Rule-based and auditable. This is Model C's explanation; there is no "
                    "SHAP for it, deliberately.",
                    width="large",
                ),
                "sim_recoverable_amt": st.column_config.NumberColumn(
                    "Recoverable", format="dollar"
                ),
                "sim_p_overturn": st.column_config.NumberColumn("p(overturn)", format="%.3f"),
                "sim_expected_net_recovery": st.column_config.NumberColumn("ENR", format="dollar"),
                "sim_days_to_deadline": st.column_config.NumberColumn("Days left", format="%d"),
            },
        )
        st.caption(
            "`tier` and `recommended_action` carry no `sim_` marker because they are "
            "statements about OUR process rather than about the simulated world. Every "
            "value column does carry it — including `sim_recoverable_amt`, which is "
            "`sim_denied_amount` renamed and lost its marker for four commits before the "
            "[QUEUE-PREFIX] fix put it back."
        )
    else:
        st.caption(
            "Per-claim rows are hidden in the Executive view. The distributions above are "
            "computed over the whole queue, not a sample."
        )

# ---------------------------------------------------------------------------
# Model A — the prevention queue, with SHAP reasons
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Prevention queue — Model A, with per-claim SHAP drivers")

st.warning(disclosures.OPERATING_POINT, icon=":material/speed:")

if not data.has("model_a_reason_codes"):
    st.info(
        "Per-claim drivers are produced by `make train` and ship in the demo bundle. "
        "Build one with `make demo-extract`."
    )
else:
    reasons = data.load("model_a_reason_codes")
    test_fold = data.model_a_test_fold()
    flagged = test_fold.loc[test_fold["top_decile_flag"]].sort_values(
        "sim_denial_risk", ascending=False
    )

    kpi_row(
        [
            Kpi(
                "Forward test claims",
                f"{len(test_fold):,}",
                "DERIVED",
                "Temporal split at 2021-12-28; none of these were used to fit or calibrate.",
            ),
            Kpi(
                "Flagged at 10% capacity",
                f"{len(flagged):,}",
                "DERIVED",
                "The top decile by calibrated score — the operating point a team can run.",
            ),
            Kpi(
                "Precision at that point",
                "26.3%",
                "DERIVED",
                "2.2x the 12.05% base rate. Three in four flagged claims would NOT have been denied.",
            ),
            Kpi(
                "Recall at that point",
                "20.9%",
                "DERIVED",
                "Four in five denials are not caught. This is a triage aid, not a filter.",
            ),
        ]
    )

    st.info(disclosures.MODEL_A_EXPLANATION_LIMIT, icon=":material/help:")

    if role == "Analyst":
        st.markdown("**Pick a flagged claim to see what drove its score.**")
        choice = st.selectbox(
            "Claim",
            flagged["claim_sk"].head(200).tolist(),
            format_func=lambda sk: (
                f"claim_sk {sk} — risk "
                f"{float(flagged.loc[flagged['claim_sk'] == sk, 'sim_denial_risk'].iloc[0]):.3f}"
            ),
        )
        claim_row = flagged.loc[flagged["claim_sk"] == choice].iloc[0]
        drivers = reasons.loc[reasons["claim_sk"] == choice].sort_values("driver_rank")

        left, right = st.columns([2, 3])
        with left:
            st.metric("Calibrated denial risk", f"{float(claim_row['sim_denial_risk']):.4f}")
            st.caption(
                f"Actually denied in the simulation: **{bool(claim_row['sim_denial_flag'])}**. "
                f"Submitted {pd.Timestamp(claim_row['sim_submission_date']).date()}. "
                "Fold: `test` — a forward prediction, not an in-sample fit."
            )
        with right:
            if drivers.empty:
                st.caption("No SHAP row shipped for this claim.")
            else:
                driver_chart = (
                    alt.Chart(drivers)
                    .mark_bar()
                    .encode(
                        y=alt.Y("feature:N", sort=alt.SortField("driver_rank"), title=None),
                        x=alt.X("sim_shap_contribution:Q", title="SHAP contribution (log-odds)"),
                        color=alt.Color(
                            "direction:N", title="Direction", scale=alt.Scale(scheme="set2")
                        ),
                        tooltip=[
                            alt.Tooltip("feature:N", title="Feature"),
                            alt.Tooltip(
                                "sim_shap_contribution:Q", title="Contribution", format=".4f"
                            ),
                            alt.Tooltip("reason_code:N", title="Reason code"),
                            alt.Tooltip("analyst_action:N", title="Action"),
                        ],
                    )
                    .properties(height=200)
                )
                st.altair_chart(driver_chart, use_container_width=True)

        if not drivers.empty:
            dataframe(
                drivers[["driver_rank", "reason_code", "feature", "direction", "analyst_action"]],
                column_config={
                    "driver_rank": st.column_config.NumberColumn("#", format="%d"),
                    "analyst_action": st.column_config.TextColumn(
                        "Recommended action", width="large"
                    ),
                },
            )
        st.caption(
            "**A waterfall is not a causal account.** SHAP explains the model, not the world, "
            "and it will decompose a claim the generator denied for no reason at all with the "
            "same confidence it decomposes one with a missing prior authorization. About a "
            "third of these denials are in the first category."
        )
    else:
        st.markdown("**Which reason codes drive the flagged population**")
        top_reasons = (
            reasons.loc[reasons["driver_rank"] <= 3]
            .groupby(["reason_code", "analyst_action"], as_index=False)
            .agg(claims=("claim_sk", "nunique"))
            .nlargest(12, "claims")
        )
        reason_chart = (
            alt.Chart(top_reasons)
            .mark_bar()
            .encode(
                y=alt.Y("reason_code:N", sort="-x", title="Reason code"),
                x=alt.X("claims:Q", title="Claims with this in their top 3 drivers"),
                tooltip=[
                    alt.Tooltip("reason_code:N"),
                    alt.Tooltip("analyst_action:N", title="Action"),
                    alt.Tooltip("claims:Q", format=","),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(reason_chart, use_container_width=True)
        provenance_note(
            "DERIVED",
            "Reason codes and analyst actions are this project's own mapping "
            "(`src/models/explain.py`), attached to declared features. The SHAP values "
            "underneath carry the `sim_` marker because they attribute a model fitted on a "
            "simulated label.",
        )

# ---------------------------------------------------------------------------
# The heuristic scaffold queue
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Heuristic scaffold queue — no model at all")

try:
    heuristic = build_heuristic_queue_table(data.load("vw_work_queue_priority"), limit=25)
except data.DashboardDataError as error:
    st.error(str(error))
else:
    st.caption(
        "Dollars at stake scaled by an age factor. **No learned weights, no calibration, "
        "not a model score and not an Expected Net Recovery.** It exists so a deployment "
        "with no model artifacts still has a worklist, and every row carries "
        "`is_heuristic_placeholder`."
    )
    columns = [
        c
        for c in (
            "sim_heuristic_priority_score",
            "sim_dollars_at_stake",
            "sim_action_type",
            "sim_denial_category",
            "age_days",
            "priority_tier",
            "is_heuristic_placeholder",
        )
        if c in heuristic.columns
    ]
    if role == "Analyst":
        columns = ["claim_sk", *columns]
    dataframe(heuristic[columns])
    st.caption(
        "Three of these column names are re-marked at this boundary and do NOT carry the "
        "marker in the warehouse view: `dollars_at_stake`, `heuristic_priority_score` and "
        "`action_type`. All three are derived from simulated values — `action_type` is a "
        "CASE on `sim_denial_flag`, so it is the label wearing a process name. The view is "
        "the thing that needs fixing; re-marking here means a reader is not shown an "
        "unmarked simulated value while that happens."
    )
    control_query(reconcile.sql_for("Work queue — Heuristic scaffold rows"))

st.divider()
disclosure_block(
    "How dollars at risk should and should not be quoted",
    disclosures.DOLLARS_AT_RISK,
    icon=":material/warning:",
)
st.info(disclosures.NOT_A_FRAUD_SIGNAL, icon=":material/gavel:")
required_disclosures()
