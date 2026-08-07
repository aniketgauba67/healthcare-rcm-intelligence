"""FastAPI scoring and reporting service.

    make api        # uv run uvicorn src.api.main:app --reload
    GET /docs       # OpenAPI, generated from the schemas in src/api/schemas.py

Six endpoints: `/health`, `POST /score/denial`, `POST /score/appeal`,
`GET /claims/{claim_id}`, `GET /work-queue`, `GET /metrics/executive`.

THE RULE THIS SERVICE IS BUILT AROUND
--------------------------------------
docs/project_rules.md §3 at the wire: **every response containing simulated values says so.**
The `meta` block on every payload carries `contains_simulated`, the list of
simulated fields, and the notice — and the flag is MEASURED from the payload and
from the declared provenance of the datasets it was built from, never asserted by
the endpoint author. An endpoint that grows a simulated field later gets the flag
without anyone remembering to add it.

ROLE-LIKE VIEWS
---------------
`?role=executive` (the default) serves aggregates; `?role=analyst` unlocks
claim-level detail — the work queue's per-claim rows, the claim record's full
column set. The split is enforced here rather than in the database because the
hosted demo has no database to enforce it in: the whole point of the bundled
extract is that it runs without one. The same two roles gate the dashboard's
pages, and `docs/` records the boundary. This is a demonstration of the shape, not
a security control; it is documented as such in the README and nothing here should
be read as authentication.

ERRORS
------
404 for a claim that does not exist, 422 for a request the schema rejects (FastAPI
does that), 503 when the configured data source or the model is unavailable, and
501 when a dataset only a demo bundle carries is asked of a live warehouse. Every
error body carries a `hint` naming the command that fixes it — a 503 that says
"service unavailable" and nothing else costs the reader the one thing the service
knows and they do not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import os
import pathlib
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from dashboard.disclosures import (
    API_SIMULATED_NOTICE,
    CROSSWALK_COLLISION,
    DOLLARS_AT_RISK,
    MODEL_A_HONESTY,
    MODEL_C_HONESTY,
    VINTAGE_SKEW,
)
from src.api import scoring, tables
from src.api.provenance import API_VERSION, response_meta, utc_now
from src.api.schemas import (
    AppealScoreRequest,
    AppealScoreResponse,
    ClaimResponse,
    DenialScoreRequest,
    DenialScoreResponse,
    ErrorResponse,
    ExecutiveMetricsResponse,
    HealthResponse,
    LivenessResponse,
    ModelInfo,
    WorkQueueResponse,
)
from src.demo.bundle import BundleNotFoundError, BundleReadinessError, validate_bundle_readiness
from src.demo.source import BUNDLE, DataSourceError, configured_kind, get_source
from src.infra.postgres_contract import PostgresContractError, validate_postgres_contract

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

Role = Literal["executive", "analyst"]

#: Readiness failures are logged here as well as returned, because a platform
#: health check keeps the status code and throws the body away.
_LOGGER = logging.getLogger("rcm.api")

MODEL_A_LIMITATIONS = [
    "Every outcome behind this score is SIMULATED. The score is not evidence about real claims.",
    "The champion is a regularized logistic regression at ROC-AUC 0.6254 on the forward test "
    "fold. XGBoost - logistic is +0.0003 [-0.0173, +0.0183]: no difference.",
    "~33% of the simulated denials are deliberate label noise with no mechanism behind them, "
    "so the achievable ceiling is about 0.68 and some denials are unexplainable by construction.",
    "At the capacity-constrained operating point precision is 26.3%: roughly three in four "
    "flagged claims would not have been denied. Flag for human review; do not hold a claim on it.",
    "Not a payment decision, not a clinical determination, and never a fraud signal.",
]

MODEL_C_LIMITATIONS = [
    "Every outcome behind this score is SIMULATED.",
    "Model C is a NEGATIVE result: xgboost - denial-category rule = -0.0356 [-0.1325, +0.0597], "
    "an interval spanning zero. No slice beats chance.",
    "At 10% capacity, ranking by largest denial first captured 65.7% of recoverable dollars "
    "against 61.0% for this score. Expected Net Recovery ships for the cutoff and the tiering, "
    "not for the ordering.",
    "The model was fitted on the 967 denials somebody chose to appeal and is applied to all "
    "2,663. TIMELY_FILING denials have zero training support and are still scored.",
    "The recovery ratio and processing cost are published benchmarks and project policy, not "
    "measurements on this book.",
]


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build the single scoring model before the process accepts traffic."""
    scoring.load_denial_risk_model()
    yield


