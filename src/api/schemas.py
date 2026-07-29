"""Request and response schemas.

THE MODEL A REQUEST SCHEMA IS GENERATED FROM THE FEATURE DECLARATION
--------------------------------------------------------------------
`DenialScoreRequest` is built with `pydantic.create_model` from
`MODEL_A_FEATURES.specs`, so the API's contract IS the declared feature set — the
same declaration `src/features/leakage.py` enforces the boundary against. Hand-
listing thirty-nine fields would create a second place to be wrong, and the
failure would be silent in the worst direction: a field the model no longer uses
would keep being accepted, and a new one would be quietly ignored while the
service returned a confident number computed without it.

Every field is optional. That is not laxity — a caller scoring a claim before
submission genuinely does not know the historical rate features, which are
computed from the book rather than from the claim. Omitted features are imputed
by the pipeline's own fold-fitted imputers, and every response names exactly
which ones were imputed. A score built from four supplied features is a score of
the median training claim, and the response has to let the caller see that.

EVERY SCORING RESPONSE CARRIES A MODEL VERSION AND A TIMESTAMP
--------------------------------------------------------------
`model_version` is a hash of the committed training matrix and `config/model.yaml`
— the two inputs the refit is a pure function of — so two services reporting the
same version really are serving the same model. `scored_at` is when the score was
computed and `model_fitted_at` is when this process fitted the model; they differ,
and conflating them would hide a service that has been up for a week on a model
whose inputs changed on day two.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from src.api.provenance import API_VERSION

# ---------------------------------------------------------------------------
# Shared envelope
# ---------------------------------------------------------------------------


class DataSourceInfo(BaseModel):
    """Where this process is reading from, and what state it was in."""

    kind: Literal["bundle", "postgres"]
    path: str
    git_commit: str = Field(description="Commit the demo bundle was built from.")
    git_tree_dirty: bool = False
    built_at_utc: str = ""
    source_vintages: dict[str, str] = Field(default_factory=dict)


class ResponseMeta(BaseModel):
    """Provenance metadata attached to every response.

    `contains_simulated` is the hard requirement: any payload carrying `sim_`
    fields, or built from a dataset declared as simulated, says so here. It is
    measured from the payload rather than asserted by the endpoint, so an
    endpoint that grows a simulated field later gets the flag without anyone
    remembering to set it.
    """

    contains_simulated: bool
    simulated_fields: list[str] = Field(default_factory=list)
    simulated_datasets: list[str] = Field(default_factory=list)
    notice: str = ""
    generated_at: datetime
    api_version: str = API_VERSION
    data_source: DataSourceInfo


class ErrorResponse(BaseModel):
    """A refusal that says what would fix it."""

    detail: str
    error: str
    hint: str = ""


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    name: str
    champion: str
    version: str
    fitted_at: datetime | None = None
    roc_auc_test_fold: float | None = None
    note: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    api_version: str = API_VERSION
    data_source: DataSourceInfo
    datasets_available: list[str]
    models: list[ModelInfo]
    contains_simulated: bool = Field(
        default=True,
        description=(
            "Constant true for this service: every outcome it can serve is simulated by "
            "this project's adjudication layer."
        ),
    )
    notice: str


# ---------------------------------------------------------------------------
# POST /score/denial — Model A
# ---------------------------------------------------------------------------


def _denial_request_model() -> type[BaseModel]:
    """Build the request model from the declared Model A feature set."""
    from src.features.build import MODEL_A_FEATURES

    python_types: dict[str, Any] = {"numeric": float, "boolean": bool, "categorical": str}
    fields: dict[str, Any] = {}
    for spec in MODEL_A_FEATURES.specs:
        annotation = python_types[spec.kind] | None
        fields[spec.name] = (
            annotation,
            Field(default=None, description=spec.description),
        )
    return create_model(
        "DenialScoreRequest",
        __config__=ConfigDict(
            extra="forbid",
            json_schema_extra={
                "description": (
                    "Pre-submission facts about one claim. Every field is optional; "
                    "omitted features are imputed from the training fold and named in "
                    "the response's `imputed_features`. Fields are generated from the "
                    "declared Model A feature set, so nothing knowable only AFTER "
                    "submission can appear here (CLAUDE.md §4)."
                )
            },
        ),
        **fields,
    )


DenialScoreRequest = _denial_request_model()


class ReasonCode(BaseModel):
    reason_code: str
    feature: str
    analyst_action: str


class DenialScoreResponse(BaseModel):
    """A pre-submission denial-risk score, with everything needed to read it."""

    sim_denial_risk: float = Field(
        description=(
            "Calibrated probability that this claim is DENIED. Simulated: the model was "
            "fitted on a simulated denial label, so this is not evidence about real payers."
        )
    )
    sim_denial_risk_uncalibrated: float
    risk_band: Literal["low", "moderate", "elevated", "high"]
    flagged_for_review: bool = Field(
        description=(
            "True when the score clears the capacity-constrained cut (top decile of the "
            "forward test fold). At that point precision is 26.3% — roughly three in four "
            "flagged claims would NOT have been denied."
        )
    )
    reason_codes: list[ReasonCode] = Field(
        default_factory=list,
        description=(
            "Actionable facts the caller reported, with the analyst action attached. "
            "NOT a SHAP attribution: ~33% of denials in this data are label noise with no "
            "mechanism, so a per-claim decomposition is not a causal account."
        ),
    )
    features_supplied: int
    features_expected: int
    imputed_features: list[str] = Field(
        description="Features not supplied, imputed from the training fold's medians/modes."
    )
    model_name: str = "Model A - pre-submission denial risk"
    model_champion: str
    model_version: str
    model_fitted_at: datetime
    scored_at: datetime
    limitations: list[str]
    meta: ResponseMeta


# ---------------------------------------------------------------------------
# POST /score/appeal — Model C economics
# ---------------------------------------------------------------------------


class AppealScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_denied_amount: float = Field(
        gt=0, description="Denied dollars on the remittance advice. SIMULATED."
    )
    sim_denial_category: str = Field(
        description="Denial category as posted, e.g. MEDICAL_NECESSITY. SIMULATED."
    )
    sim_denial_review_date: date = Field(
        description="Date the denial posted. The appeal clock starts here, not today."
    )
    claim_sk: int | None = Field(
        default=None,
        description=(
            "Warehouse claim key. When supplied and present in the shipped Model C run, "
            "the champion's own overturn probability is used instead of the category rule."
        ),
    )
    sim_p_overturn: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Override the probability. Reported as caller-supplied.",
    )
    as_of: date | None = Field(
        default=None, description="The moment the queue is being built. Defaults to today."
    )


class AppealScoreResponse(BaseModel):
    """Expected Net Recovery, the policy tier, and the action — with the caveat."""

    sim_p_overturn: float
    probability_source: str = Field(
        description=(
            "Which probability was used: the Model C champion's own score for a claim it "
            "ranked, the denial-category rule baseline, or a caller override."
        )
    )
    sim_recoverable_amt: float
    sim_expected_recovery_amt: float
    sim_expected_net_recovery: float
    sim_days_to_deadline: int
    tier: Literal["DEADLINE_CRITICAL", "MANDATORY_REVIEW", "VALUE", "BELOW_COST", "EXPIRED"]
    recommended_action: str
    recovery_ratio: float
    processing_cost_usd: float
    filing_window_days: int
    model_name: str = "Model C - appeal success and Expected Net Recovery"
    model_champion: str
    model_version: str
    scored_at: datetime
    limitations: list[str]
    meta: ResponseMeta


# ---------------------------------------------------------------------------
# GET /claims/{claim_id}
# ---------------------------------------------------------------------------


class ClaimResponse(BaseModel):
    """One enriched claim. Field names keep their `sim_` markers, deliberately."""

    model_config = ConfigDict(extra="allow")

    claim_sk: int
    clm_id: str
    prvdr_num: str | None = None
    claim: dict[str, Any] = Field(
        description=(
            "The claim as the curated view holds it. Column names are unchanged, so every "
            "simulated value still carries its `sim_` prefix at the wire."
        )
    )
    facility_display: dict[str, Any] = Field(
        description=(
            "Real facility identifiers reached this record through a seeded random "
            "crosswalk and are DISPLAY-ONLY: 4,876 synthetic providers map onto 2,857 real "
            "CCNs (worst case 8:1) and onto only 2,816 distinct display NAMES (worst case "
            "15:1). Never group on either; group on prvdr_num."
        )
    )
    sim_denial_risk: float | None = Field(
        default=None, description="Model A score for this claim, when the bundle carries one."
    )
    denial_risk_fold: str | None = Field(
        default=None,
        description=(
            "Which fold this claim was in. 'fit' and 'calibrate' scores are IN-SAMPLE; only "
            "'test' is a forward prediction."
        ),
    )
    meta: ResponseMeta


# ---------------------------------------------------------------------------
# GET /work-queue
# ---------------------------------------------------------------------------


class WorkQueueRow(BaseModel):
    model_config = ConfigDict(extra="allow")


class WorkQueueResponse(BaseModel):
    queue_mode: str
    ranking: str
    rows: list[dict[str, Any]]
    total_rows: int
    limit: int
    offset: int
    tier_counts: dict[str, int]
    ordering_caveat: str
    limitations: list[str]
    meta: ResponseMeta


# ---------------------------------------------------------------------------
# GET /metrics/executive
# ---------------------------------------------------------------------------


class ExecutiveMetricsResponse(BaseModel):
    period_from: str
    period_to: str
    claims_submitted: int
    denied_claims: int
    denial_rate: float
    clean_claim_rate: float
    first_pass_paid_rate: float
    sim_net_collection_rate: float
    source_billed_charge_amt: float
    sim_allowed_amt: float
    sim_paid_amt: float
    sim_denied_amt: float
    sim_cost_to_collect: float
    avg_days_to_payment: float
    claims_appealed: int
    appeal_overturn_rate: float
    monthly: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation: dict[str, Any] = Field(
        description=(
            "The SQL control totals this response must equal (CLAUDE.md §7). Returned "
            "with the figures rather than asserted elsewhere, so a caller can check them."
        )
    )
    meta: ResponseMeta
