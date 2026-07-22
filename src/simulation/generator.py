"""The simulated adjudication generator.

Everything this module produces is INVENTED (CLAUDE.md §3, docs/assumptions.md).
The source CMS synthetic claims contain no denials, no submission or
adjudication dates, no appeals, and no workflow events; all of that is
fabricated here from config/simulation.yaml. Nothing generated here describes
real Medicare, Medicare Advantage, commercial, or Medicaid adjudication.

The pipeline runs in CAUSAL order, and that ordering is the point:

    pre-submission facts  ->  timeline through submission  ->  latent denial
    probability  ->  realized denial + category  ->  money  ->  post-submission
    timeline  ->  appeals  ->  workflow events  ->  operating costs

Everything a model could legitimately know at scoring time is produced before
the denial is drawn; everything produced after it is post-submission and belongs
on the forbidden list (CLAUDE.md §4). `sim_latent_p`, `sim_provider_quality_latent`
and `sim_appeal_latent_p` are stored for validation only and are never features.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .base import (
    assign_service_line,
    claim_base,
    expit,
    logit,
    rounded_days,
    solve_intercept,
    zscore,
)
from .config import SimulationConfig, load_config, stream

# Event type -> causal rank, used only to break same-date ties deterministically.
_EVENT_RANK: dict[str, int] = {
    "CODING_COMPLETE": 1,
    "CLAIM_SUBMITTED": 2,
    "PAYER_ACKNOWLEDGED": 3,
    "ADJUDICATED": 4,
    "DENIAL_POSTED": 5,
    "PAYMENT_POSTED": 6,
    "DENIAL_REVIEWED": 7,
    "APPEAL_FILED": 8,
    "APPEAL_DECISION": 9,
    "APPEAL_RECOVERY_POSTED": 10,
    "CLAIM_CLOSED": 11,
}
_EVENT_ACTOR: dict[str, str] = {
    "CODING_COMPLETE": "CODER",
    "CLAIM_SUBMITTED": "BILLER",
    "PAYER_ACKNOWLEDGED": "PAYER_SYSTEM",
    "ADJUDICATED": "PAYER_SYSTEM",
    "DENIAL_POSTED": "PAYER_SYSTEM",
    "PAYMENT_POSTED": "PAYMENT_POSTER",
    "DENIAL_REVIEWED": "DENIAL_ANALYST",
    "APPEAL_FILED": "APPEALS_SPECIALIST",
    "APPEAL_DECISION": "PAYER_SYSTEM",
    "APPEAL_RECOVERY_POSTED": "PAYMENT_POSTER",
    "CLAIM_CLOSED": "SYSTEM",
}
# Which activity's touch-minute distribution each event consumes. Events absent
# from this map are payer-side or automated and cost the provider no labour.
_EVENT_ACTIVITY: dict[str, str] = {
    "CODING_COMPLETE": "coding",
    "CLAIM_SUBMITTED": "billing_submission",
    "PAYMENT_POSTED": "payment_posting",
    "APPEAL_RECOVERY_POSTED": "payment_posting",
    "DENIAL_REVIEWED": "denial_review",
    "APPEAL_FILED": "appeal_preparation",
}
_DENIAL_NONE = "NONE"


@dataclass
class SimulationResult:
    """Every generated table, plus a report of what the run actually produced."""

    tables: dict[str, pd.DataFrame]
    report: dict[str, object] = field(default_factory=dict)
    # The SOURCE/DERIVED claim frame the run was built on. Carried so validation
    # can check simulated money against the real billed charge without going back
    # to the warehouse, and so it is obvious this frame is NOT an output table.
    base: pd.DataFrame = field(default_factory=pd.DataFrame)

    def table(self, name: str) -> pd.DataFrame:
        return self.tables[name]


def _to_np_days(dates: pd.Series) -> np.ndarray:
    return pd.to_datetime(dates).to_numpy().astype("datetime64[D]")


def _to_ts(days: np.ndarray) -> pd.Series:
    return pd.Series(days.astype("datetime64[ns]"))


def _gamma(rng, spec: dict, size: int) -> np.ndarray:
    return rng.gamma(shape=float(spec["shape"]), scale=float(spec["scale"]), size=size)


def _lognormal(rng, spec: dict, size: int) -> np.ndarray:
    return rng.lognormal(mean=float(spec["mu"]), sigma=float(spec["sigma"]), size=size)


def _choice_by_group(
    rng, group_keys: np.ndarray, distributions: dict[str, dict[str, float]]
) -> np.ndarray:
    """Draw a categorical value per row, conditional on that row's group key.

    Groups are visited in sorted order so the draw sequence does not depend on
    dict or row ordering — otherwise "same seed, same output" would hold only by
    accident of insertion order.
    """
    out = np.empty(len(group_keys), dtype=object)
    for key in sorted(set(group_keys.tolist())):
        mask = group_keys == key
        dist = distributions[key]
        labels = sorted(dist)
        probs = np.array([dist[label] for label in labels], dtype="float64")
        probs = probs / probs.sum()
        out[mask] = rng.choice(labels, size=int(mask.sum()), p=probs)
    return out


# ---------------------------------------------------------------------------
# 1. Structural attributes: payer, service line, provider quality
# ---------------------------------------------------------------------------
def _assign_payers(cfg: SimulationConfig, base: pd.DataFrame) -> pd.Series:
    """Assign a simulated payer archetype per BENEFICIARY, not per claim.

    Coverage belongs to a person, so a beneficiary's claims share a payer. This
    also creates the within-beneficiary correlation that makes naive random
    train/test splitting leak — which is a hazard Phase 4 has to handle honestly.
    """
    rng = stream(cfg.seed, "payer_assignment")
    benes = np.sort(base["bene_key"].unique())
    probs = np.array([p.mix for p in cfg.payers], dtype="float64")
    assigned = rng.choice(cfg.payer_ids, size=len(benes), p=probs / probs.sum())
    lookup = dict(zip(benes.tolist(), assigned.tolist()))
    return base["bene_key"].map(lookup).astype("string")


def _provider_quality(cfg: SimulationConfig, base: pd.DataFrame) -> pd.Series:
    """One latent 'clean claim' quality draw per billing provider.

    Positive means a worse biller (it enters the denial log-odds additively).
    """
    rng = stream(cfg.seed, "provider_quality")
    sigma = float(cfg.mechanisms["nonlinear"]["provider_quality_sigma"])
    providers = np.sort(base["prvdr_num"].fillna("UNKNOWN").unique())
    draws = rng.normal(0.0, sigma, size=len(providers))
    lookup = dict(zip(providers.tolist(), draws.tolist()))
    return base["prvdr_num"].fillna("UNKNOWN").map(lookup).astype("float64")


# ---------------------------------------------------------------------------
# 2. Pre-submission facts (legitimate model features)
# ---------------------------------------------------------------------------
def _authorization_eligibility(
    cfg: SimulationConfig, base: pd.DataFrame, payer_id: pd.Series, quality: np.ndarray
) -> pd.DataFrame:
    rng = stream(cfg.seed, "authorization_eligibility")
    n = len(base)
    prev = cfg.mechanisms["prevalence"]
    scale = float(cfg.mechanisms["nonlinear"]["provider_quality_prevalence_scale"])

    auth_required_rate = payer_id.map({p.id: p.auth_required_rate for p in cfg.payers}).to_numpy(
        dtype="float64"
    )
    auth_required = rng.random(n) < auth_required_rate

    # A worse biller misses authorizations more often — this is what makes a
    # provider's historical clean-claim rate genuinely predictive downstream.
    p_missing = expit(
        np.full(n, logit(float(prev["auth_missing_given_required"]))) + scale * quality
    )
    auth_missing = auth_required & (rng.random(n) < p_missing)
    auth_obtained = auth_required & ~auth_missing
    auth_late = auth_obtained & (rng.random(n) < 0.15)

    anchor = _to_np_days(base["service_from_date"])
    lead_days = rounded_days(rng.gamma(2.0, 3.0, size=n), minimum=0)
    request_date = anchor - lead_days.astype("timedelta64[D]")
    decision_lag = rounded_days(rng.gamma(1.5, 2.0, size=n), minimum=0)
    decision_date = request_date + decision_lag.astype("timedelta64[D]")
    # A "late" authorization is one decided after the service already started.
    decision_date = np.where(auth_late, anchor + np.timedelta64(2, "D"), decision_date)

    checked = rng.random(n) < float(prev["eligibility_verification_attempted"])
    elig_failed = checked & (rng.random(n) < float(prev["eligibility_failed"]))
    check_lead = rounded_days(rng.gamma(2.0, 2.0, size=n), minimum=0)
    check_date = anchor - check_lead.astype("timedelta64[D]")
    secondary_payer = rng.random(n) < 0.18

    return pd.DataFrame(
        {
            "claim_sk": base["claim_sk"].to_numpy(),
            "clm_id": base["clm_id"].to_numpy(),
            "sim_payer_id": payer_id.to_numpy(),
            "sim_auth_required": auth_required,
            "sim_auth_obtained": auth_obtained,
            "sim_auth_missing": auth_missing,
            "sim_auth_obtained_late": auth_late,
            "sim_auth_reference_id": np.where(
                auth_obtained,
                pd.Series(base["claim_sk"].to_numpy()).map(lambda v: f"SIMAUTH-{v:08d}").to_numpy(),
                None,
            ),
            "sim_auth_request_date": _to_ts(
                np.where(auth_required, request_date, np.datetime64("NaT"))
            ),
            "sim_auth_decision_date": _to_ts(
                np.where(auth_obtained | auth_late, decision_date, np.datetime64("NaT"))
            ),
            "sim_eligibility_checked": checked,
            "sim_eligibility_failed": elig_failed,
            "sim_eligibility_check_date": _to_ts(
                np.where(checked, check_date, np.datetime64("NaT"))
            ),
            "sim_secondary_payer_present": secondary_payer,
        }
    )


def _documentation_coding(
    cfg: SimulationConfig, base: pd.DataFrame, quality: np.ndarray
) -> pd.DataFrame:
    rng = stream(cfg.seed, "documentation_coding")
    n = len(base)
    prev = cfg.mechanisms["prevalence"]
    scale = float(cfg.mechanisms["nonlinear"]["provider_quality_prevalence_scale"])

    p_doc_deficit = expit(np.full(n, logit(float(prev["documentation_deficit"]))) + scale * quality)
    doc_deficit = rng.random(n) < p_doc_deficit
    coding_deficit = rng.random(n) < float(prev["coding_specificity_deficit"])
    duplicate = rng.random(n) < float(prev["duplicate_submission"])

    # Completeness score: a noisy observable of the latent deficit, so a model
    # sees a usable-but-imperfect signal rather than the mechanism itself.
    score = np.where(doc_deficit, rng.beta(2.5, 5.0, size=n), rng.beta(7.0, 2.0, size=n))
    query_outstanding = doc_deficit & (rng.random(n) < 0.45)

    return pd.DataFrame(
        {
            "claim_sk": base["claim_sk"].to_numpy(),
            "clm_id": base["clm_id"].to_numpy(),
            "sim_documentation_complete": ~doc_deficit,
            "sim_documentation_score": np.round(score, 4),
            "sim_coder_query_outstanding": query_outstanding,
            "sim_coding_specificity_deficit": coding_deficit,
            "sim_coding_complexity_score": np.round(
                expit(zscore(base["diagnosis_count"].to_numpy(dtype="float64"))), 4
            ),
            "sim_duplicate_submission_flag": duplicate,
        }
    )


# ---------------------------------------------------------------------------
# 3. Timeline through submission (late filing is endogenous)
# ---------------------------------------------------------------------------
def _pre_submission_timeline(
    cfg: SimulationConfig, base: pd.DataFrame, payer_id: pd.Series
) -> dict[str, np.ndarray]:
    rng = stream(cfg.seed, "timeline_pre_submission")
    n = len(base)
    tl = cfg.timelines
    anchor = _to_np_days(base["anchor_date"])

    coded_lag = rounded_days(_gamma(rng, tl["discharge_to_coded_gamma"], n), minimum=0)
    submit_lag = rounded_days(_lognormal(rng, tl["coded_to_submission_lognormal"], n), minimum=0)
    # A small tail stalls in the billing office. This is the ONLY route to a
    # late-filing denial: whether a claim is late falls out of the delay meeting
    # the payer's contractual limit, rather than being asserted as a rule.
    stalled = rng.random(n) < float(tl["late_submission_tail_rate"])
    extra = rounded_days(_lognormal(rng, tl["late_submission_extra_days_lognormal"], n), minimum=0)
    # Cap the tail. Uncapped, the lognormal reached 5.3 years past the service
    # date — implausible, and it pushed generated dates past the source data's
    # own period. The cap is above every configured filing limit, so claims in
    # the tail are still late for every payer and the mechanism is unchanged.
    extra = np.minimum(extra, int(tl["late_submission_max_extra_days"]))
    submit_lag = submit_lag + np.where(stalled, extra, 0)

    coded_date = anchor + coded_lag.astype("timedelta64[D]")
    submission_date = coded_date + submit_lag.astype("timedelta64[D]")

    limit = payer_id.map({p.id: p.timely_filing_days for p in cfg.payers}).to_numpy(dtype="int64")
    days_from_service = (submission_date - _to_np_days(base["service_from_date"])).astype("int64")
    late_filing = days_from_service > limit

    return {
        "coded_date": coded_date,
        "submission_date": submission_date,
        "filing_limit_days": limit,
        "days_service_to_submission": days_from_service,
        "late_filing": late_filing,
    }


# ---------------------------------------------------------------------------
# 4. Latent denial probability
# ---------------------------------------------------------------------------
def _mechanism_contributions(
    cfg: SimulationConfig,
    auth: pd.DataFrame,
    doc: pd.DataFrame,
    timeline: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Log-odds contributed by each DISCRETE mechanism, per claim.

    Kept separately from the continuous terms because the denial category is
    drawn conditionally on whichever of these contributed most — so the category
    carries real information about the cause rather than being an independent
    label bolted on afterwards.
    """
    orr = cfg.mechanisms["odds_ratios"]
    inter = cfg.mechanisms["interactions"]
    n = len(auth)
    zeros = np.zeros(n, dtype="float64")

    auth_missing = auth["sim_auth_missing"].to_numpy()
    auth_required = auth["sim_auth_required"].to_numpy()
    doc_deficit = ~doc["sim_documentation_complete"].to_numpy()

    contrib: dict[str, np.ndarray] = {}
    # The headline interaction: an auth being required is close to harmless on
    # its own; required AND missing is what bites.
    contrib["authorization_missing"] = np.where(
        auth_required & auth_missing,
        np.log(float(orr["authorization_missing"]))
        + np.log(float(inter["auth_required_x_auth_missing"])),
        zeros,
    )
    contrib["authorization_late"] = np.where(
        auth["sim_auth_obtained_late"].to_numpy(), np.log(float(orr["authorization_late"])), zeros
    )
    contrib["eligibility_failed"] = np.where(
        auth["sim_eligibility_failed"].to_numpy(), np.log(float(orr["eligibility_failed"])), zeros
    )
    contrib["eligibility_not_verified"] = np.where(
        ~auth["sim_eligibility_checked"].to_numpy(),
        np.log(float(orr["eligibility_not_verified"])),
        zeros,
    )
    contrib["documentation_deficit"] = np.where(
        doc_deficit,
        np.log(float(orr["documentation_deficit"]))
        + np.where(auth_missing, np.log(float(inter["auth_missing_x_documentation_deficit"])), 0.0),
        zeros,
    )
    contrib["coding_specificity_deficit"] = np.where(
        doc["sim_coding_specificity_deficit"].to_numpy(),
        np.log(float(orr["coding_specificity_deficit"])),
        zeros,
    )
    contrib["late_filing"] = np.where(
        timeline["late_filing"], np.log(float(orr["late_filing"])), zeros
    )
    contrib["duplicate_submission"] = np.where(
        doc["sim_duplicate_submission_flag"].to_numpy(),
        np.log(float(orr["duplicate_submission"])),
        zeros,
    )
    return contrib


