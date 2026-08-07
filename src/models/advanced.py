"""The advanced model: gradient-boosted trees over the same features.

The point of this module is comparison, not victory. It is given the *same*
preprocessor and the *same* declared features as the logistic baseline, because
otherwise "advanced beats baseline" would be a statement about preprocessing.

Expect the comparison to come out close to even on this data, and read that as
the correct answer rather than a tuning failure. The strongest signal in the
generator is an authorization interaction that a linear model absorbs by
construction — you cannot miss an authorization that was never required, so
`sim_auth_missing` already *is* the `auth_required x auth_missing` interaction,
and there is nothing left for a tree to discover in it. The one genuinely
tree-shaped interaction available (payer x service line) is thin, because the
source DRG mix is concentrated. docs/project_rules.md §7 asks for the comparison to be
reported, not for the tree to win, and manufacturing a win by searching until
the tree came out ahead would be optimising impressive over honest (§1).
"""

from __future__ import annotations

from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from src.features.spec import FeatureSet
from src.models.preprocess import build_preprocessor


def gradient_boosted_model(feature_set: FeatureSet, config: dict) -> Pipeline:
    """XGBoost behind the shared preprocessor, parameters from config.

    `scale_pos_weight` is deliberately left at 1: the positive class is 12.9% of
    claims, which is imbalanced but not rare, and reweighting would distort the
    probabilities that the calibration step and the dollar-weighted threshold
    both depend on without improving the ranking.
    """
    params = dict(config["estimators"]["xgboost"])
    return Pipeline(
        [
            ("preprocess", build_preprocessor(feature_set)),
            (
                "estimator",
                XGBClassifier(
                    n_estimators=int(params["n_estimators"]),
                    max_depth=int(params["max_depth"]),
                    learning_rate=float(params["learning_rate"]),
                    subsample=float(params["subsample"]),
                    colsample_bytree=float(params["colsample_bytree"]),
                    min_child_weight=float(params["min_child_weight"]),
                    reg_lambda=float(params["reg_lambda"]),
                    n_jobs=int(params["n_jobs"]),
                    random_state=int(config["seed"]),
                    eval_metric="logloss",
                    tree_method="hist",
                ),
            ),
        ]
    )
