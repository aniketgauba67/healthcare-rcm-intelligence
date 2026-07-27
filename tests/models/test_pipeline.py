"""The training pipeline's structural guarantees, tested without a database.

These are the properties that make the reported numbers mean what they say:
the folds run forward in time, the calibrator does not refit the model it is
calibrating, preprocessing statistics come from the fit fold only, the SHAP
attribution lands on the right feature, and a suspiciously good score stops the
run instead of being written out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.build import MODEL_A_FEATURES
from src.features.spec import FeatureSet, FeatureSpec
from src.models.advanced import gradient_boosted_model
from src.models.baselines import BaseRateBaseline, PayerRuleBaseline, logistic_baseline
from src.models.calibrate import calibrate, method_from_config
from src.models.explain import REASON_CODES, _owner_of, unmapped_features
from src.models.preprocess import build_preprocessor, median_fingerprint, prepare_matrix
from src.models.train import make_folds, sanity_verdict

CONFIG: dict = {
    "seed": 1337,
    "split": {
        "strategy": "temporal",
        "time_column": "sim_submission_date",
        "train_quantile": 0.80,
        "calibration_quantile_of_train": 0.80,
    },
    "estimators": {
        "logistic": {"C": 1.0, "max_iter": 500, "solver": "lbfgs"},
        "xgboost": {
            "n_estimators": 20,
            "max_depth": 3,
            "learning_rate": 0.1,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "min_child_weight": 5.0,
            "reg_lambda": 1.0,
            "n_jobs": 1,
        },
    },
    "sanity_bounds": {
        "oracle_roc_auc": 0.68,
        "suspicious_roc_auc": 0.75,
        "suspicious_pr_auc": 0.95,
    },
    "calibration": "isotonic",
}

TOY_FEATURES = FeatureSet(
    model="A",
    specs=(
        FeatureSpec("risk_score", "numeric", "a number", sources=("risk_score",)),
        FeatureSpec("flag", "boolean", "a flag", sources=("flag",)),
        FeatureSpec("sim_payer_id", "categorical", "the payer", sources=("sim_payer_id",)),
    ),
    label="sim_denial_flag",
    time_column="sim_submission_date",
    passthrough=("claim_sk", "sim_submission_date", "sim_denial_flag"),
)


def toy_frame(n: int = 900, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    risk = rng.uniform(0, 1, n)
    payer = rng.choice(["A", "B", "C"], n)
    flag = rng.uniform(0, 1, n) < 0.3
    p = 0.05 + 0.2 * risk + 0.1 * flag
    return pd.DataFrame(
        {
            "claim_sk": np.arange(n),
            "sim_submission_date": pd.date_range("2018-01-01", periods=n, freq="D"),
            "risk_score": risk,
            "flag": flag,
            "sim_payer_id": payer,
            "sim_denial_flag": (rng.uniform(0, 1, n) < p).astype(int),
        }
    )


# --- folds -----------------------------------------------------------------


def test_the_three_folds_run_strictly_forward_in_time() -> None:
    frame = toy_frame()
    folds = make_folds(frame, CONFIG)
    dates = frame["sim_submission_date"]
    assert dates[folds.fit].max() <= dates[folds.calibrate].min()
    assert dates[folds.calibrate].max() < dates[folds.test].min()


def test_the_folds_partition_the_frame_without_overlap() -> None:
    frame = toy_frame()
    folds = make_folds(frame, CONFIG)
    stacked = folds.fit.astype(int) + folds.calibrate.astype(int) + folds.test.astype(int)
    assert (stacked == 1).all(), "a claim landed in two folds, or in none"


# --- preprocessing (CLAUDE.md 4.4) -----------------------------------------


def test_preprocessing_statistics_come_from_the_fit_fold_only() -> None:
    """The control: fitting on everything must produce different medians."""
    frame = toy_frame()
    frame.loc[frame.index[-200:], "risk_score"] = 50.0  # a shift in the test period
    folds = make_folds(frame, CONFIG)
    matrix = prepare_matrix(frame, TOY_FEATURES)

    fit_only = build_preprocessor(TOY_FEATURES).fit(matrix.loc[folds.fit])
    everything = build_preprocessor(TOY_FEATURES).fit(matrix)
    assert not np.allclose(median_fingerprint(fit_only), median_fingerprint(everything))


def test_the_matrix_handed_to_an_estimator_holds_no_passthrough_columns() -> None:
    frame = toy_frame()
    matrix = prepare_matrix(frame, TOY_FEATURES)
    assert set(matrix.columns) == set(TOY_FEATURES.names)
    for column in ("claim_sk", "sim_submission_date", "sim_denial_flag"):
        assert column not in matrix.columns


# --- baselines and the advanced model ---------------------------------------


def test_every_estimator_exposes_the_same_scoring_interface() -> None:
    frame = toy_frame()
    matrix = prepare_matrix(frame, TOY_FEATURES)
    y = frame["sim_denial_flag"].to_numpy()
    for model in (
        BaseRateBaseline(),
        PayerRuleBaseline(),
        logistic_baseline(TOY_FEATURES, CONFIG),
        gradient_boosted_model(TOY_FEATURES, CONFIG),
    ):
        scores = model.fit(matrix, y).predict_proba(matrix)[:, 1]
        assert scores.shape == (len(frame),)
        assert ((scores >= 0) & (scores <= 1)).all()


def test_the_base_rate_baseline_predicts_the_fit_folds_base_rate() -> None:
    frame = toy_frame()
    matrix = prepare_matrix(frame, TOY_FEATURES)
    folds = make_folds(frame, CONFIG)
    y = frame["sim_denial_flag"].to_numpy()
    model = BaseRateBaseline().fit(matrix.loc[folds.fit], y[folds.fit])
    scores = model.predict_proba(matrix.loc[folds.test])[:, 1]
    assert scores.min() == scores.max() == pytest.approx(y[folds.fit].mean())


def test_an_unseen_payer_falls_back_to_the_base_rate() -> None:
    frame = toy_frame()
    matrix = prepare_matrix(frame, TOY_FEATURES)
    y = frame["sim_denial_flag"].to_numpy()
    model = PayerRuleBaseline().fit(matrix, y)
    unseen = matrix.head(1).assign(sim_payer_id="PAYER_NEVER_SEEN")
    assert model.predict_proba(unseen)[0, 1] == pytest.approx(model.base_rate_)


def test_both_estimators_see_the_same_declared_features() -> None:
    """Otherwise 'advanced beats baseline' is a claim about preprocessing."""
    linear = logistic_baseline(MODEL_A_FEATURES, CONFIG).named_steps["preprocess"]
    trees = gradient_boosted_model(MODEL_A_FEATURES, CONFIG).named_steps["preprocess"]
    assert [name for name, _, cols in linear.transformers] == [
        name for name, _, cols in trees.transformers
    ]
    assert [cols for _, _, cols in linear.transformers] == [
        cols for _, _, cols in trees.transformers
    ]


# --- calibration ------------------------------------------------------------


def test_the_calibrator_does_not_refit_the_model_it_wraps() -> None:
    """If it refitted, the calibration fold would silently become training data."""
    frame = toy_frame()
    matrix = prepare_matrix(frame, TOY_FEATURES)
    y = frame["sim_denial_flag"].to_numpy()
    folds = make_folds(frame, CONFIG)

    model = logistic_baseline(TOY_FEATURES, CONFIG).fit(matrix.loc[folds.fit], y[folds.fit])
    before = model.named_steps["estimator"].coef_.copy()
    calibrate(model, matrix.loc[folds.calibrate], y[folds.calibrate])
    assert np.allclose(before, model.named_steps["estimator"].coef_)


def test_calibration_repairs_a_model_that_overstates_every_probability() -> None:
    """Class weighting is the standard way to get good ranking and bad probabilities.

    `class_weight="balanced"` leaves the ordering essentially untouched and
    inflates every probability toward 0.5. Isotonic is monotone, so it cannot
    repair ranking — which is precisely why it is the right instrument here and
    why this test isolates the one thing calibration is for.
    """
    from sklearn.linear_model import LogisticRegression

    from src.models.evaluate import expected_calibration_error

    rng = np.random.default_rng(0)
    n = 9000
    x = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    p = 1 / (1 + np.exp(-(-2.2 + 1.5 * x["a"] + 0.8 * x["b"])))
    y = (rng.uniform(size=n) < p).astype(int)
    fit, cal, test = slice(0, 4000), slice(4000, 6500), slice(6500, n)

    model = LogisticRegression(class_weight="balanced").fit(x[fit], y[fit])
    before = expected_calibration_error(y[test], model.predict_proba(x[test])[:, 1])
    calibrator = calibrate(model, x[cal], y[cal])
    after = expected_calibration_error(y[test], calibrator.predict_proba(x[test])[:, 1])

    assert before > 0.10, "the setup did not actually produce a miscalibrated model"
    assert after < before / 3


def test_calibration_preserves_the_direction_of_the_score() -> None:
    """A calibrator that inverts a score is worse than none, and does not raise.

    This is not hypothetical: sklearn picks the response column by estimator
    type, so an estimator sklearn does not recognise as a classifier gets
    calibrated against the probability of the wrong class and comes back
    negatively correlated with itself. See the note in src/models/baselines.py.
    """
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(1)
    n = 6000
    x = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-(-2 + 1.5 * x["a"])))).astype(int)
    fit, cal, test = slice(0, 3000), slice(3000, 4500), slice(4500, n)

    model = LogisticRegression().fit(x[fit], y[fit])
    calibrator = calibrate(model, x[cal], y[cal])
    raw = model.predict_proba(x[test])[:, 1]
    calibrated = calibrator.predict_proba(x[test])[:, 1]
    assert np.corrcoef(raw, calibrated)[0, 1] > 0.9


def test_sklearn_recognises_every_estimator_as_a_classifier() -> None:
    """Silent failure otherwise: see the base-class note in src/models/baselines.py."""
    from sklearn.base import is_classifier

    for model in (
        BaseRateBaseline(),
        PayerRuleBaseline(),
        logistic_baseline(TOY_FEATURES, CONFIG),
        gradient_boosted_model(TOY_FEATURES, CONFIG),
    ):
        assert is_classifier(model), f"{type(model).__name__} is not seen as a classifier"


def test_the_calibration_method_comes_from_config_and_platt_is_a_synonym() -> None:
    assert method_from_config({"calibration": "isotonic"}) == "isotonic"
    assert method_from_config({"calibration": "platt"}) == "sigmoid"
    with pytest.raises(ValueError, match="unknown calibration method"):
        method_from_config({"calibration": "vibes"})


# --- explanations -----------------------------------------------------------


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("sim_payer_id_PAYER_C", "sim_payer_id"),
        ("sim_payer_prior_denial_rate", "sim_payer_prior_denial_rate"),
        ("missingindicator_sim_auth_decision_lead_days", "sim_auth_decision_lead_days"),
        ("drg_cd_infrequent_sklearn", "drg_cd"),
    ],
)
def test_encoded_columns_are_attributed_to_the_feature_that_produced_them(
    encoded: str, expected: str
) -> None:
    assert _owner_of(encoded, list(MODEL_A_FEATURES.names)) == expected


def test_a_shared_prefix_does_not_send_a_rate_to_the_wrong_owner() -> None:
    """`sim_payer_id` is a prefix of nothing here, but shortest-match would fail."""
    declared = ["sim_payer", "sim_payer_prior_denial_rate"]
    assert _owner_of("sim_payer_prior_denial_rate", declared) == "sim_payer_prior_denial_rate"


def test_every_declared_feature_has_an_analyst_action() -> None:
    """A driver with no action is a worklist item nobody can work."""
    assert unmapped_features(MODEL_A_FEATURES) == []


def test_reason_codes_are_unique_per_action() -> None:
    by_code: dict[str, set[str]] = {}
    for code, action in REASON_CODES.values():
        by_code.setdefault(code, set()).add(action)
    collisions = {code: actions for code, actions in by_code.items() if len(actions) > 1}
    assert not collisions, f"one code, two different actions: {collisions}"


# --- the honesty tripwire ---------------------------------------------------


def test_a_plausible_score_passes_the_tripwire() -> None:
    verdict, breaches = sanity_verdict(
        {"roc_auc": 0.6254, "pr_auc": 0.2210}, CONFIG["sanity_bounds"]
    )
    assert not breaches
    assert verdict["verdict"] == "within the documented noise ceiling"


@pytest.mark.parametrize(
    "headline",
    [
        {"roc_auc": 0.91, "pr_auc": 0.30},  # ranking far above the oracle
        {"roc_auc": 0.60, "pr_auc": 0.97},  # PR-AUC that cannot happen at a 12% base rate
    ],
)
def test_a_suspiciously_good_score_is_called_a_probable_leak(headline: dict) -> None:
    verdict, breaches = sanity_verdict(headline, CONFIG["sanity_bounds"])
    assert breaches
    assert "SUSPICIOUS" in verdict["verdict"]
    assert "leakage" in verdict["verdict"]


# The end-to-end half of the tripwire — that a breach actually STOPS the run and
# writes no metrics.json — needs the real feature store, so it lives in
# tests/models/test_train_postgres.py behind the integration marker.