def _continuous_terms(
    cfg: SimulationConfig,
    base: pd.DataFrame,
    payer_id: pd.Series,
    service_line: pd.Series,
    quality: np.ndarray,
) -> np.ndarray:
    nl = cfg.mechanisms["nonlinear"]
    payer_offset = payer_id.map({p.id: p.logit_offset for p in cfg.payers}).to_numpy("float64")
    sl_offset = service_line.map({s.id: s.logit_offset for s in cfg.service_lines}).to_numpy(
        "float64"
    )

    cross = cfg.mechanisms["interactions"]["payer_x_service_line"] or {}
    pairs = pd.Series(
        [
            float(cross.get(p, {}).get(s, 0.0))
            for p, s in zip(payer_id.tolist(), service_line.tolist())
        ],
        dtype="float64",
    ).to_numpy()

    z_charge = zscore(np.log1p(base["billed_amount"].to_numpy(dtype="float64")))
    charge = float(nl["log_charge"]["b1"]) * z_charge + float(nl["log_charge"]["b2"]) * z_charge**2

    los = base["length_of_stay_days"].to_numpy(dtype="float64")
    los_spec = nl["length_of_stay"]
    los_term = (
        float(los_spec["b_short"]) * (los <= float(los_spec["short_days"]))
        + float(los_spec["b_long"]) * np.maximum(0.0, los - float(los_spec["long_days"])) / 10.0
    )

    dx = base["diagnosis_count"].to_numpy(dtype="float64") / 10.0
    dx_spec = nl["diagnosis_count"]
    dx_term = float(dx_spec["b1"]) * dx + float(dx_spec["b2"]) * dx**2

    return payer_offset + sl_offset + pairs + charge + los_term + dx_term + quality


