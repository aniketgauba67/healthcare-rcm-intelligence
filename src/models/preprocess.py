"""Preprocessing, fitted on training folds only (docs/project_rules.md §4.4).

Everything that learns anything from the data — the imputer's medians, the
scaler's means, the encoder's category vocabulary — lives inside one sklearn
`Pipeline`. That is not a style preference: a median computed over the whole
frame before splitting is a small, invisible leak from the test period into the
training fold, and the only reliable way to avoid it is to make it structurally
impossible to call `fit` on anything but the training rows.

The same transformer feeds the logistic baseline and the gradient-boosted model.
Trees do not need the scaling and could handle the nulls natively, but giving
the two estimators different inputs would make "advanced beats baseline" a
statement about preprocessing rather than about the estimator, and the §7
definition of done asks for a comparison that means something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.features.spec import FeatureSet

# Levels rarer than this are folded into one "infrequent" bucket. drg_cd has 168
# levels over 20,867 claims with a heavy concentration in a handful of DRGs; the
# long tail would otherwise become hundreds of columns that fire a few times
# each and memorise individual claims.
MIN_CATEGORY_FREQUENCY = 30


def _numeric_pipeline() -> Pipeline:
    return Pipeline(
        [
            # add_indicator keeps missingness as signal rather than silently
            # imputing it away: an authorization decision date is null precisely
            # when no authorization was sought, which is a real fact about the
            # claim, not an absence of data.
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]
    )


def _categorical_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "encode",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    min_frequency=MIN_CATEGORY_FREQUENCY,
                    sparse_output=False,
                ),
            ),
        ]
    )


def build_preprocessor(feature_set: FeatureSet) -> ColumnTransformer:
    """A transformer for exactly the declared features, in declared order."""
    numeric = list(feature_set.by_kind("numeric"))
    boolean = list(feature_set.by_kind("boolean"))
    categorical = list(feature_set.by_kind("categorical"))
    if not (numeric or boolean or categorical):
        raise ValueError("feature set declares no features")

    return ColumnTransformer(
        [
            ("numeric", _numeric_pipeline(), numeric),
            # Booleans are already 0/1 and need no scaling, but they can be null
            # if an upstream join misses, so they still get an imputer.
            ("boolean", SimpleImputer(strategy="most_frequent"), boolean),
            ("categorical", _categorical_pipeline(), categorical),
        ],
        remainder="drop",  # anything undeclared is dropped, not passed through
        verbose_feature_names_out=False,
    )


def prepare_matrix(frame: pd.DataFrame, feature_set: FeatureSet) -> pd.DataFrame:
    """Select the declared features and coerce them to modelling dtypes."""
    matrix = frame.loc[:, list(feature_set.names)].copy()
    for name in feature_set.by_kind("boolean"):
        matrix[name] = matrix[name].astype("float64")
    for name in feature_set.by_kind("numeric"):
        matrix[name] = pd.to_numeric(matrix[name], errors="coerce").astype("float64")
    for name in feature_set.by_kind("categorical"):
        matrix[name] = matrix[name].astype("string").fillna("__missing__")
    return matrix


def transformed_feature_names(preprocessor: ColumnTransformer) -> list[str]:
    """Post-encoding column names, for SHAP and coefficient tables."""
    return [str(name) for name in preprocessor.get_feature_names_out()]


def median_fingerprint(preprocessor: ColumnTransformer) -> np.ndarray:
    """The imputer's learned medians — the thing a fold leak would change.

    Exposed so a test can assert that fitting on the training fold produces
    different statistics from fitting on the whole frame. If those two are
    identical, the split is not doing anything.
    """
    return np.asarray(preprocessor.named_transformers_["numeric"].named_steps["impute"].statistics_)
