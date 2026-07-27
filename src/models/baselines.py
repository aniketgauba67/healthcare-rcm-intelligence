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
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.validation import check_is_fitted


class BaseRateBaseline(BaseEstimator, ClassifierMixin):
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


class PayerRuleBaseline(BaseEstimator, ClassifierMixin):
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