# ---------------------------------------------------------------------------
# 5. Money
# ---------------------------------------------------------------------------
def _money(
    cfg: SimulationConfig,
    base: pd.DataFrame,
    payer_id: pd.Series,
    denied: np.ndarray,
    partial: np.ndarray,
) -> dict[str, np.ndarray]:
    """Allowed / paid / patient-responsibility / adjustment amounts.

    The invariant paid <= allowed <= billed is enforced here by construction and
    re-checked by the validation suite. `sim_billed_amount` deliberately does
    not exist: billed charges are a SOURCE value and stay in the SOURCE fact
    table, reached by join. Copying it into a sim_ column would make a real
    value look generated.
    """
    rng = stream(cfg.seed, "money")
    n = len(base)
    billed = base["billed_amount"].to_numpy(dtype="float64")

    ratio = np.zeros(n, dtype="float64")
    for payer in cfg.payers:
        mask = (payer_id == payer.id).to_numpy()
        count = int(mask.sum())
        if count:
            spec = payer.allowed_ratio_beta
            ratio[mask] = rng.beta(float(spec["a"]), float(spec["b"]), size=count)
    allowed = np.minimum(np.round(billed * ratio, 2), billed)

    # Patient cost share (deductible + coinsurance) on the allowed amount.
    patient_share = rng.beta(1.5, 18.0, size=n)
    patient_resp = np.round(allowed * patient_share, 2)

    reduction = np.zeros(n, dtype="float64")
    reduction[denied & partial] = rng.beta(2.0, 3.0, size=int((denied & partial).sum()))
    reduction[denied & ~partial] = 1.0
    denied_amount = np.round(allowed * reduction, 2)

    paid = np.round(allowed - patient_resp - denied_amount, 2)
    # Rounding can push paid marginally negative on tiny balances; the invariant
    # matters more than the cent, and the shortfall is absorbed into the denial.
    shortfall = np.maximum(0.0, -paid)
    denied_amount = np.round(denied_amount - shortfall, 2)
    paid = np.clip(np.round(paid + shortfall, 2), 0.0, None)
    paid = np.minimum(paid, allowed)

    return {
        "allowed": allowed,
        "paid": paid,
        "patient_resp": patient_resp,
        "denied_amount": np.clip(denied_amount, 0.0, None),
        "adjustment": np.round(billed - allowed, 2),
    }


