"""The scoring engine behind `POST /score/denial` and `POST /score/appeal`.

MODEL A IS REFITTED AT STARTUP, NOT DESERIALIZED
------------------------------------------------
`artifacts/features/model_a_training_matrix.parquet` is committed, so the
champion — regularized logistic on the fit fold, isotonic calibration on the
calibrate fold — is rebuilt from it when the service starts. The fit takes about
0.2 seconds and reproduces `docs/model_card.md` exactly (ROC-AUC 0.6254
uncalibrated, 0.6185 calibrated on the forward test fold), which is asserted
rather than hoped for. Three things follow, and each is why this is not a pickle:

* it needs no warehouse, so the container starts on a clean clone;
* it ships no serialized estimator whose validity silently depends on the
  reader's scikit-learn version matching ours;
* the served model is auditable — the artifact it comes from is in git, and
  `model_version` below is a hash of that artifact and the config, so two
  services claiming the same version really are the same model.

THERE IS NO MODEL C ESTIMATOR HERE, AND THAT IS A DECISION
-----------------------------------------------------------
Model C is fitted on warehouse data and has no committed matrix, so it cannot be
rebuilt the way Model A can. It would have been easy to export one into the demo
bundle — and that is exactly what `[LOG-SIM-DENIED]` is open about, because
`log_sim_denied_amount` is a Model C feature and committing a Model C matrix is
one of that item's two named revisit triggers. Not this agent's ruling to make,
so the matrix is not exported and this module holds no appeal estimator.

What `/score/appeal` serves instead is the honest remainder, and the model card
is what makes it honest rather than a workaround:

* for a claim the shipped Model C run already scored, the champion's own
  `sim_p_overturn` is returned, by lookup;
* for anything else, the **denial-category prior overturn rate** — the
  `category_rule` baseline that Model C does not beat (xgboost − category rule
  = −0.0356 [−0.1325, +0.0597], spanning zero).

Every response states which of the two it used in `probability_source`. Serving
a rule and calling it a model would be the dishonest version of this; serving a
rule, saying so, and citing the interval that says the model is not
distinguishable from it, is the accurate one.

The Expected Net Recovery, the tier and the recommended action are computed by
`src/models/work_queue.py` — the shipped implementation, not a reimplementation —
and they are deterministic given the inputs. That part of Model C is a policy,
and a policy is exactly reproducible.
"""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.api.provenance import utc_now

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG_PATH = REPO_ROOT / "config" / "model.yaml"
COMMITTED_MATRIX = REPO_ROOT / "artifacts" / "features" / "model_a_training_matrix.parquet"
COMMITTED_MATRIX_MANIFEST = COMMITTED_MATRIX.with_suffix(".json")

MODEL_A_CHAMPION = "logistic + isotonic"
MODEL_C_CHAMPION = "xgboost + isotonic"

#: The published figures the startup refit must reproduce (docs/model_card.md §1).
MODEL_CARD_ROC_UNCALIBRATED = 0.6254
MODEL_CARD_ROC_CALIBRATED = 0.6185
_ROC_TOLERANCE = 5e-5

PROBABILITY_FROM_MODEL_C = "model_c_champion_xgboost_isotonic"
PROBABILITY_FROM_CATEGORY_RULE = "denial_category_prior_overturn_rate (category_rule baseline)"
PROBABILITY_FROM_CALLER = "supplied_by_caller"

CATEGORY_RULE_NOTE = (
    "Model C is not distinguishable from this rule on the shipped test fold: "
    "xgboost - category_rule ROC-AUC = -0.0356 [-0.1325, +0.0597], an interval spanning "
    "zero. The rule is therefore a defensible default rather than a degraded substitute."
)

ORDERING_NOTE = (
    "Expected Net Recovery ships for the CUTOFF (which claims clear the cost of working "
    "them) and for the policy TIERING, not for the ordering. At 10% capacity, ranking by "
    "largest denial first captured 65.7% of recoverable dollars against 61.0% for this "
    "score and 59.8% for the tiered queue. The probability does not earn its place in the "
    "ordering."
)


