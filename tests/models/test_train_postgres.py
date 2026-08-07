"""The Model A run against the real warehouse. READ-ONLY.

Two things can only be checked here: that the whole pipeline executes on the
actual feature store, and that its honesty tripwire fires on the actual metrics
rather than on a fixture.

These tests only SELECT. They apply no DDL and write nothing to the database.
"""

from __future__ import annotations

import copy
import json

import pytest
from sqlalchemy import text

from src.features.leakage import forbidden_columns, load_model_config
from src.models.train import SuspiciousPerformanceError, run_model_a

pytestmark = pytest.mark.integration

# Enough resamples for the report to have the right shape, not enough to be a
# real interval. The reported intervals come from `make train`.
_FAST_RESAMPLES = 25


@pytest.fixture(scope="module")
def engine():
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    from sqlalchemy import create_engine

    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")
    return eng


@pytest.fixture(scope="module")
def report(engine, tmp_path_factory):
    return run_model_a(
        engine,
        artifact_dir=tmp_path_factory.mktemp("model_a"),
        n_resamples=_FAST_RESAMPLES,
    )


def test_the_run_produces_every_section_the_model_card_quotes(report) -> None:
    for section in (
        "split",
        "folds",
        "champion_selection",
        "metrics_test_fold",
        "comparisons",
        "calibration",
        "threshold",
        "dollars_at_risk",
        "slices",
        "shap",
        "sanity",
    ):
        assert section in report, f"the report is missing {section}"
    assert {"payer", "service_line", "value_band"} <= set(report["slices"])


def test_the_baseline_comparison_is_reported_not_just_the_winner(report) -> None:
    """docs/project_rules.md §7: the definition of done is the comparison, both directions."""
    models = report["metrics_test_fold"]
    for name in ("base_rate", "payer_rule", "logistic", "xgboost"):
        assert name in models
    difference = report["comparisons"]["xgboost_minus_logistic"]["roc_auc"]
    assert difference["ci_low"] < difference["ci_high"]


def test_performance_sits_below_the_documented_noise_ceiling(report) -> None:
    """The headline claim of this phase, asserted rather than asserted-about."""
    bounds = load_model_config()["sanity_bounds"]
    for name, metrics in report["metrics_test_fold"].items():
        assert metrics["roc_auc"] <= float(bounds["suspicious_roc_auc"]), (
            f"{name} scored ROC-AUC {metrics['roc_auc']} — above the leak threshold"
        )
        assert metrics["pr_auc"] <= float(bounds["suspicious_pr_auc"]), name


def test_no_forbidden_column_reached_the_matrix(report, engine) -> None:
    """Re-derives the matrix and checks it against the blacklist, independently."""
    from src.features.build import MODEL_A_FEATURES, build_model_a_frame
    from src.models.preprocess import prepare_matrix

    matrix = prepare_matrix(build_model_a_frame(engine), MODEL_A_FEATURES)
    blacklist = forbidden_columns("A")
    assert not (set(matrix.columns) & blacklist)
    assert len(matrix.columns) == report["features"]


def test_the_written_report_is_parseable_by_something_other_than_python(engine, tmp_path) -> None:
    """NaN is a valid float and not valid JSON; the dashboard reads this file."""
    run_model_a(engine, artifact_dir=tmp_path, n_resamples=_FAST_RESAMPLES)
    text_ = (tmp_path / "metrics.json").read_text()
    parsed = json.loads(
        text_, parse_constant=lambda c: pytest.fail(f"non-standard JSON token: {c}")
    )
    assert parsed["claims"] > 0
    for name in ("calibration_curve.png", "shap_global_importance.png", "slice_payer.csv"):
        assert (tmp_path / name).exists(), f"{name} was not written"


def test_a_leaking_run_stops_instead_of_reporting_a_great_number(engine, tmp_path) -> None:
    """The tripwire, end to end: lower the bound and the same run must refuse.

    Proving the check is live, rather than a threshold nothing ever approaches.
    """
    config = copy.deepcopy(load_model_config())
    config["sanity_bounds"]["suspicious_roc_auc"] = 0.10

    with pytest.raises(SuspiciousPerformanceError, match="leak to hunt"):
        run_model_a(engine, config=config, artifact_dir=tmp_path, n_resamples=_FAST_RESAMPLES)

    assert not (tmp_path / "metrics.json").exists(), "a suspicious run wrote its metrics anyway"
    diagnosis = json.loads((tmp_path / "metrics_SUSPICIOUS.json").read_text())
    assert "SUSPICIOUS" in diagnosis["sanity"]["verdict"]