# ---------------------------------------------------------------------------
# 6. Post-submission timeline
# ---------------------------------------------------------------------------
def _post_submission_timeline(
    cfg: SimulationConfig,
    payer_id: pd.Series,
    submission_date: np.ndarray,
    denied: np.ndarray,
    paid: np.ndarray,
) -> dict[str, np.ndarray]:
    rng = stream(cfg.seed, "timeline_post_submission")
    n = len(submission_date)
    tl = cfg.timelines

    ack_spec = tl["submission_to_ack_days"]
    ack_lag = rng.integers(int(ack_spec["min"]), int(ack_spec["max"]) + 1, size=n)
    ack_date = submission_date + ack_lag.astype("timedelta64[D]")

    adj_lag = np.zeros(n, dtype="int64")
    for payer in cfg.payers:
        mask = (payer_id == payer.id).to_numpy()
        count = int(mask.sum())
        if count:
            adj_lag[mask] = rounded_days(
                _lognormal(rng, payer.adjudication_lognormal, count), minimum=1
            )
    adjudication_date = ack_date + adj_lag.astype("timedelta64[D]")

    pay_lag = rounded_days(_lognormal(rng, tl["adjudication_to_payment_lognormal"], n), minimum=0)
    payment_date = adjudication_date + pay_lag.astype("timedelta64[D]")
    payment_date = np.where(paid > 0.0, payment_date, np.datetime64("NaT"))

    review_lag = rounded_days(rng.gamma(2.0, 2.5, size=n), minimum=1)
    denial_review_date = np.where(
        denied, adjudication_date + review_lag.astype("timedelta64[D]"), np.datetime64("NaT")
    )

    return {
        "ack_date": ack_date,
        "adjudication_date": adjudication_date,
        "payment_date": payment_date,
        "denial_review_date": denial_review_date,
    }