class ScoringUnavailableError(RuntimeError):
    """Raised when a score cannot be produced, with what would fix it."""


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


def _digest(*parts: bytes) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part)
    return hasher.hexdigest()[:12]


@functools.lru_cache(maxsize=1)
def model_a_version() -> str:
    """A version that changes when, and only when, the served model changes.

    Composed from the committed matrix's own manifest and from `config/model.yaml`
    — the two inputs the refit is a pure function of. A wall-clock timestamp or a
    hand-bumped number would let two services report the same version while
    serving different models, which is the failure a version string exists to
    prevent.
    """
    matrix_bytes = (
        COMMITTED_MATRIX_MANIFEST.read_bytes()
        if COMMITTED_MATRIX_MANIFEST.is_file()
        else COMMITTED_MATRIX.read_bytes()
    )
    return f"model_a-{MODEL_A_CHAMPION.replace(' + ', '-').replace(' ', '')}-{_digest(matrix_bytes, MODEL_CONFIG_PATH.read_bytes())}"


# ---------------------------------------------------------------------------
# Model A
# ---------------------------------------------------------------------------


@dataclass
class DenialRiskModel:
    """The fitted Model A champion plus everything a response must disclose."""

    pipeline: Any
    calibrated: Any
    feature_names: tuple[str, ...]
    numeric_features: tuple[str, ...]
    fit_medians: dict[str, float]
    top_decile_threshold: float
    version: str
    fitted_at: datetime
    roc_auc_test: float
    roc_auc_test_calibrated: float
    test_rows: int
    cut_date: str

    def score(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(calibrated, uncalibrated) probabilities for a prepared frame."""
        calibrated = np.asarray(self.calibrated.predict_proba(frame))[:, 1]
        raw = np.asarray(self.pipeline.predict_proba(frame))[:, 1]
        return calibrated, raw


@functools.lru_cache(maxsize=1)
def load_denial_risk_model() -> DenialRiskModel:
    """Refit the champion from the committed matrix. Called once per process."""
    from sklearn.metrics import roc_auc_score

    from src.features.build import MODEL_A_FEATURES
    from src.features.splits import calibration_split, split_from_config
    from src.models.baselines import logistic_baseline
    from src.models.calibrate import calibrate, method_from_config
    from src.models.preprocess import prepare_matrix

    if not COMMITTED_MATRIX.is_file():
        raise ScoringUnavailableError(
            f"{COMMITTED_MATRIX.relative_to(REPO_ROOT)} is missing. It is committed on purpose; "
            "regenerate it with `make features` (needs the warehouse) or restore it from git."
        )

    config = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    feature_set = MODEL_A_FEATURES
    frame = pd.read_parquet(COMMITTED_MATRIX)
    frame[feature_set.time_column] = pd.to_datetime(frame[feature_set.time_column])

    split = split_from_config(frame, config)
    calibration = calibration_split(frame, split, config)
    fit_mask = calibration.train
    calibration_mask = calibration.test
    test_mask = split.test
    matrix = prepare_matrix(frame, feature_set)
    label = frame[feature_set.label].astype(int).to_numpy()

    pipeline = logistic_baseline(feature_set, config)
    pipeline.fit(matrix[fit_mask], label[fit_mask])
    calibrated = calibrate(
        pipeline,
        matrix[calibration_mask],
        label[calibration_mask],
        method_from_config(config),
    )

    raw_test = np.asarray(pipeline.predict_proba(matrix[test_mask]))[:, 1]
    cal_test = np.asarray(calibrated.predict_proba(matrix[test_mask]))[:, 1]
    roc_raw = float(roc_auc_score(label[test_mask], raw_test))
    roc_cal = float(roc_auc_score(label[test_mask], cal_test))
    _assert_reproduces_model_card(roc_raw, roc_cal)

    numeric = tuple(feature_set.by_kind("numeric"))
    medians = {
        name: float(matrix.loc[fit_mask, name].astype(float).median())
        for name in numeric
        if name in matrix.columns
    }

    return DenialRiskModel(
        pipeline=pipeline,
        calibrated=calibrated,
        feature_names=tuple(feature_set.names),
        numeric_features=numeric,
        fit_medians=medians,
        top_decile_threshold=float(np.quantile(cal_test, 0.90)),
        version=model_a_version(),
        fitted_at=utc_now(),
        roc_auc_test=round(roc_raw, 4),
        roc_auc_test_calibrated=round(roc_cal, 4),
        test_rows=int(test_mask.sum()),
        cut_date=str(split.cut_date.date()),
    )


def _assert_reproduces_model_card(roc_raw: float, roc_calibrated: float) -> None:
    """A served model that does not match the published card is not servable.

    Failing startup is the correct response. The alternative — logging a warning
    and serving anyway — puts a model in front of users whose only published
    evaluation describes a different model.
    """
    drift = {
        name: (round(got, 6), expected)
        for name, got, expected in (
            ("uncalibrated", roc_raw, MODEL_CARD_ROC_UNCALIBRATED),
            ("calibrated", roc_calibrated, MODEL_CARD_ROC_CALIBRATED),
        )
        if abs(round(got, 4) - expected) > _ROC_TOLERANCE
    }
    if drift:
        raise ScoringUnavailableError(
            "the Model A refit does not reproduce docs/model_card.md: "
            + "; ".join(f"{k} ROC-AUC {got} vs published {exp}" for k, (got, exp) in drift.items())
            + ". The committed matrix, config/model.yaml or a library version has moved. "
            "Do not relax this check to start the service."
        )


def prepare_request_frame(
    features: dict[str, Any], model: DenialRiskModel
) -> tuple[pd.DataFrame, list[str]]:
    """One request into a one-row matrix, reporting what the caller left out.

    Omitted features are left as NaN and handled by the pipeline's own imputers,
    fitted on the training fold — the same treatment a missing value gets during
    training. What is NOT acceptable is doing that quietly, so the names of the
    imputed features are returned and the endpoint puts them in the response. A
    score computed from four supplied features and thirty-five imputed ones is a
    score of the training-fold median claim, and the caller has to be able to see
    that.
    """
    from src.features.build import MODEL_A_FEATURES
    from src.models.preprocess import prepare_matrix

    supplied = {name: value for name, value in features.items() if value is not None}
    unknown = sorted(set(supplied) - set(model.feature_names))
    if unknown:
        raise ScoringUnavailableError(
            f"{unknown} are not Model A features. The declared feature set is in "
            "src/features/build.py; the request schema is generated from it."
        )

    row = {name: supplied.get(name, None) for name in model.feature_names}
    frame = pd.DataFrame([row])
    for name in MODEL_A_FEATURES.by_kind("numeric"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    for name in MODEL_A_FEATURES.by_kind("boolean"):
        frame[name] = frame[name].astype("boolean")
    for name in MODEL_A_FEATURES.by_kind("categorical"):
        frame[name] = frame[name].astype("object")

    imputed = sorted(name for name in model.feature_names if name not in supplied)
    return prepare_matrix(frame, MODEL_A_FEATURES), imputed


def top_reason_codes(features: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """Analyst reason codes for the risk-raising facts the caller actually sent.

    This is NOT a SHAP attribution, and the endpoint says so. Per-claim SHAP for
    an arbitrary request would need the tree model fitted and explained per call,
    and — more to the point — docs/model_card.md is explicit that ~33% of these
    denials are label noise with no mechanism, so a per-claim decomposition is not
    a causal account of anything. What is defensible is the reverse direction:
    naming the actionable facts the caller reported, with the action attached.
    """
    from src.models.explain import REASON_CODES

    raising = [
        ("sim_auth_missing", True),
        ("sim_auth_obtained_late", True),
        ("sim_eligibility_failed", True),
        ("sim_documentation_complete", False),
        ("sim_coder_query_outstanding", True),
        ("sim_coding_specificity_deficit", True),
        ("sim_duplicate_submission_flag", True),
        ("sim_late_filing_flag", True),
    ]
    hits: list[dict[str, str]] = []
    for name, risky_value in raising:
        value = features.get(name)
        if value is None or bool(value) is not risky_value:
            continue
        code, action = REASON_CODES.get(name, ("UNMAPPED", "-- no mapped action --"))
        hits.append({"reason_code": code, "feature": name, "analyst_action": action})
        if len(hits) >= limit:
            break
    return hits


# ---------------------------------------------------------------------------
# Model C — economics, tiering, and a probability that says where it came from
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def appeal_economics():  # noqa: ANN201 - AppealEconomics, imported lazily
    """The shipped appeal economics, from config/model.yaml.

    `recovery_ratio` is the value the Model C run measured on its fit fold
    (0.8156) and published; it is read out of the shipped metrics rather than
    re-estimated here, so the API and the model card agree on what an overturned
    appeal is worth.
    """
    from src.models.work_queue import AppealEconomics

    config = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    ratio = _published_recovery_ratio()
    return AppealEconomics.from_config(config, recovery_ratio=ratio)


def _published_recovery_ratio(default: float = 0.8156) -> float:
    from src.demo.source import get_source

    try:
        source = get_source()
        metrics = json.loads(
            source.frame("model_metrics").set_index("model").loc["C", "metrics_json"]
        )
        return float(metrics["economics"]["recovery_ratio_fit_fold"])
    except Exception:  # noqa: BLE001 - a missing bundle must not break scoring
        return default


def category_overturn_rates() -> dict[str, float]:
    """Prior overturn rate per denial category, from the shipped curated view.

    This is the `category_rule` baseline, read from `vw_denial_root_cause` — the
    same view the dashboard renders, so the number the API uses and the number a
    user sees on screen are the same number.
    """
    from src.demo.source import get_source

    frame = get_source().frame("vw_denial_root_cause")
    grouped = frame.groupby("sim_denial_category", as_index=False).agg(
        appealed=("claims_appealed", "sum"), overturned=("claims_overturned", "sum")
    )
    rates = {}
    for row in grouped.itertuples():
        if row.appealed and row.appealed > 0:
            rates[str(row.sim_denial_category)] = float(row.overturned) / float(row.appealed)
    return rates


def book_overturn_rate() -> float:
    rates = category_overturn_rates()
    return float(np.mean(list(rates.values()))) if rates else 0.0


def assert_queue_carries_markers(queue: pd.DataFrame) -> None:
    """Refuse to serve a queue whose simulated columns lost their `sim_` marker.

    `build_work_queue` is imported from `src/models/`, which this agent does not
    own, and on a tree that predates commit f0a1e12 it emits `recoverable_amt`
    where it should emit `sim_recoverable_amt`. Serving that would put an unmarked
    simulated dollar figure on the wire — the [QUEUE-PREFIX] defect at exactly the
    boundary it was closed for. A 503 that names the commit is the right answer;
    renaming the columns on the way out would make the endpoint look correct while
    leaving the source wrong, and nothing would ever show it.
    """
    from src.demo import spec

    stripped = spec.stripped_marker_columns(queue.columns)
    if stripped:
        raise ScoringUnavailableError(
            f"src/models/work_queue.py on this tree emits {stripped} without the `sim_` "
            "marker. " + spec.QUEUE_RENAME_ADVICE
        )


def shipped_queue_probability(claim_sk: int) -> float | None:
    """Model C's own score for a claim the shipped run already ranked."""
    from src.demo.source import get_source

    source = get_source()
    if "model_c_work_queue" not in source.available():
        return None
    queue = source.frame("model_c_work_queue")
    match = queue.loc[queue["claim_sk"] == int(claim_sk), "sim_p_overturn"]
    return float(match.iloc[0]) if len(match) else None
