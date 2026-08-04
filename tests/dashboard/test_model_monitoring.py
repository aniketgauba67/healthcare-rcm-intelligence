"""Model monitoring must degrade honestly on empty or partial warehouse data."""

from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

from dashboard.data import MONITORING_COLUMNS, usable_model_monitoring


def test_empty_monitoring_frame_has_an_unavailable_state() -> None:
    frame = pd.DataFrame(columns=sorted(MONITORING_COLUMNS))
    usable, reason = usable_model_monitoring(frame)
    assert usable.empty
    assert reason
    assert "no monitoring cohorts" in reason


def test_missing_monitoring_columns_are_named() -> None:
    usable, reason = usable_model_monitoring(pd.DataFrame({"feature_name": ["denial_rate"]}))
    assert usable.empty
    assert reason
    assert "metric_kind" in reason
    assert "sim_metric_value" in reason


def test_partial_rows_keep_only_chartable_monitoring_content() -> None:
    frame = pd.DataFrame(
        [
            {
                "sim_submission_year_month": "2023-01",
                "feature_name": "denial_rate",
                "metric_kind": "outcome_rate",
                "sim_metric_value": 0.12,
                "sim_n_claims": 10,
            },
            {
                "sim_submission_year_month": "2023-02",
                "feature_name": "denial_rate",
                "metric_kind": None,
                "sim_metric_value": 0.13,
                "sim_n_claims": 12,
            },
        ]
    )
    usable, reason = usable_model_monitoring(frame)
    assert reason is None
    assert len(usable) == 1
    assert usable.iloc[0]["metric_kind"] == "outcome_rate"


def test_empty_monitoring_frame_renders_the_unavailable_state(tmp_path) -> None:
    page = tmp_path / "empty_monitoring.py"
    page.write_text(
        """
import pandas as pd
from dashboard.data import MONITORING_COLUMNS
from dashboard.model_monitoring import render_model_monitoring

render_model_monitoring(pd.DataFrame(columns=sorted(MONITORING_COLUMNS)))
"""
    )
    app = AppTest.from_file(page, default_timeout=30).run()
    assert not app.exception
    assert len(app.info) == 1
    assert "no monitoring cohorts" in app.info[0].value