app = FastAPI(
    title="Healthcare RCM Intelligence API",
    version=API_VERSION,
    description=(
        "Scoring and reporting over a PostgreSQL warehouse of official CMS **synthetic** "
        "Medicare claims with a **clearly labelled simulated** adjudication layer.\n\n"
        f"{API_SIMULATED_NOTICE}\n\n"
        "**Two disclosures worth reading before any number below.**\n\n"
        f"*Reference-file vintage skew.* {VINTAGE_SKEW}\n\n"
        f"*Crosswalk collision.* {CROSSWALK_COLLISION}\n\n"
        "**Model results.**\n\n"
        f"{MODEL_A_HONESTY}\n\n"
        f"{MODEL_C_HONESTY}\n\n"
        f"{DOLLARS_AT_RISK}"
    ),
    contact={"name": "Healthcare RCM Intelligence Platform"},
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _source():  # noqa: ANN202 - DataSource protocol
    try:
        return get_source()
    except (DataSourceError, BundleNotFoundError) as error:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": str(error),
                "error": "data_source_unavailable",
                "hint": (
                    "Build the demo bundle with `make demo-extract`, or set "
                    "RCM_DATA_SOURCE=postgres with POSTGRES_* configured in .env."
                ),
            },
        ) from error


def _frame(dataset: str) -> pd.DataFrame:
    source = _source()
    if dataset not in source.available():
        raise HTTPException(
            status_code=501,
            detail={
                "detail": (
                    f"{dataset!r} is not available from the {source.kind} data source. "
                    "Model datasets are produced by a training run and ship in the demo bundle."
                ),
                "error": "dataset_unavailable",
                "hint": "Run `make train && make train-appeal && make demo-extract`.",
            },
        )
    try:
        return source.frame(dataset)
    except DataSourceError as error:
        raise HTTPException(
            status_code=503,
            detail={"detail": str(error), "error": "data_source_unavailable", "hint": ""},
        ) from error


def _meta(payload: Any, datasets: tuple[str, ...] = ()) -> dict[str, Any]:
    return response_meta(payload, _source().describe(), datasets=datasets)


@app.exception_handler(scoring.ScoringUnavailableError)
async def _scoring_unavailable(request: Request, error: scoring.ScoringUnavailableError):  # noqa: ANN201, ARG001
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(error),
            "error": "model_unavailable",
            "hint": "`make features` rebuilds the committed training matrix the model refits from.",
        },
    )


# ---------------------------------------------------------------------------
# GET /live, /ready, /health
# ---------------------------------------------------------------------------


@app.get("/live")
def live() -> LivenessResponse:
    """Process liveness only; use `/ready` before sending application traffic."""
    return LivenessResponse(status="live")


