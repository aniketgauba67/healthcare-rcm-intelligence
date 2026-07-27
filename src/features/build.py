"""The Model A feature store: point-in-time facts as of the moment before submission.

Naming follows CLAUDE.md §3.2 rather than modelling convention: a feature
computed from simulated inputs keeps the `sim_` prefix, even after engineering,
so that a matrix, a SHAP plot or a dashboard column carries its provenance with
it. `billed_charge_amt` and `patient_age_years` come from the CMS claim and do
not carry the prefix. That distinction is the point of the project, and losing
it inside the model layer would be the easiest place to lose it.

Every column is declared in `MODEL_A_FEATURES` with its source columns, and
`build_model_a_frame` refuses to return a frame that does not match the
declaration. Adding a feature therefore means declaring where it came from.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from src.features.extract import fetch_claims
from src.features.historical import add_prior_period_rates
from src.features.leakage import assert_no_forbidden_columns, assert_sources_safe, load_model_config
from src.features.spec import FeatureSet, FeatureSpec, assert_frame_matches, validate_feature_set

LABEL = "sim_denial_flag"
TIME_COLUMN = "sim_submission_date"

# Carried for splitting, slicing and joining. Dropped before fitting; they are
# not features and the model never sees them.
PASSTHROUGH = (
    "claim_sk",
    "sim_submission_date",
    "prvdr_num",
    "sim_denial_flag",
)

_DIRECT_BOOLEANS: tuple[tuple[str, str], ...] = (
    ("sim_auth_required", "Payer requires prior authorization for this service."),
    ("sim_auth_obtained", "Authorization was obtained."),
    ("sim_auth_missing", "Authorization required and not obtained."),
    ("sim_auth_obtained_late", "Authorization obtained after the service date."),
    ("sim_eligibility_checked", "Eligibility was verified before submission."),
    ("sim_eligibility_failed", "Eligibility verification came back negative."),
    ("sim_secondary_payer_present", "A secondary payer is on the account."),
    ("sim_documentation_complete", "Clinical documentation package is complete."),
    ("sim_coder_query_outstanding", "An unanswered coder query is open on the chart."),
    ("sim_coding_specificity_deficit", "Coding lacks the specificity the payer expects."),
    ("sim_duplicate_submission_flag", "Claim looks like a duplicate of an earlier submission."),
    ("sim_late_filing_flag", "Submission is past the payer's contractual filing limit."),
)

_DIRECT_NUMERICS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("sim_documentation_score", ("sim_documentation_score",), "Documentation quality, 0-1."),
    ("sim_coding_complexity_score", ("sim_coding_complexity_score",), "Coding complexity, 0-1."),
    (
        "sim_days_service_to_submission",
        ("sim_days_service_to_submission",),
        "Days from service to submission.",
    ),
    (
        "sim_filing_limit_days",
        ("sim_filing_limit_days",),
        "The payer's contractual filing limit.",
    ),
    ("billed_charge_amt", ("clm_tot_chrg_amt",), "Total charge billed on the CMS claim."),
    ("length_of_stay_days", ("length_of_stay_days",), "Inpatient length of stay."),
    ("clm_utlztn_day_cnt", ("clm_utlztn_day_cnt",), "Covered utilization days on the claim."),
    ("diagnosis_count", ("fact_claim_diagnosis",), "Diagnoses coded on the claim."),
)


def _spec_list() -> tuple[FeatureSpec, ...]:
    specs: list[FeatureSpec] = []
    for name, description in _DIRECT_BOOLEANS:
        specs.append(
            FeatureSpec(name=name, kind="boolean", description=description, sources=(name,))
        )
    for name, sources, description in _DIRECT_NUMERICS:
        specs.append(
            FeatureSpec(name=name, kind="numeric", description=description, sources=sources)
        )
    specs.extend(
        [
            FeatureSpec(
                name="log_billed_charge_amt",
                kind="numeric",
                description="log1p of the billed charge; the charge distribution is heavy-tailed "
                "(median ~1.1k, max ~600k) and a linear model needs the compressed scale.",
                sources=("clm_tot_chrg_amt",),
            ),
            FeatureSpec(
                name="patient_age_years",
                kind="numeric",
                description="Beneficiary age at admission. Age is clinically meaningful in a "
                "Medicare population; sex and race are deliberately excluded (model card).",
                sources=("birth_date", "admission_date"),
            ),
            FeatureSpec(
                name="sim_filing_headroom_days",
                kind="numeric",
                description="Days of filing limit still unused at submission. Negative means the "
                "claim went out late.",
                sources=("sim_filing_limit_days", "sim_days_service_to_submission"),
            ),
            FeatureSpec(
                name="sim_filing_use_ratio",
                kind="numeric",
                description="Share of the filing window consumed before submission.",
                sources=("sim_filing_limit_days", "sim_days_service_to_submission"),
            ),
            FeatureSpec(
                name="sim_coding_lag_days",
                kind="numeric",
                description="Days from coding complete to submission — billing-office backlog.",
                sources=("sim_coded_date", "sim_submission_date"),
            ),
            FeatureSpec(
                name="sim_auth_decision_lead_days",
                kind="numeric",
                description="Days between the authorization decision and submission. Null where "
                "no authorization was sought.",
                sources=("sim_auth_decision_date", "sim_submission_date"),
            ),
            FeatureSpec(
                name="sim_pre_submission_touch_minutes",
                kind="numeric",
                description="Staff minutes logged on the claim up to and including the "
                "CLAIM_SUBMITTED event. Aggregated only over the filtered pre-submission "
                "rows — over the full history this counts denial rework and encodes the label.",
                sources=("sim_workflow_events.sim_touch_minutes",),
            ),
            FeatureSpec(
                name="sim_coding_to_submission_hours",
                kind="numeric",
                description="Hours from the first pre-submission workflow event to submission.",
                sources=("sim_workflow_events.sim_event_ts",),
            ),
            FeatureSpec(
                name="sim_payer_id",
                kind="categorical",
                description="Simulated payer. The strongest legitimate signal in the data.",
                sources=("sim_payer_id",),
            ),
            FeatureSpec(
                name="sim_service_line_id",
                kind="categorical",
                description="Simulated service line, derived from the DRG range.",
                sources=("sim_service_line_id",),
            ),
            FeatureSpec(
                name="drg_cd",
                kind="categorical",
                description="MS-DRG on the CMS claim. 168 levels, heavily concentrated; rare "
                "levels are grouped by the encoder, which is fitted on the training fold only.",
                sources=("drg_cd",),
            ),
            FeatureSpec(
                name="provider_state_cd",
                kind="categorical",
                description="Synthetic provider's state. Not the crosswalked real facility state.",
                sources=("provider_state_cd",),
            ),
            FeatureSpec(
                name="sim_submission_month",
                kind="categorical",
                description="Calendar month of submission, for seasonality. Deliberately month "
                "and not year: a year index cannot extrapolate to the forward test fold.",
                sources=("sim_submission_date",),
            ),
            FeatureSpec(
                name="overall_prior_denial_rate",
                kind="numeric",
                description="Book-wide denial rate over the embargoed prior window — the moving "
                "base rate every other historical rate shrinks toward.",
                sources=("sim_submission_date",),
                point_in_time="prior_period",
                prior_period_sources=("sim_denial_flag",),
            ),
        ]
    )
    for prefix, entity, note in (
        (
            "sim_payer",
            "sim_payer_id",
            "Payer denial propensity. Payer is a strong, stable stratifier here.",
        ),
        (
            "sim_provider",
            "prvdr_num",
            "Provider denial rate — the inverse of the clean-claim rate a denials manager "
            "tracks. Keyed on the synthetic prvdr_num, never the crosswalked real CCN. "
            "Coverage is claim-weighted, not provider-weighted: the median provider has "
            "two claims and never builds a usable rate, but volume is concentrated enough "
            "(top 10% of providers hold 53% of claims) that 72% of claims do have prior "
            "history. The companion count says how much to trust each one.",
        ),
        (
            "sim_service_line",
            "sim_service_line_id",
            "Service-line denial rate. Weak and noisy in this data; read with the count.",
        ),
    ):
        specs.append(
            FeatureSpec(
                name=f"{prefix}_prior_denial_rate",
                kind="numeric",
                description=note,
                sources=(entity, "sim_submission_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_denial_flag",),
            )
        )
        specs.append(
            FeatureSpec(
                name=f"{prefix}_prior_claims",
                kind="numeric",
                description=f"Claims behind {prefix}_prior_denial_rate. Zero means no history, "
                "and the rate is then just the book rate.",
                sources=(entity, "sim_submission_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_denial_flag",),
            )
        )
    return tuple(specs)


MODEL_A_FEATURES = FeatureSet(
    model="A",
    specs=_spec_list(),
    label=LABEL,
    time_column=TIME_COLUMN,
    passthrough=PASSTHROUGH,
)


def _engineer(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Derive the engineered columns from the extracted raw frame."""
    out = frame.copy()
    for column in (
        "sim_submission_date",
        "sim_coded_date",
        "sim_auth_decision_date",
        "birth_date",
        "admission_date",
    ):
        out[column] = pd.to_datetime(out[column])

    out["log_billed_charge_amt"] = np.log1p(out["billed_charge_amt"].astype(float))
    out["patient_age_years"] = ((out["admission_date"] - out["birth_date"]).dt.days / 365.25).round(
        2
    )
    out["sim_filing_headroom_days"] = (
        out["sim_filing_limit_days"] - out["sim_days_service_to_submission"]
    )
    out["sim_filing_use_ratio"] = out["sim_days_service_to_submission"] / out[
        "sim_filing_limit_days"
    ].replace(0, np.nan)
    out["sim_coding_lag_days"] = (out["sim_submission_date"] - out["sim_coded_date"]).dt.days
    out["sim_auth_decision_lead_days"] = (
        out["sim_submission_date"] - out["sim_auth_decision_date"]
    ).dt.days
    out["sim_submission_month"] = out["sim_submission_date"].dt.month.astype("string")
    out = out.rename(
        columns={
            "pre_submission_touch_minutes": "sim_pre_submission_touch_minutes",
            "coding_to_submission_hours": "sim_coding_to_submission_hours",
        }
    )

    prior = config["feature_store"]["prior_period"]
    out = add_prior_period_rates(
        out,
        date_column=TIME_COLUMN,
        outcome_column=LABEL,
        definitions=prior["entities"],
        embargo_days=int(prior["embargo_days"]),
        smoothing=float(prior["smoothing"]),
    )
    return out


