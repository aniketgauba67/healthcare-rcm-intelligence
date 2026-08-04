"""Safe rendering for the Model & Data Quality monitoring section."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.data import usable_model_monitoring
from dashboard.components import provenance_note


def render_model_monitoring(frame: pd.DataFrame) -> bool:
    """Render monitoring data, or an explicit unavailable state, without fabrication."""
    monitoring, unavailable = usable_model_monitoring(frame)
    if unavailable:
        st.info(unavailable, icon=":material/info:")
        return False

    features = sorted(str(value) for value in monitoring["feature_name"].unique())
    feature = st.selectbox(
        "Monitored feature",
        features,
        index=features.index("denial_rate") if "denial_rate" in features else 0,
    )
    series = monitoring.loc[monitoring["feature_name"] == feature].copy()
    series["month"] = pd.to_datetime(series["sim_submission_year_month"] + "-01", errors="coerce")
    metric_kind = str(series["metric_kind"].iloc[0])
    drift_chart = (
        alt.Chart(series.dropna(subset=["month"]))
        .mark_line(point=alt.OverlayMarkDef(size=14))
        .encode(
            x=alt.X("month:T", title="Claim submission month"),
            y=alt.Y("sim_metric_value:Q", title=f"{feature} ({metric_kind})"),
            tooltip=[
                alt.Tooltip("month:T", title="Month"),
                alt.Tooltip("sim_metric_value:Q", title="Value", format=".4f"),
                alt.Tooltip("sim_n_claims:Q", title="Claims", format=","),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(drift_chart, use_container_width=True)
    provenance_note(
        "SIMULATED",
        "The claim count per month is in the tooltip. Early months are thin in the CMS "
        "extract, so a swing in a rate there is sampling noise rather than drift.",
    )
    return True