def _postgres_readiness_required() -> bool:
    return os.environ.get("RCM_REQUIRE_POSTGRES_READY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _assert_required_dependencies() -> None:
    """Readiness gate on the warehouse contract, with the reason on the way out.

    THE REASON MUST REACH THE LOG. A platform health check reads the STATUS and
    discards the body, so the operator sees a wall of identical `503`s while the
    diagnosis sits unread in a response nobody keeps. Three deploy attempts were
    spent guessing at a cause this function already knew, so it is logged here
    as well as returned.

    DETERMINISTIC FAILURES ARE NOT WORTH RETRYING. A missing column will never
    resolve by asking again — the schema has to change — so a contract mismatch
    is logged once at ERROR with the offending relations named. A connection
    failure is genuinely transient (a sleeping database, a rotated credential
    not yet propagated) and stays at WARNING, because retrying is the right
    response to it. Both still return 503: fail-fast here means fail
    INFORMATIVELY, not fail differently — the platform decides when to stop.
    """
    if not _postgres_readiness_required():
        return
    try:
        validate_postgres_contract()
    except PostgresContractError as error:
        # The schema is reachable and WRONG. Retrying cannot fix it.
        _LOGGER.error(
            "readiness: postgres contract mismatch, not retryable without a schema change — %s",
            error,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": f"PostgreSQL readiness failed: {error}",
                "error": "postgres_contract_mismatch",
                "retryable": False,
                "hint": (
                    "The database is reachable but does not match the tracked DDL. Re-run "
                    "scripts/initialize_hosted_postgres.py --recreate against it; retrying "
                    "this endpoint will not change the result."
                ),
            },
        ) from error
    except Exception as error:
        # Could not reach or authenticate. Often transient; retrying is correct.
        _LOGGER.warning(
            "readiness: postgres unreachable (%s: %s) — treating as transient",
            type(error).__name__,
            error,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": f"PostgreSQL readiness failed: {type(error).__name__}: {error}",
                "error": "postgres_unreachable",
                "retryable": True,
                "hint": (
                    "Check DATABASE_URL and that the database is awake. A rotated credential "
                    "that has not been updated here presents exactly like this."
                ),
            },
        ) from error


def _assert_bundle_readiness() -> None:
    if configured_kind() != BUNDLE:
        return
    try:
        validate_bundle_readiness()
    except BundleReadinessError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": f"DuckDB bundle readiness failed: {error}",
                "error": "bundle_not_ready",
                "hint": (
                    "Restore the pinned committed demo bundle at RCM_DEMO_BUNDLE, then retry. "
                    "Readiness opens and validates the on-disk artifact independently of the "
                    "cached serving connection."
                ),
            },
        ) from error


@app.get("/ready", responses={503: {"model": ErrorResponse}})
@app.get("/health", responses={503: {"model": ErrorResponse}})
def health() -> HealthResponse:
    """Dependency readiness plus the provenance of everything this process serves.

    `/health` remains a compatibility alias for `/ready`. In Compose this checks
    the complete PostgreSQL object contract before opening and describing the
    committed DuckDB bundle. `/live` is the process-only probe.
    """
    _assert_required_dependencies()
    _assert_bundle_readiness()
    source = _source()
    available = sorted(source.available())

    models: list[ModelInfo] = []
    status: Literal["ok", "degraded"] = "ok"
    try:
        model_a = scoring.load_denial_risk_model()
        models.append(
            ModelInfo(
                name="Model A - pre-submission denial risk",
                champion=scoring.MODEL_A_CHAMPION,
                version=model_a.version,
                fitted_at=model_a.fitted_at,
                roc_auc_test_fold=model_a.roc_auc_test_calibrated,
                note=(
                    "Refitted at startup from the committed training matrix; reproduces "
                    "docs/model_card.md exactly or the service refuses to start."
                ),
            )
        )
    except scoring.ScoringUnavailableError as error:
        status = "degraded"
        models.append(
            ModelInfo(
                name="Model A - pre-submission denial risk",
                champion=scoring.MODEL_A_CHAMPION,
                version="unavailable",
                note=str(error),
            )
        )

    models.append(
        ModelInfo(
            name="Model C - appeal success and Expected Net Recovery",
            champion=scoring.MODEL_C_CHAMPION,
            version="shipped-run-lookup + category-rule fallback",
            note=(
                "No appeal estimator is fitted in this service. Scores come from the shipped "
                "Model C run for claims it ranked, and from the denial-category rule otherwise "
                "- a rule Model C does not beat. See /score/appeal."
            ),
        )
    )

    if "model_c_work_queue" not in available:
        status = "degraded"

    return HealthResponse(
        status=status,
        data_source=source.describe(),
        datasets_available=available,
        models=models,
        contains_simulated=True,
        notice=API_SIMULATED_NOTICE,
    )