# ---------------------------------------------------------------------------
# 7. Appeals
# ---------------------------------------------------------------------------
def _solve_overturn_intercept(
    linear: np.ndarray, attempt2: np.ndarray, multiplier: float, target: float
) -> float:
    """Intercept such that P(overturned at any level) averages `target`.

    Level 2 is only attempted when level 1 is upheld and the balance clears the
    configured floor, so the overall rate is p1 + (1-p1)·attempt2·p2 and has no
    closed form. Bisection again.
    """
    lo, hi = -25.0, 25.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        p1 = expit(linear + mid)
        odds2 = np.exp(linear + mid) * multiplier
        p2 = odds2 / (1.0 + odds2)
        overall = float(np.mean(p1 + (1.0 - p1) * attempt2 * p2))
        if overall < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _appeals(
    cfg: SimulationConfig,
    adjudication: pd.DataFrame,
    doc: pd.DataFrame,
    denial_review_date: np.ndarray,
) -> pd.DataFrame:
    """One row per (claim, appeal level) for denied claims that get worked."""
    rng = stream(cfg.seed, "appeals")
    ap = cfg.appeal
    denied_mask = adjudication["sim_denial_flag"].to_numpy()
    if not denied_mask.any():
        return _empty_appeals()

    idx = np.flatnonzero(denied_mask)
    category = adjudication["sim_denial_category"].to_numpy()[idx]
    disputed = adjudication["sim_denied_amount"].to_numpy()[idx]
    partial = adjudication["sim_denial_type"].to_numpy()[idx] == "PARTIAL"
    doc_complete = doc["sim_documentation_complete"].to_numpy()[idx]
    review_date = denial_review_date[idx]

    # Propensity: category base, scaled by the size of the balance. A finite
    # billing team works dollars, which is exactly what makes the Phase 4
    # expected-net-recovery work-queue score a real decision problem.
    base_prop = np.array(
        [float(ap["propensity_by_category"][c]) for c in category], dtype="float64"
    )
    z_amount = zscore(np.log1p(disputed))
    scaled = np.clip(base_prop * (1.0 + float(ap["amount_propensity_k"]) * z_amount), 0.01, 0.95)
    shift = solve_intercept(
        np.log(scaled / (1.0 - scaled)), float(cfg.targets["appeal_rate"]["point"])
    )
    appealed = rng.random(len(idx)) < expit(np.log(scaled / (1.0 - scaled)) + shift)
    if not appealed.any():
        return _empty_appeals()

    sel = np.flatnonzero(appealed)
    a_idx = idx[sel]
    a_category = category[sel]
    a_disputed = disputed[sel]
    a_review = review_date[sel]

    linear = (
        np.array([float(ap["overturn_logit_by_category"][c]) for c in a_category], dtype="float64")
        + float(ap["overturn_logit_documentation_complete"]) * doc_complete[sel]
        + float(ap["overturn_logit_partial_denial"]) * partial[sel]
    )
    eligible_l2 = (a_disputed >= float(ap["level_2_min_amount"])).astype("float64")
    attempt2_prob = eligible_l2 * float(ap["level_2_attempt_rate"])
    intercept = _solve_overturn_intercept(
        linear,
        attempt2_prob,
        float(ap["level_2_overturn_multiplier"]),
        float(cfg.targets["appeal_overturn_rate"]["point"]),
    )
    p1 = expit(linear + intercept)
    overturned_1 = rng.random(len(sel)) < p1

    filed_lag = rounded_days(_lognormal(rng, cfg.timelines["denial_to_appeal_lognormal"], len(sel)))
    filed_1 = a_review + filed_lag.astype("timedelta64[D]")
    decide_lag = rounded_days(
        _lognormal(rng, cfg.timelines["appeal_decision_lognormal"], len(sel)), minimum=1
    )
    decided_1 = filed_1 + decide_lag.astype("timedelta64[D]")

    recovery_spec = ap["recovery_fraction_beta"]
    recovered_1 = np.where(
        overturned_1,
        np.round(
            a_disputed
            * rng.beta(float(recovery_spec["a"]), float(recovery_spec["b"]), size=len(sel)),
            2,
        ),
        0.0,
    )

    rows = [
        pd.DataFrame(
            {
                "claim_sk": adjudication["claim_sk"].to_numpy()[a_idx],
                "clm_id": adjudication["clm_id"].to_numpy()[a_idx],
                "sim_appeal_level": np.int16(1),
                "sim_appeal_filed_date": _to_ts(filed_1),
                "sim_appeal_decision_date": _to_ts(decided_1),
                "sim_appeal_outcome": np.where(overturned_1, "OVERTURNED", "UPHELD"),
                "sim_appeal_disputed_amount": np.round(a_disputed, 2),
                "sim_appeal_recovered_amount": recovered_1,
                "sim_appeal_latent_p": np.round(p1, 6),
            }
        )
    ]

    # Level 2: only where level 1 was upheld, the balance clears the floor, and
    # the team elects to escalate.
    escalate = (
        (~overturned_1)
        & (eligible_l2 > 0)
        & (rng.random(len(sel)) < float(ap["level_2_attempt_rate"]))
    )
    if escalate.any():
        e = np.flatnonzero(escalate)
        odds2 = np.exp(linear[e] + intercept) * float(ap["level_2_overturn_multiplier"])
        p2 = odds2 / (1.0 + odds2)
        overturned_2 = rng.random(len(e)) < p2
        filed_2 = decided_1[e] + rounded_days(
            _lognormal(rng, cfg.timelines["denial_to_appeal_lognormal"], len(e)), minimum=1
        ).astype("timedelta64[D]")
        decided_2 = filed_2 + rounded_days(
            _lognormal(rng, cfg.timelines["appeal_decision_lognormal"], len(e)), minimum=1
        ).astype("timedelta64[D]")
        recovered_2 = np.where(
            overturned_2,
            np.round(
                a_disputed[e]
                * rng.beta(float(recovery_spec["a"]), float(recovery_spec["b"]), size=len(e)),
                2,
            ),
            0.0,
        )
        rows.append(
            pd.DataFrame(
                {
                    "claim_sk": adjudication["claim_sk"].to_numpy()[a_idx][e],
                    "clm_id": adjudication["clm_id"].to_numpy()[a_idx][e],
                    "sim_appeal_level": np.int16(2),
                    "sim_appeal_filed_date": _to_ts(filed_2),
                    "sim_appeal_decision_date": _to_ts(decided_2),
                    "sim_appeal_outcome": np.where(overturned_2, "OVERTURNED", "UPHELD"),
                    "sim_appeal_disputed_amount": np.round(a_disputed[e], 2),
                    "sim_appeal_recovered_amount": recovered_2,
                    "sim_appeal_latent_p": np.round(p2, 6),
                }
            )
        )

    appeals = pd.concat(rows, ignore_index=True)
    appeals = appeals.sort_values(["claim_sk", "sim_appeal_level"]).reset_index(drop=True)
    appeals.insert(0, "sim_appeal_sk", np.arange(1, len(appeals) + 1, dtype="int64"))
    appeals["sim_appeal_level"] = appeals["sim_appeal_level"].astype("int16")
    return appeals


def _empty_appeals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sim_appeal_sk": pd.Series(dtype="int64"),
            "claim_sk": pd.Series(dtype="int64"),
            "clm_id": pd.Series(dtype="string"),
            "sim_appeal_level": pd.Series(dtype="int16"),
            "sim_appeal_filed_date": pd.Series(dtype="datetime64[ns]"),
            "sim_appeal_decision_date": pd.Series(dtype="datetime64[ns]"),
            "sim_appeal_outcome": pd.Series(dtype="object"),
            "sim_appeal_disputed_amount": pd.Series(dtype="float64"),
            "sim_appeal_recovered_amount": pd.Series(dtype="float64"),
            "sim_appeal_latent_p": pd.Series(dtype="float64"),
        }
    )


