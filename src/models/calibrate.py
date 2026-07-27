"""Probability calibration on a held-out, strictly earlier fold.

A denial-risk score is only useful as a probability if 0.30 means "about three
in ten of these get denied". Ranking metrics say nothing about that: a model can
have a respectable ROC-AUC and still claim 0.60 for claims that are denied 20% of
the time, which would wreck any threshold chosen from a cost matrix and every
dollar figure computed downstream.

Two rules make the calibration honest here.

**Held out.** The calibrator is fitted on rows the estimator never saw. Fitting
isotonic on the estimator's own training rows calibrates to the fit rather than
to behaviour, and produces a curve that looks perfect and generalises to nothing.
`src/features/splits.py: calibration_split` carves that fold off the END of the
training window, so it is unseen by the estimator and still strictly earlier than
the test fold — the calibrator never touches the forward period either.

**Isotonic, per `config/model.yaml: calibration`.** Isotonic is non-parametric
and only assumes monotonicity, which is what we want when the miscalibration
comes from class imbalance and tree averaging rather than from a logistic shape.
Its cost is that it is a step function fitted on ~3,300 rows, so it can be jumpy
in the sparse high-risk tail; Platt is available through the same entry point for
comparison, and both are reported.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

Method = Literal["isotonic", "sigmoid"]

_ALIASES: dict[str, Method] = {"isotonic": "isotonic", "platt": "sigmoid", "sigmoid": "sigmoid"}


def method_from_config(config: dict) -> Method:
    """`calibration:` in config/model.yaml, with 'platt' accepted as a synonym."""
    raw = str(config.get("calibration", "isotonic")).lower()
    if raw not in _ALIASES:
        raise ValueError(f"unknown calibration method {raw!r}; expected one of {sorted(_ALIASES)}")
    return _ALIASES[raw]


def calibrate(
    fitted_estimator: BaseEstimator,
    x_calibration: pd.DataFrame,
    y_calibration: np.ndarray,
    method: Method = "isotonic",
) -> CalibratedClassifierCV:
    """Wrap an already-fitted estimator in a calibrator fitted on held-out rows.

    `FrozenEstimator` is what guarantees the wrapped model is not refitted: the
    calibrator sees its predictions on the calibration fold and fits only the
    mapping. If it refitted, the calibration fold would silently become training
    data and the whole arrangement would be pointless.
    """
    if len(x_calibration) == 0:
        raise ValueError("empty calibration fold")
    calibrator = CalibratedClassifierCV(FrozenEstimator(fitted_estimator), method=method)
    calibrator.fit(x_calibration, np.asarray(y_calibration))
    return calibrator