# ---------------------------------------------------------------------------
# POST /score/denial
# ---------------------------------------------------------------------------


@app.post("/score/denial", responses={503: {"model": ErrorResponse}})
def score_denial(request: DenialScoreRequest) -> DenialScoreResponse:  # type: ignore[valid-type]
    """Score one claim for denial risk at the moment BEFORE submission."""
    model = scoring.load_denial_risk_model()
    features = request.model_dump(exclude_none=False)
    matrix, imputed = scoring.prepare_request_frame(features, model)
    calibrated, raw = model.score(matrix)

    risk = float(calibrated[0])
    payload: dict[str, Any] = {
        "sim_denial_risk": round(risk, 6),
        "sim_denial_risk_uncalibrated": round(float(raw[0]), 6),
        "risk_band": _risk_band(risk),
        "flagged_for_review": bool(risk >= model.top_decile_threshold),
        "reason_codes": scoring.top_reason_codes(features),
        "features_supplied": len(model.feature_names) - len(imputed),
        "features_expected": len(model.feature_names),
        "imputed_features": imputed,
        "model_champion": scoring.MODEL_A_CHAMPION,
        "model_version": model.version,
        "model_fitted_at": model.fitted_at,
        "scored_at": utc_now(),
        "limitations": MODEL_A_LIMITATIONS,
    }
    return DenialScoreResponse(**payload, meta=_meta(payload))


def _risk_band(risk: float) -> str:
    """Bands relative to the book's 12.9% base rate, not to a mental 0-100 scale.

    A caller reading 0.31 has no way to know whether that is high without knowing
    the base rate; a band that means "2.4x the rate at which claims are denied
    here" is the number they actually wanted.
    """
    if risk < 0.06:
        return "low"
    if risk < 0.13:
        return "moderate"
    if risk < 0.26:
        return "elevated"
    return "high"


# ---------------------------------------------------------------------------
# POST /score/appeal
# ---------------------------------------------------------------------------


@app.post("/score/appeal", responses={503: {"model": ErrorResponse}})
def score_appeal(request: AppealScoreRequest) -> AppealScoreResponse:
    """Expected Net Recovery, the policy tier, and the recommended action.

    Read `probability_source` before `sim_p_overturn`: the number is the Model C
    champion's own score only for a claim the shipped run ranked. Otherwise it is
    the denial-category rule, which is what Model C fails to beat.
    """
    from src.models.work_queue import build_work_queue

    economics = scoring.appeal_economics()
    probability, source_label = _resolve_overturn_probability(request)

    frame = pd.DataFrame(
        [
            {
                "claim_sk": int(request.claim_sk or 0),
                "sim_denied_amount": float(request.sim_denied_amount),
                "sim_denial_review_date": pd.Timestamp(request.sim_denial_review_date),
                "sim_denial_category": request.sim_denial_category,
            }
        ]
    )
    as_of = pd.Timestamp(request.as_of) if request.as_of else pd.Timestamp(utc_now().date())
    queue = build_work_queue(frame, [probability], economics, as_of=as_of)
    scoring.assert_queue_carries_markers(queue)
    row = queue.iloc[0]

    payload: dict[str, Any] = {
        "sim_p_overturn": round(float(row["sim_p_overturn"]), 6),
        "probability_source": source_label,
        "sim_recoverable_amt": round(float(row["sim_recoverable_amt"]), 2),
        "sim_expected_recovery_amt": round(float(row["sim_expected_recovery_amt"]), 2),
        "sim_expected_net_recovery": round(float(row["sim_expected_net_recovery"]), 2),
        "sim_days_to_deadline": int(row["sim_days_to_deadline"]),
        "tier": str(row["tier"]),
        "recommended_action": str(row["recommended_action"]),
        "recovery_ratio": round(float(economics.recovery_ratio), 4),
        "processing_cost_usd": float(economics.processing_cost_usd),
        "filing_window_days": int(economics.filing_window_days),
        "model_champion": scoring.MODEL_C_CHAMPION,
        "model_version": f"model_c-shipped-run-{scoring.model_a_version().rsplit('-', 1)[-1]}",
        "scored_at": utc_now(),
        "limitations": [*MODEL_C_LIMITATIONS, scoring.ORDERING_NOTE],
    }
    return AppealScoreResponse(**payload, meta=_meta(payload))


