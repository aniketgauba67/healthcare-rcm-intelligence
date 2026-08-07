"""Same seed, same numbers — the property the model card's honesty rests on.

docs/project_rules.md §7 states this requirement for the simulation. It matters just as much
for the models, for a duller reason: every figure in `docs/model_card.md` is
copied from `models_artifacts/*/metrics.json`, so a run that quietly varies makes
the card unverifiable by the next person who runs it.

Written after one Model A run diverged from five others in the same session
(ROC-AUC difference +0.0026 against +0.0003, ECE 0.02056 against 0.01964). It
could not be reproduced afterwards — two consecutive full 1,000-resample runs
came out byte-identical — so the cause is unknown rather than fixed. An
unexplained one-off is exactly the thing that needs a standing check rather than
a note, because the next occurrence will otherwise look like a real change.

XGBoost is the first place to look if this ever fires: it is configured with
`n_jobs: 4`, and thread-count-dependent floating-point reduction order is the
usual cause of a tree model that will not reproduce.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
import pytest

from src.features.build import MODEL_A_FEATURES, labels
from src.features.leakage import load_model_config
from src.models.advanced import gradient_boosted_model
from src.models.baselines import logistic_baseline
from src.models.preprocess import prepare_matrix
from src.models.train import make_folds


def _digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values, dtype=float).tobytes()).hexdigest()


def _engine():  # noqa: ANN202
    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")
    return create_engine(url)


@pytest.mark.integration
def test_fitting_twice_gives_identical_scores() -> None:
    """The cheap half: refit in one process and compare score vectors bitwise.

    Both estimators, because the linear model reproducing tells you nothing about
    the tree model, and the tree model is the one with a thread count.
    """
    from src.features.build import build_model_a_frame

    config = load_model_config()
    frame = build_model_a_frame(_engine(), config)
    matrix = prepare_matrix(frame, MODEL_A_FEATURES)
    y = labels(frame).to_numpy()
    folds = make_folds(frame, config)
    x_fit, y_fit, x_test = matrix.loc[folds.fit], y[folds.fit], matrix.loc[folds.test]

    for name, factory in (
        ("logistic", logistic_baseline),
        ("xgboost", gradient_boosted_model),
    ):
        first = factory(MODEL_A_FEATURES, config).fit(x_fit, y_fit).predict_proba(x_test)[:, 1]
        second = factory(MODEL_A_FEATURES, config).fit(x_fit, y_fit).predict_proba(x_test)[:, 1]
        assert _digest(first) == _digest(second), (
            f"{name} does not reproduce within a single process. If this is xgboost, "
            "check estimators.xgboost.n_jobs in config/model.yaml — parallel reduction "
            "order is the usual cause."
        )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RCM_SLOW_TESTS") != "1",
    reason="two full 1,000-resample runs; set RCM_SLOW_TESTS=1 to run",
)
def test_two_full_runs_agree_on_every_reported_number(tmp_path) -> None:  # noqa: ANN001
    """The expensive half, and the one that actually covers the model card.

    Compares the whole report rather than a headline, because the divergence that
    prompted this test showed up in a calibration statistic, not in an AUC.
    """
    import json

    from src.models.train import json_safe, run_model_a

    engine = _engine()
    reports = []
    for i in (0, 1):
        report = run_model_a(engine, artifact_dir=tmp_path / f"run{i}", n_resamples=1000)
        report.pop("generated_at_utc")
        reports.append(json.dumps(json_safe(report), sort_keys=True, default=str))
    assert reports[0] == reports[1], "two identical runs produced different reports"