# ---------------------------------------------------------------------------
# 8. Workflow event log
# ---------------------------------------------------------------------------
def _workflow_events(
    cfg: SimulationConfig, adjudication: pd.DataFrame, appeals: pd.DataFrame
) -> pd.DataFrame:
    """A process-mining-ready event log, one row per (claim, event occurrence).

    Sequence numbers are assigned by sorting on the generated DATE (with a
    causal rank breaking same-day ties), not by the order events were built, so
    `sim_event_seq` and `sim_event_ts` can never disagree.
    """
    rng = stream(cfg.seed, "workflow_events")
    parts: list[pd.DataFrame] = []

    def add(mask: np.ndarray, event_type: str, dates: np.ndarray, level: int = 0) -> None:
        keep = mask & ~pd.isna(dates)
        if not keep.any():
            return
        parts.append(
            pd.DataFrame(
                {
                    "claim_sk": adjudication["claim_sk"].to_numpy()[keep],
                    "clm_id": adjudication["clm_id"].to_numpy()[keep],
                    "sim_event_type": event_type,
                    "sim_event_date": pd.to_datetime(dates[keep]),
                    "sim_appeal_level": np.int16(level),
                }
            )
        )

    n = len(adjudication)
    everyone = np.ones(n, dtype=bool)
    denied = adjudication["sim_denial_flag"].to_numpy()
    paid_any = adjudication["sim_paid_amount"].to_numpy() > 0.0

    add(everyone, "CODING_COMPLETE", adjudication["sim_coded_date"].to_numpy())
    add(everyone, "CLAIM_SUBMITTED", adjudication["sim_submission_date"].to_numpy())
    add(everyone, "PAYER_ACKNOWLEDGED", adjudication["sim_ack_date"].to_numpy())
    add(everyone, "ADJUDICATED", adjudication["sim_adjudication_date"].to_numpy())
    add(denied, "DENIAL_POSTED", adjudication["sim_adjudication_date"].to_numpy())
    add(denied, "DENIAL_REVIEWED", adjudication["sim_denial_review_date"].to_numpy())
    add(paid_any, "PAYMENT_POSTED", adjudication["sim_payment_date"].to_numpy())

    if not appeals.empty:
        for level in sorted(appeals["sim_appeal_level"].unique().tolist()):
            sub = appeals[appeals["sim_appeal_level"] == level]
            for event_type, col in (
                ("APPEAL_FILED", "sim_appeal_filed_date"),
                ("APPEAL_DECISION", "sim_appeal_decision_date"),
            ):
                parts.append(
                    pd.DataFrame(
                        {
                            "claim_sk": sub["claim_sk"].to_numpy(),
                            "clm_id": sub["clm_id"].to_numpy(),
                            "sim_event_type": event_type,
                            "sim_event_date": pd.to_datetime(sub[col].to_numpy()),
                            "sim_appeal_level": np.int16(level),
                        }
                    )
                )
            won = sub[sub["sim_appeal_recovered_amount"] > 0.0]
            if not won.empty:
                lag = rounded_days(
                    _lognormal(rng, cfg.timelines["adjudication_to_payment_lognormal"], len(won)),
                    minimum=1,
                )
                parts.append(
                    pd.DataFrame(
                        {
                            "claim_sk": won["claim_sk"].to_numpy(),
                            "clm_id": won["clm_id"].to_numpy(),
                            "sim_event_type": "APPEAL_RECOVERY_POSTED",
                            "sim_event_date": pd.to_datetime(
                                won["sim_appeal_decision_date"].to_numpy().astype("datetime64[D]")
                                + lag.astype("timedelta64[D]")
                            ),
                            "sim_appeal_level": np.int16(level),
                        }
                    )
                )

    events = pd.concat(parts, ignore_index=True)

    # CLAIM_CLOSED: a few days after whatever happened last on that claim.
    last = events.groupby("claim_sk", as_index=False)["sim_event_date"].max()
    close_lag = rounded_days(rng.gamma(1.5, 2.0, size=len(last)), minimum=1)
    events = pd.concat(
        [
            events,
            pd.DataFrame(
                {
                    "claim_sk": last["claim_sk"].to_numpy(),
                    "clm_id": last["claim_sk"].map(
                        dict(zip(adjudication["claim_sk"], adjudication["clm_id"]))
                    ),
                    "sim_event_type": "CLAIM_CLOSED",
                    "sim_event_date": last["sim_event_date"] + pd.to_timedelta(close_lag, unit="D"),
                    "sim_appeal_level": np.int16(0),
                }
            ),
        ],
        ignore_index=True,
    )

    events["_rank"] = events["sim_event_type"].map(_EVENT_RANK).astype("int64")
    events = events.sort_values(
        ["claim_sk", "sim_event_date", "_rank", "sim_appeal_level"], kind="mergesort"
    ).reset_index(drop=True)
    events["sim_event_seq"] = events.groupby("claim_sk").cumcount() + 1

    # Time of day: a fixed 45-minute cadence from 07:00 with up to 30 minutes of
    # jitter. The jitter is strictly smaller than the cadence, so timestamps stay
    # strictly increasing within a claim even when several events share a date.
    jitter = rng.integers(0, 30, size=len(events))
    offsets = 7 * 60 + (events["sim_event_seq"].to_numpy() - 1) * 45 + jitter
    events["sim_event_ts"] = events["sim_event_date"] + pd.to_timedelta(offsets, unit="m")
    events["sim_actor_role"] = events["sim_event_type"].map(_EVENT_ACTOR)

    activity = events["sim_event_type"].map(_EVENT_ACTIVITY)
    minutes = np.zeros(len(events), dtype="float64")
    for name, spec in cfg.operating_costs["touch_minutes"].items():
        mask = (activity == name).to_numpy()
        count = int(mask.sum())
        if count:
            minutes[mask] = _gamma(rng, spec, count)
    events["sim_touch_minutes"] = np.round(minutes, 2)
    events["sim_activity"] = activity.fillna("AUTOMATED")

    events = events.drop(columns=["_rank"])
    events.insert(0, "sim_event_sk", np.arange(1, len(events) + 1, dtype="int64"))
    return events[
        [
            "sim_event_sk",
            "claim_sk",
            "clm_id",
            "sim_event_seq",
            "sim_event_type",
            "sim_activity",
            "sim_event_date",
            "sim_event_ts",
            "sim_actor_role",
            "sim_appeal_level",
            "sim_touch_minutes",
        ]
    ]


