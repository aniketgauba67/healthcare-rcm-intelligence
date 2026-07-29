"""Frame shaping for the API, kept separate from the routes that serve it.

Every function here takes a frame and returns the table (or record) that goes on
the wire, and none of them touch FastAPI. That split is deliberate and it is the
contract `tests/leakage/test_output_surface_provenance.py` asks for: a user-facing
emitter is probed by perturbing a simulated input and checking that no emitted
column moves without carrying a `sim_` marker, and that is only possible when the
frame-building step is a function you can call.

WHERE THIS LAYER RE-MARKS A COLUMN, AND WHY
--------------------------------------------
`rcm.vw_work_queue_priority` emits `dollars_at_stake`, `heuristic_priority_score`
and `action_type` with no `sim_` marker, and all three are derived from simulated
values. That is the [QUEUE-PREFIX] defect one layer upstream, in a view this agent
does not own. It is reported to team-lead rather than fixed here — but it is also
not passed through unmarked, because this module IS the last boundary before a
reader. So the marker is restored on the way out and `RE_MARKED_COLUMNS` records
every rename, so the disagreement between the warehouse's spelling and the wire's
is visible rather than quietly introduced. Delete these entries the day the view
is corrected.

`action_type` was on `PROCESS_METADATA_COLUMNS` until ml-engineer-7 measured what
builds it: `vw_work_queue_priority:78-82` is a CASE on `sim_denial_flag`, so the
column IS the label wearing a process name, and it is now forbidden as a feature
in `config/model.yaml`. A rank or a recommendation is a statement about our
process; a case statement on the outcome is a statement about the outcome.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

#: Columns arriving from a curated view WITHOUT the `sim_` marker that §3.2
#: requires, re-marked here at the last boundary. Each entry is a finding against
#: the view, not a preference: the value is derived from simulated money or, in
#: `action_type`'s case, from the simulated denial label itself.
RE_MARKED_COLUMNS: dict[str, str] = {
    "dollars_at_stake": "sim_dollars_at_stake",
    "heuristic_priority_score": "sim_heuristic_priority_score",
    "action_type": "sim_action_type",
}

#: Statements about OUR process rather than about the simulated world, in the
#: sense the exposure probe uses: a rank, a tier, a recommendation, a query
#: parameter. These legitimately carry no `sim_` marker. `action_type` is NOT
#: here — see the module docstring.
PROCESS_METADATA_COLUMNS: frozenset[str] = frozenset(
    {
        "tier",
        "tier_rank",
        "queue_position",
        "recommended_action",
        "priority_tier",
        "queue_mode",
        "as_of",
        "is_heuristic_placeholder",
    }
)


def re_mark_simulated_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore the `sim_` marker on columns a curated view dropped it from."""
    present = {old: new for old, new in RE_MARKED_COLUMNS.items() if old in frame.columns}
    return frame.rename(columns=present) if present else frame


# ---------------------------------------------------------------------------
# GET /work-queue
# ---------------------------------------------------------------------------


