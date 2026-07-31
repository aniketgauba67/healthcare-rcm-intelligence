"""Page 5 — Model & data quality.

The page that checks the other four. Warehouse data-quality checks, input-drift
monitoring, the model cards as the runs wrote them, the bundle's own provenance
register, and — the part §7 actually asks for — **every figure on this dashboard
beside the SQL control query it must equal**, evaluated live.

WHY THE RECONCILIATION IS ON A PAGE AND NOT ONLY IN A TEST
------------------------------------------------------------
It is in a test too (`dashboard/reconcile.py` is pure and takes frames, so it runs
without Streamlit). But a reconciliation that lives only in CI tells a reader
nothing, and this project's whole claim is that a reader can check it rather than
trust it. Showing the control query beside a green row is the difference between
"the totals reconcile" as an assertion and as an artifact.

WHAT THIS PAGE CANNOT TELL YOU
-------------------------------
Both sides of every reconciliation read the same bundle, so a bad COPY out of
PostgreSQL would move both sides together and stay green. What catches that is the
build step copying views verbatim and the PostgreSQL gate in
`sql/quality/view_reconciliation.py`. Stated here rather than left for a reader to
discover, because a green table that quietly cannot fail is worse than no table.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import data, disclosures, reconcile
from dashboard.components import (
    Kpi,
    data_source_caption,
    dataframe,
    kpi_row,
    provenance_note,
    render_page_header,
    render_synthetic_data_banner,
    required_disclosures,
)
from dashboard.model_monitoring import render_model_monitoring
from dashboard.provenance import emitter_for

PAGE_EMITTER = emitter_for("dashboard/pages/model_data_quality.py")

render_page_header(
    "Model & data quality",
    "Warehouse checks, input drift, model cards, bundle provenance, and the "
    "figure-by-figure reconciliation to the SQL control queries.",
    emitter=PAGE_EMITTER,
)
render_synthetic_data_banner()
data_source_caption()


# ---------------------------------------------------------------------------
# Reconciliation — CLAUDE.md §7
# ---------------------------------------------------------------------------

st.subheader("Every figure on this dashboard, against its SQL control query")

try:
    wanted = [
        name for name in (*reconcile.REQUIRED_DATASETS, *reconcile.MODEL_DATASETS) if data.has(name)
    ]
    frames = {name: data.load(name) for name in wanted}
except data.DashboardDataError as error:
    st.error(str(error))
    st.stop()

results = reconcile.run(frames)
failures = reconcile.failures(results)
summary = reconcile.summarize(results)

st.markdown(
    "**Reconciliation status:** "
    f"{summary.declared} declared | {summary.evaluated} evaluated | {summary.passed} passed | "
    f"{summary.failed} failed | {summary.not_evaluated} not evaluated."
)

if summary.all_passed:
    st.success(
        f"**All {summary.declared}/{summary.declared} reconciled figures match their control totals exactly.** "
        "Each control value is derived from a DIFFERENT dataset than the figure — the executive "
        "view's claim count against the enriched claim view, the aging spine's open claims "
        "against `sim_ar_open_flag`, and so on — so this is a second path to the number, not a "
        "second run of the same code.",
        icon=":material/check_circle:",
    )
else:
    if failures:
        st.error(
            f"**{summary.failed} of {summary.evaluated} evaluated figures do not reconcile.** "
            "A dashboard whose totals disagree with the warehouse is reporting something nobody "
            "can check. The failing rows are marked below.",
            icon=":material/error:",
        )
    incomplete = [result for result in results if not result.evaluated]
    if incomplete:
        missing = [result for result in incomplete if result.status == "MISSING_INPUT"]
        errors = [result for result in incomplete if result.status == "ERROR"]
        details = [
            f"- **{result.figure}**: unavailable dataset(s) `{', '.join(result.missing_inputs)}`."
            for result in missing
        ]
        details.extend(
            f"- **{result.figure}**: evaluation error: {result.detail}." for result in errors
        )
        st.warning(
            "**Reconciliation is incomplete.** "
            f"{summary.not_evaluated} of {summary.declared} declared checks were not evaluated "
            "and none are counted as passing:\n\n" + "\n".join(details),
            icon=":material/warning:",
        )

results_frame = reconcile.to_frame(results)
dataframe(
    results_frame,
    emitter=PAGE_EMITTER,
    column_config={
        "figure": st.column_config.TextColumn("Figure on the dashboard", width="large"),
        "dashboard_value": st.column_config.NumberColumn("Dashboard", format="%.4f"),
        "control_value": st.column_config.NumberColumn("Control total", format="%.4f"),
        "difference": st.column_config.NumberColumn("Difference", format="%.6f"),
        "status": st.column_config.TextColumn("Status"),
        "missing_inputs": st.column_config.TextColumn("Unavailable dataset(s)", width="medium"),
        "detail": st.column_config.TextColumn("Evaluation detail", width="large"),
        "reconciles": st.column_config.CheckboxColumn("Passes"),
    },
)

with st.expander("The control queries themselves", icon=":material/database:"):
    for result in results:
        st.markdown(f"**{result.figure}**")
        st.code(result.control_sql.strip(), language="sql")
st.caption(
    "**What this cannot catch.** Both sides read the same bundle, so a view copied wrongly "
    "out of PostgreSQL would move both sides together. The build step copies views "
    "verbatim (`select * from rcm.vw_...`, never recomputed) and "
    "`sql/quality/view_reconciliation.py` runs the same controls against the live "
    "warehouse; this table catches the failure those two cannot, which is a PAGE inventing "
    "its own version of a measure."
)

# ---------------------------------------------------------------------------
# Warehouse data quality
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Warehouse data-quality scorecard")

scorecard = data.load("vw_data_quality_scorecard")
passing = int(scorecard["pass_flag"].fillna(False).astype(bool).sum())
critical_failing = int(
    (
        (scorecard["severity"] == "critical") & ~scorecard["pass_flag"].fillna(False).astype(bool)
    ).sum()
)

kpi_row(
    [
        Kpi("Checks", f"{len(scorecard)}", "DERIVED", "Named checks over the warehouse."),
        Kpi("Passing", f"{passing} / {len(scorecard)}", "DERIVED", ""),
        Kpi(
            "Critical failures",
            f"{critical_failing}",
            "DERIVED",
            "A critical failure fails the reconciliation gate.",
        ),
        Kpi(
            "Subject",
            "warehouse",
            "DERIVED",
            "These measure the DATA, not the business. A failing row is a data-quality "
            "review flag, not a finding about any provider.",
        ),
    ]
)
dataframe(
    scorecard,
    emitter=PAGE_EMITTER,
    column_config={
        "description": st.column_config.TextColumn("Check", width="large"),
        "metric_value": st.column_config.NumberColumn("Value", format="%.4f"),
        "pass_flag": st.column_config.CheckboxColumn("Pass"),
    },
)
st.caption(
    "`subject_provenance` names the class of data under test, which is the column that "
    "keeps this table honest: a check over a SIMULATED subject is measuring our generator, "
    "not the CMS files."
)

# ---------------------------------------------------------------------------
# Input drift
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Input-distribution monitoring")

st.caption(
    "**This view holds no model score and no prediction.** Every row carries "
    "`is_drift_scaffold`. It tracks how the INPUTS move across submission-month cohorts, "
    "and since the inputs are generated, drift here is drift in the simulation — not "
    "evidence that a deployed model is degrading, because nothing here is deployed."
)
render_model_monitoring(data.load("vw_model_monitoring"))

# ---------------------------------------------------------------------------
# Model cards
# ---------------------------------------------------------------------------

st.divider()
st.subheader("The models, as the runs reported them")

if not data.has("model_metrics"):
    st.info("Model metrics ship in the demo bundle; build one with `make demo-extract`.")
else:
    metrics_a = data.model_metrics("A")
    metrics_c = data.model_metrics("C")

    model_a_tab, model_c_tab = st.tabs(
        ["Model A — pre-submission denial risk", "Model C — appeal success & ENR"]
    )

    with model_a_tab:
        st.markdown(disclosures.MODEL_A_HONESTY)
        comparison = metrics_a["comparisons"]["xgboost_minus_logistic"]["roc_auc"]
        baseline = metrics_a["comparisons"]["logistic_minus_payer_rule"]["roc_auc"]
        kpi_row(
            [
                Kpi(
                    "Champion",
                    "logistic + isotonic",
                    "DERIVED",
                    "Chosen on the calibration fold by PR-AUC, held out and strictly earlier "
                    "than the test fold.",
                ),
                Kpi(
                    "ROC-AUC",
                    f"{metrics_a['metrics_test_fold']['logistic']['roc_auc']:.4f}",
                    "DERIVED",
                    "Forward test fold, 4,173 rows.",
                ),
                Kpi(
                    "xgboost − logistic",
                    f"{comparison['point']:+.4f}",
                    "DERIVED",
                    f"[{comparison['ci_low']:+.4f}, {comparison['ci_high']:+.4f}] — an interval "
                    "an order of magnitude wider than the point estimate. No difference.",
                ),
                Kpi(
                    "logistic − payer rule",
                    f"{baseline['point']:+.4f}",
                    "DERIVED",
                    f"[{baseline['ci_low']:+.4f}, {baseline['ci_high']:+.4f}] — excludes zero. "
                    "The model does beat a non-strawman baseline.",
                ),
            ]
        )
        calibration = pd.DataFrame(metrics_a["calibration"]["curve"])
        calibration_chart = (
            alt.Chart(calibration)
            .mark_circle(size=90)
            .encode(
                x=alt.X("mean_predicted:Q", title="Mean predicted probability"),
                y=alt.Y("observed_rate:Q", title="Observed denial rate (SIMULATED)"),
                size=alt.Size("n:Q", title="Claims in bin"),
                tooltip=[
                    alt.Tooltip("bin:Q"),
                    alt.Tooltip("n:Q", format=","),
                    alt.Tooltip("mean_predicted:Q", format=".4f"),
                    alt.Tooltip("observed_rate:Q", format=".4f"),
                    alt.Tooltip("gap:Q", format="+.4f"),
                ],
            )
        )
        diagonal = (
            alt.Chart(pd.DataFrame({"x": [0, calibration["observed_rate"].max()]}))
            .mark_line(strokeDash=[5, 5], color="grey")
            .encode(x="x:Q", y="x:Q")
        )
        st.altair_chart(
            (diagonal + calibration_chart).properties(height=320), use_container_width=True
        )
        provenance_note(
            "DERIVED",
            f"Expected calibration error {metrics_a['calibration']['ece_uncalibrated']:.5f} "
            f"before isotonic and {metrics_a['calibration']['ece_calibrated']:.5f} after. "
            "The vertical axis is the SIMULATED denial rate, so this shows the model is "
            "well calibrated to our generator — nothing more.",
        )
        st.caption(
            f"**Sanity ceiling.** {metrics_a['sanity']['verdict'].capitalize()}: an oracle "
            f"scoring with the generator's own latent probability reaches only "
            f"{metrics_a['sanity']['oracle_roc_auc']}, and a run above "
            f"{metrics_a['sanity']['suspicious_roc_auc']} FAILS as a suspected leak. The "
            f"observed {metrics_a['sanity']['observed_roc_auc']} sits where it should."
        )

    with model_c_tab:
        st.markdown(disclosures.MODEL_C_HONESTY)
        against_rule = metrics_c["comparisons"]["xgboost_minus_category_rule"]["roc_auc"]
        kpi_row(
            [
                Kpi("Champion", "xgboost + isotonic", "DERIVED", "On 193 test rows."),
                Kpi(
                    "ROC-AUC",
                    f"{metrics_c['metrics_test_fold']['xgboost']['roc_auc']:.4f}",
                    "DERIVED",
                    "Barely above chance.",
                ),
                Kpi(
                    "xgboost − category rule",
                    f"{against_rule['point']:+.4f}",
                    "DERIVED",
                    f"[{against_rule['ci_low']:+.4f}, {against_rule['ci_high']:+.4f}] — spans "
                    "zero, and the point estimate is NEGATIVE.",
                ),
                Kpi(
                    "Fitted on / applied to",
                    "967 / 2,663",
                    "SIMULATED",
                    "Selection on the outcome's own decision. TIMELY_FILING denials were "
                    "appealed 0.0% of the time and are still scored.",
                ),
            ]
        )
        st.markdown(disclosures.MODEL_C_NO_SHAP)
        selection = pd.DataFrame(metrics_c["selection_bias"]["by_category"])
        selection_chart = (
            alt.Chart(selection)
            .mark_bar()
            .encode(
                y=alt.Y("sim_denial_category:N", sort="-x", title="Denial category"),
                x=alt.X(
                    "appeal_rate:Q", title="Share of denials appealed", axis=alt.Axis(format="%")
                ),
                color=alt.Color(
                    "no_training_support:N",
                    title="No training support",
                    scale=alt.Scale(scheme="set2"),
                ),
                tooltip=[
                    alt.Tooltip("sim_denial_category:N", title="Category"),
                    alt.Tooltip("denials:Q", format=","),
                    alt.Tooltip("appealed:Q", format=","),
                    alt.Tooltip("appeal_rate:Q", format=".1%"),
                    alt.Tooltip("overturn_rate_given_appealed:Q", title="Overturned", format=".1%"),
                ],
            )
            .properties(height=260)
        )
        st.altair_chart(selection_chart, use_container_width=True)
        provenance_note(
            "SIMULATED",
            "Whether a denial was appealed is a simulated decision, and it is not "
            "independent of the outcome being modelled. This is measured rather than "
            "assumed away, and it cannot be fixed from this data.",
        )

# ---------------------------------------------------------------------------
# The bundle's own provenance register
# ---------------------------------------------------------------------------

st.divider()
st.subheader("What this app is reading, and how each dataset is classified")

described = data.source_description()
vintages = described.get("source_vintages") or {}

kpi_row(
    [
        Kpi(
            "Data source",
            str(described.get("kind", "unknown")),
            "DERIVED",
            "CLAUDE.md §2 locks the hosted demo to the bundled extract.",
        ),
        Kpi(
            "Built from commit",
            str(described.get("git_commit", "unknown"))[:12],
            "DERIVED",
            "A bundle with no commit stamp cannot be pinned by inspection.",
        ),
        Kpi(
            "Tree state",
            "dirty" if described.get("git_tree_dirty") else "clean",
            "DERIVED",
            "Recorded rather than suppressed: a bundle built from uncommitted work is fine "
            "during development and misleading if it ships without saying so.",
        ),
        Kpi("Built at (UTC)", str(described.get("built_at_utc", "unknown"))[:19], "DERIVED", ""),
    ]
)

if vintages:
    st.markdown("**Source vintages, read from `config/sources.yaml` rather than restated.**")
    dataframe(
        pd.DataFrame(sorted(vintages.items()), columns=["source", "vintage"]),
        emitter=PAGE_EMITTER,
    )
    st.warning(disclosures.VINTAGE_SKEW, icon=":material/schedule:")

register = data.manifest()
if not register.empty:
    st.markdown("**The bundle's own provenance register.**")
    dataframe(
        register,
        emitter=PAGE_EMITTER,
        column_config={
            "note": st.column_config.TextColumn("Note", width="large"),
            "grain": st.column_config.TextColumn("Grain", width="medium"),
            "contains_simulated": st.column_config.CheckboxColumn("Contains simulated"),
        },
    )
    st.caption(
        "`contains_simulated` is DECLARED in `src/demo/spec.py`, not inferred from column "
        "spellings. `vw_denial_root_cause` carries no `sim_` column of its own and every "
        "count in it is a count of events that never happened — inferring the flag from "
        "names would have called that table clean."
    )

st.divider()
st.markdown(
    "The full model card is `docs/model_card.md`; the field-level provenance register is "
    "`docs/provenance_register.md` and the data dictionary is `docs/data_dictionary.md`. "
    "Every simulated assumption and its published benchmark is in `docs/assumptions.md`."
)
required_disclosures()
