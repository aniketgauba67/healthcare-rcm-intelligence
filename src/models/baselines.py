"""Baselines the advanced model has to beat to have earned its complexity.

Three of them, in increasing order of effort:

1. **Base rate.** Score every claim with the training base rate. ROC-AUC 0.5 by
   construction. It exists to anchor PR-AUC, which has no fixed floor — a PR-AUC
   of 0.13 sounds terrible until you notice the base rate is 0.128.
2. **Payer-only rule.** Score each claim with its payer's prior-period denial
   rate. This is the rule a denials manager already has in a spreadsheet, and it
   is the honest bar for "did the model add anything": beating a coin is easy,
   beating the thing already in use is the question.
3. **Regularized logistic.** A real model, just a linear one.

All three are fitted on the training fold only and expose the same
`predict_proba` interface as anything from sklearn, so the evaluation code cannot
accidentally treat them differently.

Note the base-class order `(ClassifierMixin, BaseEstimator)`. It is not
cosmetic: sklearn resolves an estimator's type through `__sklearn_tags__` along
the MRO, and with `BaseEstimator` first the mixin never gets to set the type, so
`sklearn.base.is_classifier` returns False for what is plainly a classifier.
Nothing raises. What happens instead is that sklearn's response-value machinery
stops treating `predict_proba` output as (negative, positive) and hands the
whole two-column array to whatever asked for a score — which is how a calibrator
wrapped around one of these ends up fitted on the probability of the wrong
class. `tests/models/test_pipeline.py` asserts the type for every estimator the
project exposes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from src.features.spec import FeatureSet
from src.models.preprocess import build_preprocessor


class BaseRateBaseline(ClassifierMixin, BaseEstimator):
    """Predicts the training base rate for every claim."""

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> BaseRateBaseline:  # noqa: N803
        self.classes_ = np.array([0, 1])
        self.base_rate_ = float(np.mean(y))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        check_is_fitted(self, "base_rate_")
        p = np.full(len(X), self.base_rate_, dtype=float)
        return np.column_stack([1.0 - p, p])

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


class PayerRuleBaseline(ClassifierMixin, BaseEstimator):
    """Scores each claim with its payer's denial rate, learned on the training fold.

    Deliberately reads the payer's *observed training-fold* rate rather than the
    point-in-time `sim_payer_prior_denial_rate` feature: this is meant to be the
    spreadsheet rule, and the spreadsheet is refreshed periodically from history,
    not recomputed per claim. Unseen payers fall back to the base rate.
    """

    def __init__(self, payer_column: str = "sim_payer_id") -> None:
        self.payer_column = payer_column

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> PayerRuleBaseline:  # noqa: N803
        if self.payer_column not in X.columns:
            raise KeyError(f"{self.payer_column} is not in the feature matrix")
        self.classes_ = np.array([0, 1])
        frame = pd.DataFrame({"payer": X[self.payer_column].astype("string"), "y": np.asarray(y)})
        self.base_rate_ = float(frame["y"].mean())
        self.rates_ = frame.groupby("payer")["y"].mean().to_dict()
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        check_is_fitted(self, "rates_")
        payers = X[self.payer_column].astype("string")
        p = payers.map(self.rates_).astype(float).fillna(self.base_rate_).to_numpy()
        return np.column_stack([1.0 - p, p])

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def logistic_baseline(feature_set: FeatureSet, config: dict) -> Pipeline:
    """Regularized logistic regression on the full declared feature set.

    Wrapped with the preprocessor in one `Pipeline` so that `fit` cannot be
    called on the training fold with statistics learned anywhere else
    (CLAUDE.md §4.4). The estimator hyperparameters come from
    `config/model.yaml: estimators.logistic`; nothing here is tuned.
    """
    params = dict(config["estimators"]["logistic"])
    return Pipeline(
        [
            ("preprocess", build_preprocessor(feature_set)),
            (
                "estimator",
                LogisticRegression(
                    C=float(params["C"]),
                    max_iter=int(params["max_iter"]),
                    solver=str(params["solver"]),
                    random_state=int(config["seed"]),
                ),
            ),
        ]
    )