def build_work_queue_table(
    queue: pd.DataFrame,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> pd.DataFrame:
    """The Expected Net Recovery worklist, in the order the shipped queue ranks it.

    Ordered by `queue_position` — the tiered order Model C ships — and never
    re-sorted by expected net recovery, because the tiers are the guarantee. A
    deadline-critical claim outranks a larger one by policy, and re-sorting on
    the dollar column here would silently undo that at the last step.
    """
    frame = re_mark_simulated_columns(queue)
    if tier:
        frame = frame.loc[frame["tier"].astype(str).str.upper() == tier.upper()]
    sort_columns = [c for c in ("tier_rank", "queue_position") if c in frame.columns]
    if sort_columns:
        frame = frame.sort_values(sort_columns, kind="mergesort")
    return frame.iloc[offset : offset + limit].reset_index(drop=True)


def build_heuristic_queue_table(
    queue: pd.DataFrame, limit: int = 50, offset: int = 0
) -> pd.DataFrame:
    """The rule-based scaffold queue, for a deployment with no model artifacts.

    Every row carries `is_heuristic_placeholder`; the view is explicit that this
    is dollars weighted by age with no learned weights, and the endpoint says so
    in `ranking` rather than letting a caller mistake it for a model score.
    """
    frame = re_mark_simulated_columns(queue)
    if "sim_heuristic_priority_score" in frame.columns:
        frame = frame.sort_values("sim_heuristic_priority_score", ascending=False, kind="mergesort")
    return frame.iloc[offset : offset + limit].reset_index(drop=True)


def tier_counts(queue: pd.DataFrame) -> dict[str, int]:
    column = "tier" if "tier" in queue.columns else "priority_tier"
    if column not in queue.columns:
        return {}
    counts = queue[column].astype(str).value_counts()
    return {str(name): int(value) for name, value in counts.items()}


# ---------------------------------------------------------------------------
# GET /claims/{claim_id}
# ---------------------------------------------------------------------------

#: Crosswalked real-facility columns. Separated into their own block in the
#: response so a consumer cannot mistake them for claim attributes: they are a
#: seeded random assignment, display-only, and 8:1 collided at worst.
_FACILITY_DISPLAY_COLUMNS = (
    "sim_facility_ccn",
    "sim_facility_name",
    "sim_facility_state",
    "sim_facility_type",
)


def build_claim_record(row: pd.Series) -> dict[str, Any]:
    """One claim, with the display-only crosswalk fields held apart from the claim."""
    record = {key: _json_safe(value) for key, value in row.items()}
    facility = {name: record.pop(name, None) for name in _FACILITY_DISPLAY_COLUMNS}
    return {"claim": record, "facility_display": facility}


def _json_safe(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.date().isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return value
    if pd.isna(value) if not isinstance(value, (list, dict, tuple)) else False:
        return None
    return value


# ---------------------------------------------------------------------------
# GET /metrics/executive
# ---------------------------------------------------------------------------

#: Measures that sum across months, and measures that must be recomputed from
#: their own numerator and denominator. Averaging a rate over months weights a
#: 40-claim month like a 400-claim one, which is how a book-level denial rate
#: quietly becomes a different number from the one the control query returns.
_SUMMABLE = (
    "claims_submitted",
    "denied_claims",
    "clean_claims",
    "first_pass_paid_claims",
    "billed_charge_amt",
    "medicare_source_paid_amt",
    "sim_allowed_amt",
    "sim_paid_amt",
    "sim_denied_amt",
    "sim_appeal_recovered_amt",
    "sim_cost_to_collect",
    "claims_appealed",
    "claims_overturned",
)


def build_executive_summary(monthly: pd.DataFrame) -> dict[str, Any]:
    """Book-level KPIs rolled up from the monthly executive view.

    Rolled up from the view rather than recomputed from claims, so the totals are
    the view's totals by construction — which is what makes the §7 reconciliation
    ("dashboard totals reconcile to SQL control queries") a property of the code
    rather than a thing somebody checked once.
    """
    totals = {
        name: float(monthly[name].fillna(0).sum()) for name in _SUMMABLE if name in monthly.columns
    }
    claims = totals.get("claims_submitted", 0.0)
    appealed = totals.get("claims_appealed", 0.0)

    def rate(numerator: str, denominator: float) -> float:
        return round(totals.get(numerator, 0.0) / denominator, 6) if denominator else 0.0

    payments = monthly.get("avg_days_to_payment")
    weights = monthly.get("claims_submitted")
    if payments is not None and weights is not None:
        mask = payments.notna()
        avg_days = (
            float((payments[mask] * weights[mask]).sum() / weights[mask].sum())
            if mask.any() and weights[mask].sum()
            else 0.0
        )
    else:
        avg_days = 0.0

    months = monthly["submission_year_month"].astype(str)
    return {
        "period_from": months.min(),
        "period_to": months.max(),
        "claims_submitted": int(claims),
        "denied_claims": int(totals.get("denied_claims", 0.0)),
        "denial_rate": rate("denied_claims", claims),
        "clean_claim_rate": rate("clean_claims", claims),
        "first_pass_paid_rate": rate("first_pass_paid_claims", claims),
        "sim_net_collection_rate": (
            round(totals.get("sim_paid_amt", 0.0) / totals["sim_allowed_amt"], 6)
            if totals.get("sim_allowed_amt")
            else 0.0
        ),
        "source_billed_charge_amt": round(totals.get("billed_charge_amt", 0.0), 2),
        "sim_allowed_amt": round(totals.get("sim_allowed_amt", 0.0), 2),
        "sim_paid_amt": round(totals.get("sim_paid_amt", 0.0), 2),
        "sim_denied_amt": round(totals.get("sim_denied_amt", 0.0), 2),
        "sim_cost_to_collect": round(totals.get("sim_cost_to_collect", 0.0), 2),
        "avg_days_to_payment": round(avg_days, 2),
        "claims_appealed": int(appealed),
        "appeal_overturn_rate": (
            round(totals.get("claims_overturned", 0.0) / appealed, 6) if appealed else 0.0
        ),
    }


def executive_control_totals(monthly: pd.DataFrame) -> dict[str, Any]:
    """The control query this response must satisfy, returned with the answer.

    `sql/quality/view_reconciliation.py` asserts
    `sum(claims_submitted) == count(*) from fact_inpatient_claim` and
    `sum(denied_claims) == count(*) from sim_claim_adjudication where sim_denial_flag`.
    Restating the totals beside the KPIs lets a caller reconcile without a
    database, which is the only form of the check a hosted demo can offer.
    """
    return {
        "control_query": (
            "select sum(claims_submitted), sum(denied_claims) from rcm.vw_executive_rcm_summary"
        ),
        "sum_claims_submitted": int(monthly["claims_submitted"].fillna(0).sum()),
        "sum_denied_claims": int(monthly["denied_claims"].fillna(0).sum()),
        "months": int(len(monthly)),
        "note": (
            "These must equal count(*) from rcm.fact_inpatient_claim and count(*) from "
            "rcm.sim_claim_adjudication where sim_denial_flag respectively "
            "(sql/quality/view_reconciliation.py)."
        ),
    }