def build_model_a_frame(engine: Engine, config: dict | None = None) -> pd.DataFrame:
    """The Model A feature store: features + passthrough + label, one row per claim.

    The label is present as a passthrough column so that the split, the slice
    metrics and the prior-period features all work off one frame. It is on the
    forbidden list, and `src/models/` separates it before fitting; the guard here
    runs over the feature columns only, which is what reaches the estimator.
    """
    cfg = config or load_model_config()
    validate_feature_set(MODEL_A_FEATURES, cfg)
    assert_sources_safe(
        {
            spec.name: spec.sources
            for spec in MODEL_A_FEATURES.specs
            if spec.point_in_time == "direct"
        },
        model="A",
        config=cfg,
    )

    raw = fetch_claims(engine)
    engineered = _engineer(raw, cfg)

    columns = [*MODEL_A_FEATURES.passthrough, *MODEL_A_FEATURES.names]
    frame = engineered.loc[:, columns].copy()
    assert_frame_matches(frame.columns, MODEL_A_FEATURES)
    assert_no_forbidden_columns(
        MODEL_A_FEATURES.names, model="A", config=cfg, context="Model A feature store"
    )
    return frame.sort_values([TIME_COLUMN, "claim_sk"]).reset_index(drop=True)


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Just the features — what the estimator is allowed to see."""
    return frame.loc[:, list(MODEL_A_FEATURES.names)]


def labels(frame: pd.DataFrame) -> pd.Series:
    return frame[LABEL].astype(int)