# ---------------------------------------------------------------------------
# 9. Operating costs (built bottom-up from the event log)
# ---------------------------------------------------------------------------
def _operating_costs(
    cfg: SimulationConfig, adjudication: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    """Cost to collect, derived from touch minutes rather than assigned flat.

    Building costs out of the same event log the process-mining views read means
    `sim_operating_costs` and `sim_workflow_events` reconcile to each other by
    construction, and the realized per-denial rework cost is an OUTPUT that can
    be compared against published benchmarks instead of an input asserted to
    match them.
    """
    oc = cfg.operating_costs
    rate_per_minute = float(oc["labor_rate_per_hour"]) / 60.0 * float(oc["overhead_multiplier"])
    fixed = oc["fixed_costs"]

    pivot = (
        events.pivot_table(
            index="claim_sk",
            columns="sim_activity",
            values="sim_touch_minutes",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(columns=list(oc["touch_minutes"]) + ["AUTOMATED"], fill_value=0.0)
        .fillna(0.0)
    )
    submissions = (
        events[events["sim_event_type"] == "CLAIM_SUBMITTED"]
        .groupby("claim_sk")
        .size()
        .reindex(pivot.index, fill_value=0)
    )
    appeals_filed = (
        events[events["sim_event_type"] == "APPEAL_FILED"]
        .groupby("claim_sk")
        .size()
        .reindex(pivot.index, fill_value=0)
    )

    costs = pd.DataFrame({"claim_sk": pivot.index.to_numpy()})
    costs["sim_coding_cost"] = np.round(pivot["coding"].to_numpy() * rate_per_minute, 2)
    costs["sim_submission_cost"] = np.round(
        pivot["billing_submission"].to_numpy() * rate_per_minute
        + submissions.to_numpy() * float(fixed["clearinghouse_per_submission"]),
        2,
    )
    costs["sim_payment_posting_cost"] = np.round(
        pivot["payment_posting"].to_numpy() * rate_per_minute, 2
    )
    costs["sim_denial_rework_cost"] = np.round(
        pivot["denial_review"].to_numpy() * rate_per_minute, 2
    )
    costs["sim_appeal_cost"] = np.round(
        pivot["appeal_preparation"].to_numpy() * rate_per_minute
        + appeals_filed.to_numpy() * float(fixed["records_retrieval_per_appeal"]),
        2,
    )
    costs["sim_touch_minutes_total"] = np.round(pivot.sum(axis=1).to_numpy(), 2)
    costs["sim_total_cost_to_collect"] = np.round(
        costs[
            [
                "sim_coding_cost",
                "sim_submission_cost",
                "sim_payment_posting_cost",
                "sim_denial_rework_cost",
                "sim_appeal_cost",
            ]
        ].sum(axis=1),
        2,
    )
    costs = costs.merge(adjudication[["claim_sk", "clm_id"]], on="claim_sk", how="left")
    return (
        costs[
            [
                "claim_sk",
                "clm_id",
                "sim_touch_minutes_total",
                "sim_coding_cost",
                "sim_submission_cost",
                "sim_payment_posting_cost",
                "sim_denial_rework_cost",
                "sim_appeal_cost",
                "sim_total_cost_to_collect",
            ]
        ]
        .sort_values("claim_sk")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Dimension tables
# ---------------------------------------------------------------------------
def _payer_dim(cfg: SimulationConfig) -> pd.DataFrame:
    """The simulated payer dimension. 100% invented (CLAUDE.md §3.5)."""
    return pd.DataFrame(
        {
            "sim_payer_id": [p.id for p in cfg.payers],
            "sim_payer_name": [p.name for p in cfg.payers],
            "sim_payer_mix_share": [p.mix for p in cfg.payers],
            "sim_timely_filing_days": [p.timely_filing_days for p in cfg.payers],
        }
    )


def _service_line_dim(cfg: SimulationConfig) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sim_service_line_id": [s.id for s in cfg.service_lines],
            "sim_service_line_name": [s.name for s in cfg.service_lines],
            "sim_drg_range_lo": pd.array([s.lo for s in cfg.service_lines], dtype="Int32"),
            "sim_drg_range_hi": pd.array([s.hi for s in cfg.service_lines], dtype="Int32"),
        }
    )


def _stamp_provenance(cfg: SimulationConfig, tables: dict[str, pd.DataFrame]) -> None:
    """Stamp provenance and calibration identity onto every generated table.

    Carried as columns rather than left to a DDL default so the Parquet files
    are self-describing too: a `sim_*.parquet` that ends up in the Phase 5 demo
    bundle still says, in its own data, that it is SIMULATED and which
    simulation.yaml version and seed produced it.
    """
    for df in tables.values():
        df["sim_provenance"] = "SIMULATED"
        df["sim_config_version"] = cfg.version
        df["sim_seed"] = np.int64(cfg.seed)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def generate(
    cfg: SimulationConfig | None = None, base: pd.DataFrame | None = None
) -> SimulationResult:
    """Run the full simulation. Same config + same base frame ⇒ same output."""
    cfg = cfg or load_config()
    base = claim_base() if base is None else base.copy()
    # Canonicalize the input order. Draws are positional (the i-th draw goes to
    # the i-th row), so without this the whole simulation would silently depend
    # on how the caller happened to sort the frame — reproducibility that holds
    # only until someone changes an upstream ORDER BY is not reproducibility.
    base = base.sort_values("claim_sk").reset_index(drop=True)
    n = len(base)
    if n == 0:
        raise ValueError("claim base frame is empty — run `make warehouse` inputs first")

    payer_id = _assign_payers(cfg, base)
    service_line = assign_service_line(base["drg_cd"], cfg.service_lines)
    quality = _provider_quality(cfg, base).to_numpy()

    auth = _authorization_eligibility(cfg, base, payer_id, quality)
    doc = _documentation_coding(cfg, base, quality)
    pre = _pre_submission_timeline(cfg, base, payer_id)

    contrib = _mechanism_contributions(cfg, auth, doc, pre)
    discrete_total = np.sum(list(contrib.values()), axis=0)
    continuous = _continuous_terms(cfg, base, payer_id, service_line, quality)
    linear = discrete_total + continuous

    # The calibration target is the OBSERVED denial rate — that is what the
    # benchmarks in docs/assumptions.md are rates of. Symmetric label noise
    # pulls the observed rate toward 0.5 (observed = p·(1-2ε) + ε), so solving
    # the intercept against the target directly would overshoot by ~3.5 points
    # at ε=0.05. Invert the noise first and solve for the latent mean that
    # lands the observed rate on target.
    noise_rate = float(cfg.targets["label_noise"])
    target_rate = float(cfg.targets["overall_denial_rate"]["point"])
    if not noise_rate < target_rate < 1.0 - noise_rate:
        raise ValueError(
            f"label_noise {noise_rate} cannot produce an observed denial rate of {target_rate}"
        )
    latent_target = (target_rate - noise_rate) / (1.0 - 2.0 * noise_rate)
    intercept = solve_intercept(linear, latent_target)
    latent_p = expit(linear + intercept)

    denial_rng = stream(cfg.seed, "denial_outcome")
    drawn = denial_rng.random(n) < latent_p
    # Controlled label noise: symmetric flip, so no model can reach AUC 1.0 by
    # recovering the generator. sim_latent_p stays the PRE-noise probability.
    noise = denial_rng.random(n) < noise_rate
    denied = np.where(noise, ~drawn, drawn)

    # Category conditional on the mechanism that contributed the most log-odds.
    mech_names = sorted(contrib)
    mech_matrix = np.vstack([contrib[m] for m in mech_names])
    strongest_idx = np.argmax(mech_matrix, axis=0)
    strongest_value = mech_matrix[strongest_idx, np.arange(n)]
    driver = np.where(
        strongest_value > 0.0, np.array(mech_names, dtype=object)[strongest_idx], "baseline"
    )
    category_rng = stream(cfg.seed, "denial_category")
    category = _choice_by_group(category_rng, driver, cfg.denial_categories["by_mechanism"])

    partial_rng = stream(cfg.seed, "partial_denial")
    partial = denied & (partial_rng.random(n) < float(cfg.targets["partial_denial_share"]))

    money = _money(cfg, base, payer_id, denied, partial)
    post = _post_submission_timeline(cfg, payer_id, pre["submission_date"], denied, money["paid"])

    adjudication = pd.DataFrame(
        {
            "claim_sk": base["claim_sk"].to_numpy(),
            "clm_id": base["clm_id"].to_numpy(),
            "sim_payer_id": payer_id.to_numpy(),
            "sim_service_line_id": service_line.to_numpy(),
            "sim_coded_date": _to_ts(pre["coded_date"]),
            "sim_submission_date": _to_ts(pre["submission_date"]),
            "sim_ack_date": _to_ts(post["ack_date"]),
            "sim_adjudication_date": _to_ts(post["adjudication_date"]),
            "sim_denial_review_date": _to_ts(post["denial_review_date"]),
            "sim_payment_date": _to_ts(post["payment_date"]),
            "sim_filing_limit_days": pre["filing_limit_days"].astype("int64"),
            "sim_days_service_to_submission": pre["days_service_to_submission"].astype("int64"),
            "sim_late_filing_flag": pre["late_filing"],
            "sim_allowed_amount": money["allowed"],
            "sim_paid_amount": money["paid"],
            "sim_patient_responsibility_amount": money["patient_resp"],
            "sim_contractual_adjustment_amount": money["adjustment"],
            "sim_denied_amount": money["denied_amount"],
            "sim_denial_flag": denied,
            "sim_denial_type": np.where(denied, np.where(partial, "PARTIAL", "FULL"), _DENIAL_NONE),
            "sim_denial_category": np.where(denied, category, None),
            "sim_denial_carc_group": np.where(
                denied, pd.Series(category).map(cfg.carc_group).to_numpy(), None
            ),
            "sim_denial_driver_mechanism": np.where(denied, driver, None),
            "sim_latent_p": np.round(latent_p, 6),
            "sim_label_noise_applied": noise,
            "sim_provider_quality_latent": np.round(quality, 6),
        }
    )
    adjudication["sim_days_to_adjudication"] = (
        adjudication["sim_adjudication_date"] - adjudication["sim_submission_date"]
    ).dt.days.astype("int64")
    adjudication["sim_days_to_payment"] = (
        (adjudication["sim_payment_date"] - adjudication["sim_submission_date"]).dt.days
    ).astype("Int64")

    appeals = _appeals(cfg, adjudication, doc, post["denial_review_date"])
    events = _workflow_events(cfg, adjudication, appeals)
    costs = _operating_costs(cfg, adjudication, events)

    tables = {
        "sim_payer": _payer_dim(cfg),
        "sim_service_line": _service_line_dim(cfg),
        "sim_authorization_eligibility": auth,
        "sim_documentation_coding": doc,
        "sim_claim_adjudication": adjudication,
        "sim_appeals": appeals,
        "sim_workflow_events": events,
        "sim_operating_costs": costs,
    }
    _stamp_provenance(cfg, tables)
    report = _build_report(cfg, base, adjudication, appeals, events, costs, intercept)
    return SimulationResult(tables=tables, report=report, base=base)


def _build_report(
    cfg: SimulationConfig,
    base: pd.DataFrame,
    adjudication: pd.DataFrame,
    appeals: pd.DataFrame,
    events: pd.DataFrame,
    costs: pd.DataFrame,
    intercept: float,
) -> dict[str, object]:
    denied = adjudication["sim_denial_flag"]
    denied_count = int(denied.sum())
    appealed_claims = int(appeals["claim_sk"].nunique()) if not appeals.empty else 0
    overturned_claims = (
        int(appeals.loc[appeals["sim_appeal_outcome"] == "OVERTURNED", "claim_sk"].nunique())
        if not appeals.empty
        else 0
    )
    rework = costs.loc[costs["claim_sk"].isin(adjudication.loc[denied, "claim_sk"])]
    return {
        "config_version": cfg.version,
        "seed": cfg.seed,
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "claims": int(len(base)),
        "solved_denial_intercept": round(float(intercept), 6),
        "denial_rate": round(float(denied.mean()), 6),
        "mean_latent_p": round(float(adjudication["sim_latent_p"].mean()), 6),
        "label_noise_applied": int(adjudication["sim_label_noise_applied"].sum()),
        "partial_denial_share": (
            round(float((adjudication["sim_denial_type"] == "PARTIAL").sum() / denied_count), 6)
            if denied_count
            else 0.0
        ),
        "appeal_rate_of_denied": (
            round(appealed_claims / denied_count, 6) if denied_count else 0.0
        ),
        "appeal_overturn_rate": (
            round(overturned_claims / appealed_claims, 6) if appealed_claims else 0.0
        ),
        "appeal_rows": int(len(appeals)),
        "workflow_events": int(len(events)),
        "mean_denial_rework_cost": round(
            float((rework["sim_denial_rework_cost"] + rework["sim_appeal_cost"]).mean()), 2
        )
        if len(rework)
        else 0.0,
        "mean_total_cost_to_collect": round(float(costs["sim_total_cost_to_collect"].mean()), 2),
        "sum_sim_paid_amount": round(float(adjudication["sim_paid_amount"].sum()), 2),
        "sum_sim_allowed_amount": round(float(adjudication["sim_allowed_amount"].sum()), 2),
        "sum_sim_recovered_amount": round(
            float(appeals["sim_appeal_recovered_amount"].sum()) if not appeals.empty else 0.0, 2
        ),
    }