def _resolve_overturn_probability(request: AppealScoreRequest) -> tuple[float, str]:
    """Where the probability came from, decided once and reported to the caller."""
    if request.sim_p_overturn is not None:
        return float(request.sim_p_overturn), scoring.PROBABILITY_FROM_CALLER

    if request.claim_sk is not None:
        shipped = scoring.shipped_queue_probability(int(request.claim_sk))
        if shipped is not None:
            return shipped, scoring.PROBABILITY_FROM_MODEL_C

    rates = scoring.category_overturn_rates()
    category = str(request.sim_denial_category)
    if category in rates:
        return rates[
            category
        ], f"{scoring.PROBABILITY_FROM_CATEGORY_RULE}. {scoring.CATEGORY_RULE_NOTE}"
    return (
        scoring.book_overturn_rate(),
        (
            f"book-wide prior overturn rate: no appeals are recorded for denial category "
            f"{category!r}, so the category rule has no support for it and this falls back to "
            f"the book. {scoring.CATEGORY_RULE_NOTE}"
        ),
    )


# ---------------------------------------------------------------------------
# GET /claims/{claim_id}
# ---------------------------------------------------------------------------


@app.get("/claims/{claim_id}", responses={404: {"model": ErrorResponse}})
def get_claim(
    claim_id: Annotated[
        str,
        Path(description="A `clm_id` (e.g. -10000930037831) or a warehouse `claim_sk`."),
    ],
    role: Annotated[
        Role, Query(description="`analyst` returns the full column set.")
    ] = "executive",
) -> ClaimResponse:
    """One enriched claim, resolved by `clm_id` first and then by `claim_sk`.

    The two identifier spaces cannot collide on this warehouse — every `clm_id` is
    a negative-signed string and every `claim_sk` is a small positive integer — so
    the precedence is unambiguous rather than merely documented.
    """
    claims = _frame("vw_claim_enriched")
    match = claims.loc[claims["clm_id"].astype(str) == claim_id]
    if match.empty and claim_id.lstrip("-").isdigit():
        match = claims.loc[claims["claim_sk"] == int(claim_id)]
    if match.empty:
        raise HTTPException(
            status_code=404,
            detail={
                "detail": f"no claim {claim_id!r} in {len(claims):,} claims.",
                "error": "claim_not_found",
                "hint": "Try a clm_id such as -10000930037831, or a claim_sk between 1 and 20867.",
            },
        )

    record = tables.build_claim_record(match.iloc[0])
    if role == "executive":
        record["claim"] = {
            key: value
            for key, value in record["claim"].items()
            if not key.startswith(("sim_touch", "sim_denial_driver", "bene_"))
        }

    risk, fold = _denial_risk_for(int(match.iloc[0]["claim_sk"]))
    payload: dict[str, Any] = {
        "claim_sk": int(match.iloc[0]["claim_sk"]),
        "clm_id": str(match.iloc[0]["clm_id"]),
        "prvdr_num": _optional_str(match.iloc[0].get("prvdr_num")),
        **record,
        "sim_denial_risk": risk,
        "denial_risk_fold": fold,
    }
    return ClaimResponse(**payload, meta=_meta(payload, datasets=("vw_claim_enriched",)))


def _optional_str(value: Any) -> str | None:
    return None if value is None or pd.isna(value) else str(value)


