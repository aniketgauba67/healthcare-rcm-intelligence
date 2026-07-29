"""SHAP explanations, expressed in the vocabulary an analyst can act on.

A denial-risk score that cannot say *why* is unusable in a revenue-cycle office:
the output of this model is not a number, it is a worklist item that a biller has
to do something about. So two things happen here that a plain `shap.summary_plot`
would not do.

**One-hot columns are folded back into the feature that produced them.** The
model sees `sim_payer_id_PAYER_C`, `drg_cd_infrequent_sklearn` and
`missingindicator_sim_auth_decision_lead_days`; the analyst sees "payer",
"DRG" and "no authorization decision on file". Global importance is therefore
summed over the encoded columns belonging to each *declared* feature, which is
also the only aggregation that lets a 168-level categorical be compared fairly
against a single numeric.

**Contributions are mapped to reason codes and actions.** `REASON_CODES` below
is the join between a feature name and what a person does about it. A feature
with no entry gets reported as itself rather than silently dropped — an
unmapped driver is a gap in this table, not something to hide.

One honest limit, stated here because it belongs next to the code that produces
the explanations: roughly a third of the denials in this data carry no mechanism
signal at all and exist only because the label is deliberately noisy
(docs/assumptions.md). SHAP will still produce a decomposition for those claims,
because it always does — it explains the model, not the world. Do not read a
waterfall as a claim-level causal account.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import shap
from sklearn.compose import ColumnTransformer

from src.features.spec import FeatureSet

# Feature -> (reason code, what the analyst does about it). Codes are local to
# this project's worklist, not CARC codes: CARC describes why a payer denied a
# claim after the fact, and these describe what to fix before it goes out.
REASON_CODES: dict[str, tuple[str, str]] = {
    "sim_auth_missing": ("AUTH-01", "Obtain prior authorization before submitting."),
    "sim_auth_required": (
        "AUTH-02",
        "Confirm the payer's authorization requirement for this service.",
    ),
    "sim_auth_obtained_late": (
        "AUTH-03",
        "Authorization post-dates the service; attach the retro-auth.",
    ),
    "sim_auth_obtained": ("AUTH-04", "Authorization on file; verify it covers the billed service."),
    "sim_auth_decision_lead_days": (
        "AUTH-05",
        "Check the authorization decision date against the service.",
    ),
    "sim_eligibility_failed": (
        "ELIG-01",
        "Re-verify coverage and correct the payer/plan on the claim.",
    ),
    "sim_eligibility_checked": ("ELIG-02", "Run an eligibility check before submitting."),
    "sim_secondary_payer_present": (
        "ELIG-03",
        "Confirm coordination of benefits and primary/secondary order.",
    ),
    "sim_documentation_complete": ("DOC-01", "Complete the clinical documentation package."),
    "sim_documentation_score": (
        "DOC-02",
        "Documentation is thin for this service; request the missing notes.",
    ),
    "sim_coder_query_outstanding": ("COD-01", "Resolve the open coder query before release."),
    "sim_coding_specificity_deficit": ("COD-02", "Code to the specificity the payer expects."),
    "sim_coding_complexity_score": (
        "COD-03",
        "Complex coding; route for a second-level coding review.",
    ),
    "sim_duplicate_submission_flag": (
        "DUP-01",
        "Check for an earlier submission of the same encounter.",
    ),
    "sim_late_filing_flag": ("TFL-01", "Past the filing limit; escalate before submitting."),
    "sim_filing_headroom_days": ("TFL-02", "Filing window is nearly consumed; submit now."),
    "sim_filing_use_ratio": ("TFL-02", "Filing window is nearly consumed; submit now."),
    "sim_days_service_to_submission": (
        "TFL-03",
        "Long service-to-submission lag; check the billing hold.",
    ),
    "sim_filing_limit_days": ("TFL-04", "Short contractual filing limit on this payer."),
    "sim_coding_lag_days": ("WFL-01", "Coding-to-submission backlog on this claim."),
    "sim_pre_submission_touch_minutes": (
        "WFL-02",
        "Unusual pre-submission handling effort; review the account.",
    ),
    "sim_coding_to_submission_hours": (
        "WFL-03",
        "Long pre-submission cycle time; check for a work queue hold.",
    ),
    "sim_payer_id": ("PAY-01", "Payer-specific risk; apply that payer's edit checklist."),
    "sim_payer_prior_denial_rate": (
        "PAY-02",
        "This payer denies at an elevated rate historically.",
    ),
    "sim_payer_prior_claims": (
        "PAY-03",
        "Limited history with this payer; treat the rate as uncertain.",
    ),
    "sim_provider_prior_denial_rate": (
        "PRV-01",
        "Provider's recent denial rate is elevated; coach the front end.",
    ),
    "sim_provider_prior_claims": (
        "PRV-02",
        "Little history for this provider; treat the rate as uncertain.",
    ),
    "sim_service_line_id": ("SVC-01", "Service-line risk; apply the line's pre-bill edits."),
    "sim_service_line_prior_denial_rate": (
        "SVC-02",
        "Service line denies at an elevated rate (weak signal here).",
    ),
    "sim_service_line_prior_claims": (
        "SVC-03",
        "Thin service-line history; treat the rate as uncertain.",
    ),
    "sim_overall_prior_denial_rate": (
        "BOOK-01",
        "Book-wide denial rate at the time of submission.",
    ),
    "billed_charge_amt": ("CHG-01", "High-dollar claim; route for pre-bill review."),
    "log_billed_charge_amt": ("CHG-01", "High-dollar claim; route for pre-bill review."),
    "drg_cd": ("DRG-01", "DRG-specific risk; check DRG-to-documentation alignment."),
    "length_of_stay_days": ("LOS-01", "Length of stay is atypical for this DRG; verify the stay."),
    "diagnosis_count": ("DX-01", "Diagnosis count is atypical; confirm the coded diagnoses."),
    "patient_age_years": ("DEM-01", "Age-related coverage rule; verify benefit applicability."),
    "provider_state_cd": ("GEO-01", "Provider state; check state-specific payer policy."),
    "sim_submission_month": ("SEA-01", "Seasonal submission effect; no claim-level action."),
}


@dataclass(frozen=True)
class ShapResult:
    """Everything one SHAP pass produces, in analyst-facing terms."""

    global_importance: pd.DataFrame  # one row per declared feature
    encoded_importance: pd.DataFrame  # one row per encoded column, for debugging
    values: np.ndarray  # (n_rows, n_encoded) raw SHAP values
    encoded_names: list[str]
    base_value: float
    row_index: pd.Index  # index of the rows explained, back into the frame


def _owner_of(encoded_name: str, declared: list[str]) -> str:
    """Map an encoded column back to the declared feature that produced it.

    Longest match wins: `sim_payer_prior_denial_rate` and `sim_payer_id` share a
    prefix, and a shortest-match rule would file the first under the second and
    quietly overstate the payer identity's importance.
    """
    name = encoded_name
    if name.startswith("missingindicator_"):
        name = name[len("missingindicator_") :]
    candidates = [d for d in declared if name == d or name.startswith(f"{d}_")]
    if not candidates:
        return encoded_name
    return max(candidates, key=len)


def explain_tree_model(
    preprocessor: ColumnTransformer,
    tree_estimator,  # noqa: ANN001 - any fitted XGBoost/sklearn tree model
    frame: pd.DataFrame,
    feature_set: FeatureSet,
    max_rows: int | None = 2000,
    seed: int = 1337,
) -> ShapResult:
    """Exact tree SHAP values, aggregated back to declared features.

    `max_rows` subsamples for speed; the sample is drawn with the project seed so
    the importance table is reproducible. Interventional perturbation is not used
    — `TreeExplainer`'s default path-dependent attribution needs no background
    set and cannot leak a background row's outcome into an explanation.
    """
    from src.models.preprocess import prepare_matrix

    matrix = prepare_matrix(frame, feature_set)
    if max_rows is not None and len(matrix) > max_rows:
        rng = np.random.default_rng(seed)
        positions = np.sort(rng.choice(len(matrix), size=max_rows, replace=False))
        matrix = matrix.iloc[positions]
    encoded = preprocessor.transform(matrix)
    encoded_names = [str(n) for n in preprocessor.get_feature_names_out()]

    explainer = shap.TreeExplainer(tree_estimator)
    values = np.asarray(explainer.shap_values(encoded))
    if values.ndim == 3:  # some versions return (n, k, classes)
        values = values[..., -1]

    declared = list(feature_set.names)
    owners = [_owner_of(name, declared) for name in encoded_names]
    encoded_importance = pd.DataFrame(
        {
            "encoded_feature": encoded_names,
            "feature": owners,
            "mean_abs_shap": np.abs(values).mean(axis=0),
        }
    ).sort_values("mean_abs_shap", ascending=False, kind="stable")

    # `kind="stable"` on both sorts, deliberately. Features xgboost never split on
    # all carry mean_abs_shap exactly 0.0, and pandas' default quicksort orders
    # tied rows arbitrarily — so the published importance table could reorder its
    # zero-importance tail for reasons that have nothing to do with the model.
    # Observed: renaming one feature reshuffled two unrelated zero rows. A stable
    # sort pins ties to the declared feature order, so a diff in this table always
    # means an importance actually moved.
    grouped = (
        encoded_importance.groupby("feature", as_index=False, sort=False)["mean_abs_shap"]
        .sum()
        .sort_values("mean_abs_shap", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    grouped["share"] = grouped["mean_abs_shap"] / grouped["mean_abs_shap"].sum()
    grouped["reason_code"] = [REASON_CODES.get(f, ("UNMAPPED", ""))[0] for f in grouped["feature"]]
    grouped["analyst_action"] = [
        REASON_CODES.get(f, ("", "-- no mapped action --"))[1] for f in grouped["feature"]
    ]

    base = explainer.expected_value
    base_value = float(np.ravel(base)[-1]) if np.ndim(base) else float(base)
    return ShapResult(
        global_importance=grouped,
        encoded_importance=encoded_importance.reset_index(drop=True),
        values=values,
        encoded_names=encoded_names,
        base_value=base_value,
        row_index=matrix.index,
    )


def claim_waterfall(
    result: ShapResult, position: int, feature_set: FeatureSet, top_n: int = 8
) -> pd.DataFrame:
    """The top drivers for one claim, as reason codes and actions.

    Contributions are summed within a declared feature first, so a one-hot payer
    appears once with its net effect rather than as forty near-zero rows.
    """
    declared = list(feature_set.names)
    owners = [_owner_of(name, declared) for name in result.encoded_names]
    contributions = pd.DataFrame({"feature": owners, "shap": result.values[position]})
    rolled = (
        contributions.groupby("feature", as_index=False)["shap"]
        .sum()
        .assign(abs_shap=lambda d: d["shap"].abs())
        .sort_values("abs_shap", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    rolled["direction"] = np.where(rolled["shap"] > 0, "raises risk", "lowers risk")
    rolled["reason_code"] = [REASON_CODES.get(f, ("UNMAPPED", ""))[0] for f in rolled["feature"]]
    rolled["analyst_action"] = [
        REASON_CODES.get(f, ("", "-- no mapped action --"))[1] for f in rolled["feature"]
    ]
    return rolled.drop(columns=["abs_shap"])


def unmapped_features(feature_set: FeatureSet) -> list[str]:
    """Declared features with no reason code. A gap in the table, not in the model."""
    return sorted(set(feature_set.names) - set(REASON_CODES))