def _denial_risk_for(claim_sk: int) -> tuple[float | None, str | None]:
    """This claim's Model A score, with the fold that says whether it is in-sample."""
    source = _source()
    if "model_a_scores" not in source.available():
        return None, None
    scores = source.frame("model_a_scores")
    match = scores.loc[scores["claim_sk"] == claim_sk]
    if match.empty:
        return None, None
    return round(float(match.iloc[0]["sim_denial_risk"]), 6), str(match.iloc[0]["fold"])


# ---------------------------------------------------------------------------
# GET /work-queue
# ---------------------------------------------------------------------------


@app.get("/work-queue", responses={501: {"model": ErrorResponse}})
def work_queue(
    queue_mode: Annotated[
        Literal["backtest", "live_snapshot", "heuristic"],
        Query(
            description=(
                "`backtest` triages each claim on the day its denial posted; "
                "`live_snapshot` is one instant (degenerate on this warehouse - the "
                "2023-24 tail is thin, so 1 claim is open and 467 are out of window); "
                "`heuristic` is the rule-based scaffold view, available without model "
                "artifacts."
            )
        ),
    ] = "backtest",
    tier: Annotated[str | None, Query(description="Filter to one policy tier.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    role: Annotated[Role, Query()] = "executive",
) -> WorkQueueResponse:
    """The denial worklist, in the order the shipped queue ranks it."""
    if queue_mode == "heuristic":
        full = _frame("vw_work_queue_priority")
        rows = tables.build_heuristic_queue_table(full, limit=limit, offset=offset)
        ranking = (
            "HEURISTIC SCAFFOLD - dollars weighted by age, no learned weights, not a model "
            "score and not an Expected Net Recovery."
        )
        datasets = ("vw_work_queue_priority",)
    else:
        full = _frame("model_c_work_queue")
        full = full.loc[full["queue_mode"] == queue_mode]
        rows = tables.build_work_queue_table(full, tier=tier, limit=limit, offset=offset)
        ranking = (
            "Model C Expected Net Recovery, ordered by policy TIER first and expected net "
            "recovery within tier. Deadline-critical claims outrank larger ones by design."
        )
        datasets = ("model_c_work_queue",)

    if role == "executive":
        rows = rows.drop(columns=[c for c in ("claim_sk", "clm_id") if c in rows.columns])

    payload: dict[str, Any] = {
        "queue_mode": queue_mode,
        "ranking": ranking,
        "rows": rows.to_dict(orient="records"),
        "total_rows": int(len(full)),
        "limit": limit,
        "offset": offset,
        "tier_counts": tables.tier_counts(full),
        "ordering_caveat": scoring.ORDERING_NOTE,
        "membership": (
            "This is an outcome-selected worklist: every row was selected because the "
            "simulation denied the claim or left it with open A/R. It is not a neutral "
            "pre-outcome claim population."
        ),
        "limitations": MODEL_C_LIMITATIONS,
    }
    return WorkQueueResponse(**payload, meta=_meta(payload, datasets=datasets))


# ---------------------------------------------------------------------------
# GET /metrics/executive
# ---------------------------------------------------------------------------


@app.get("/metrics/executive")
def executive_metrics(
    include_monthly: Annotated[bool, Query(description="Return the monthly trend too.")] = True,
) -> ExecutiveMetricsResponse:
    """Book-level RCM KPIs, rolled up from the monthly executive view.

    Rates are recomputed from summed numerators and denominators, never averaged
    across months: a mean of monthly denial rates weights a 40-claim month like a
    400-claim one and stops equalling the control query.
    """
    monthly = _frame("vw_executive_rcm_summary")
    summary = tables.build_executive_summary(monthly)
    payload: dict[str, Any] = {
        **summary,
        "monthly": monthly.to_dict(orient="records") if include_monthly else [],
        "reconciliation": tables.executive_control_totals(monthly),
    }
    return ExecutiveMetricsResponse(
        **payload, meta=_meta(payload, datasets=("vw_executive_rcm_summary",))
    )
