"""Validation suite for the simulated adjudication layer.

Four families, per CLAUDE.md §7 and the simulation-engineer persona:

  * DIRECTIONAL  — the causal claims the generator makes actually hold in the
    data it produced (missing authorization raises the denial rate, and so on),
    with a required margin so a coincidence cannot pass.
  * DISTRIBUTIONAL — money invariants (paid ≤ allowed ≤ billed, nothing
    negative, the accounting identity closes), marginals inside their configured
    bands, workable class balance.
  * TEMPORAL — every generated date and event timestamp is correctly ordered.
  * REFERENTIAL — one row per claim where the grain says so, no orphans.

Reproducibility (same seed ⇒ byte-identical output) is checked in
tests/simulation/, since it needs two independent runs.

What this suite proves: the generator is internally consistent and did what it
was configured to do. What it does NOT prove: that any of it is realistic. See
docs/assumptions.md §10.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.ingestion.warehouse_sql_checks import CheckResult

from .config import SimulationConfig
from .generator import SimulationResult

_MONEY_COLUMNS = (
    "sim_allowed_amount",
    "sim_paid_amount",
    "sim_patient_responsibility_amount",
    "sim_contractual_adjustment_amount",
    "sim_denied_amount",
)
# Half a cent: rounding to 2dp cannot move a total by more than this.
_CENT = 0.005


def _check(name: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(name, bool(passed), detail)


def _rate(mask: pd.Series | np.ndarray, denial: pd.Series) -> float:
    mask = np.asarray(mask, dtype=bool)
    if mask.sum() == 0:
        return float("nan")
    return float(denial.to_numpy()[mask].mean())


# ---------------------------------------------------------------------------
# Directional validity
# ---------------------------------------------------------------------------
def directional_checks(cfg: SimulationConfig, result: SimulationResult) -> list[CheckResult]:
    adj = result.table("sim_claim_adjudication")
    auth = result.table("sim_authorization_eligibility")
    doc = result.table("sim_documentation_coding")
    denial = adj["sim_denial_flag"]
    margin = float(cfg.validation["directional_min_margin"])
    out: list[CheckResult] = []

    def directional(name: str, high_mask, low_mask) -> None:
        high, low = _rate(high_mask, denial), _rate(low_mask, denial)
        lift = high - low
        out.append(
            _check(
                f"directional:{name}",
                np.isfinite(lift) and lift >= margin,
                f"{high:.4f} vs {low:.4f} (lift {lift:+.4f}, need >= {margin})",
            )
        )

    required = auth["sim_auth_required"].to_numpy()
    missing = auth["sim_auth_missing"].to_numpy()
    directional("auth_missing_raises_denial", required & missing, required & ~missing)
    directional(
        "eligibility_failure_raises_denial",
        auth["sim_eligibility_failed"].to_numpy(),
        ~auth["sim_eligibility_failed"].to_numpy(),
    )
    directional(
        "documentation_deficit_raises_denial",
        ~doc["sim_documentation_complete"].to_numpy(),
        doc["sim_documentation_complete"].to_numpy(),
    )
    directional(
        "late_filing_raises_denial",
        adj["sim_late_filing_flag"].to_numpy(),
        ~adj["sim_late_filing_flag"].to_numpy(),
    )
    directional(
        "duplicate_raises_denial",
        doc["sim_duplicate_submission_flag"].to_numpy(),
        ~doc["sim_duplicate_submission_flag"].to_numpy(),
    )

    # The interaction itself, not just the two main effects: needing an
    # authorization and having it should cost far less than not having it.
    # If this ever fails, the headline mechanism has been calibrated away.
    have = _rate(required & ~missing, denial)
    lack = _rate(required & missing, denial)
    none_required = _rate(~required, denial)
    out.append(
        _check(
            "directional:auth_interaction_dominates_main_effect",
            (lack - have) > abs(have - none_required),
            f"missing-vs-obtained lift {lack - have:+.4f} must exceed "
            f"required-vs-not-required lift {have - none_required:+.4f}",
        )
    )
    return out


# ---------------------------------------------------------------------------
# Distributional validity
# ---------------------------------------------------------------------------
def distribution_checks(cfg: SimulationConfig, result: SimulationResult) -> list[CheckResult]:
    adj = result.table("sim_claim_adjudication")
    appeals = result.table("sim_appeals")
    out: list[CheckResult] = []

    billed = result.base.set_index("claim_sk")["billed_amount"].reindex(adj["claim_sk"]).to_numpy()
    allowed = adj["sim_allowed_amount"].to_numpy()
    paid = adj["sim_paid_amount"].to_numpy()

    out.append(
        _check(
            "distribution:paid_le_allowed",
            bool(np.all(paid <= allowed + _CENT)),
            f"violations={int(np.sum(paid > allowed + _CENT))}",
        )
    )
    out.append(
        _check(
            "distribution:allowed_le_billed",
            bool(np.all(allowed <= billed + _CENT)),
            f"violations={int(np.sum(allowed > billed + _CENT))}",
        )
    )
    for col in _MONEY_COLUMNS:
        values = adj[col].to_numpy()
        out.append(
            _check(
                f"distribution:nonneg:{col}",
                bool(np.all(values >= -_CENT)),
                f"violations={int(np.sum(values < -_CENT))}",
            )
        )
    identity = np.abs(
        allowed
        - paid
        - adj["sim_patient_responsibility_amount"].to_numpy()
        - adj["sim_denied_amount"].to_numpy()
    )
    out.append(
        _check(
            "distribution:allowed_accounting_identity",
            bool(np.all(identity <= 2 * _CENT)),
            f"max residual={float(identity.max()):.4f}",
        )
    )

    latent = adj["sim_latent_p"].to_numpy()
    out.append(
        _check(
            "distribution:latent_p_in_unit_interval",
            bool(np.all((latent > 0.0) & (latent < 1.0))),
            f"min={latent.min():.6f} max={latent.max():.6f}",
        )
    )

    rate = float(adj["sim_denial_flag"].mean())
    target = cfg.targets["overall_denial_rate"]
    out.append(
        _check(
            "distribution:denial_rate_in_band",
            float(target["min"]) <= rate <= float(target["max"]),
            f"{rate:.4f} in [{target['min']}, {target['max']}]",
        )
    )
    out.append(
        _check(
            "distribution:denial_rate_near_target",
            abs(rate - float(target["point"])) <= float(cfg.validation["rate_tolerance"]),
            f"{rate:.4f} vs target {target['point']} (tol {cfg.validation['rate_tolerance']})",
        )
    )
    out.append(
        _check(
            "distribution:class_balance",
            min(rate, 1.0 - rate) >= float(cfg.validation["min_class_share"]),
            f"minority share={min(rate, 1.0 - rate):.4f}",
        )
    )

    unknown_share = float((adj["sim_service_line_id"] == "UNKNOWN").sum() / max(len(adj), 1))
    out.append(
        _check(
            "distribution:unknown_service_line_share",
            unknown_share <= float(cfg.validation["max_unknown_service_line_share"]),
            f"{unknown_share:.4f} <= {cfg.validation['max_unknown_service_line_share']}",
        )
    )

    # Denial category is populated exactly when a claim is denied, and every
    # value is in the configured catalog with a matching CARC group label.
    denied = adj["sim_denial_flag"].to_numpy()
    has_category = adj["sim_denial_category"].notna().to_numpy()
    out.append(
        _check(
            "distribution:category_iff_denied",
            bool(np.array_equal(denied, has_category)),
            f"mismatches={int(np.sum(denied != has_category))}",
        )
    )
    categories = set(adj.loc[denied, "sim_denial_category"].dropna().unique())
    out.append(
        _check(
            "distribution:categories_in_catalog",
            categories.issubset(set(cfg.category_ids)),
            f"unknown={sorted(categories - set(cfg.category_ids))}",
        )
    )
    carc_mismatch = int(
        (
            adj.loc[denied, "sim_denial_category"].map(cfg.carc_group)
            != adj.loc[denied, "sim_denial_carc_group"]
        ).sum()
    )
    out.append(
        _check(
            "distribution:carc_group_matches_category",
            carc_mismatch == 0,
            f"mismatches={carc_mismatch}",
        )
    )

    denied_count = int(denied.sum())
    if denied_count and not appeals.empty:
        appeal_rate = appeals["claim_sk"].nunique() / denied_count
        band = cfg.targets["appeal_rate"]
        out.append(
            _check(
                "distribution:appeal_rate_in_band",
                float(band["min"]) <= appeal_rate <= float(band["max"]),
                f"{appeal_rate:.4f} in [{band['min']}, {band['max']}]",
            )
        )
        overturn = (
            appeals.loc[appeals["sim_appeal_outcome"] == "OVERTURNED", "claim_sk"].nunique()
            / appeals["claim_sk"].nunique()
        )
        oband = cfg.targets["appeal_overturn_rate"]
        out.append(
            _check(
                "distribution:appeal_overturn_rate_in_band",
                float(oband["min"]) <= overturn <= float(oband["max"]),
                f"{overturn:.4f} in [{oband['min']}, {oband['max']}]",
            )
        )
        excess = int(
            (
                appeals["sim_appeal_recovered_amount"]
                > appeals["sim_appeal_disputed_amount"] + _CENT
            ).sum()
        )
        out.append(
            _check("distribution:recovered_le_disputed", excess == 0, f"violations={excess}")
        )
    return out


# ---------------------------------------------------------------------------
# Temporal ordering
# ---------------------------------------------------------------------------
def temporal_checks(result: SimulationResult) -> list[CheckResult]:
    adj = result.table("sim_claim_adjudication")
    appeals = result.table("sim_appeals")
    events = result.table("sim_workflow_events")
    out: list[CheckResult] = []

    anchor = pd.to_datetime(result.base.set_index("claim_sk")["anchor_date"]).reindex(
        adj["claim_sk"]
    )

    def ordered(name: str, earlier: pd.Series, later: pd.Series) -> None:
        both = earlier.notna().to_numpy() & later.notna().to_numpy()
        bad = int(np.sum(later.to_numpy()[both] < earlier.to_numpy()[both]))
        out.append(_check(f"temporal:{name}", bad == 0, f"violations={bad}"))

    ordered("anchor<=coded", pd.Series(anchor.to_numpy()), adj["sim_coded_date"])
    ordered("coded<=submission", adj["sim_coded_date"], adj["sim_submission_date"])
    ordered("submission<=ack", adj["sim_submission_date"], adj["sim_ack_date"])
    ordered("ack<=adjudication", adj["sim_ack_date"], adj["sim_adjudication_date"])
    ordered("adjudication<=payment", adj["sim_adjudication_date"], adj["sim_payment_date"])
    ordered(
        "adjudication<=denial_review", adj["sim_adjudication_date"], adj["sim_denial_review_date"]
    )

    if not appeals.empty:
        ordered(
            "appeal_filed<=decision",
            appeals["sim_appeal_filed_date"],
            appeals["sim_appeal_decision_date"],
        )
        review = adj.set_index("claim_sk")["sim_denial_review_date"].reindex(appeals["claim_sk"])
        ordered(
            "denial_review<=appeal_filed",
            pd.Series(review.to_numpy()),
            appeals["sim_appeal_filed_date"],
        )
        level1 = appeals[appeals["sim_appeal_level"] == 1].set_index("claim_sk")
        level2 = appeals[appeals["sim_appeal_level"] == 2]
        if not level2.empty:
            prior = level1["sim_appeal_decision_date"].reindex(level2["claim_sk"])
            ordered(
                "level1_decision<=level2_filed",
                pd.Series(prior.to_numpy()),
                pd.Series(level2["sim_appeal_filed_date"].to_numpy()),
            )

    # The event log is the artifact process mining reads, so its ordering has to
    # be exact rather than approximately right.
    ts = events.sort_values(["claim_sk", "sim_event_seq"])
    gaps = ts.groupby("claim_sk")["sim_event_ts"].diff()
    bad_ts = int((gaps.dropna() <= pd.Timedelta(0)).sum())
    out.append(_check("temporal:event_ts_strictly_increasing", bad_ts == 0, f"violations={bad_ts}"))
    seq_ok = ts.groupby("claim_sk")["sim_event_seq"].apply(
        lambda s: list(s) == list(range(1, len(s) + 1))
    )
    out.append(
        _check(
            "temporal:event_seq_contiguous_from_1",
            bool(seq_ok.all()),
            f"claims with broken sequence={int((~seq_ok).sum())}",
        )
    )
    return out


# ---------------------------------------------------------------------------
# Referential integrity between the generated tables
# ---------------------------------------------------------------------------
def referential_checks(result: SimulationResult) -> list[CheckResult]:
    adj = result.table("sim_claim_adjudication")
    claim_keys = set(adj["claim_sk"].tolist())
    out: list[CheckResult] = []

    for name in (
        "sim_authorization_eligibility",
        "sim_documentation_coding",
        "sim_operating_costs",
    ):
        table = result.table(name)
        out.append(
            _check(
                f"referential:one_row_per_claim:{name}",
                len(table) == len(adj) and set(table["claim_sk"].tolist()) == claim_keys,
                f"{len(table)} rows vs {len(adj)} claims",
            )
        )
    out.append(
        _check(
            "referential:adjudication_claim_sk_unique",
            adj["claim_sk"].is_unique,
            f"duplicates={int(len(adj) - adj['claim_sk'].nunique())}",
        )
    )

    denied_keys = set(adj.loc[adj["sim_denial_flag"], "claim_sk"].tolist())
    appeals = result.table("sim_appeals")
    if not appeals.empty:
        orphan = set(appeals["claim_sk"].tolist()) - denied_keys
        out.append(
            _check(
                "referential:appeals_only_for_denied_claims",
                not orphan,
                f"appeals on non-denied claims={len(orphan)}",
            )
        )
        dup = int(len(appeals) - len(appeals.drop_duplicates(["claim_sk", "sim_appeal_level"])))
        out.append(_check("referential:appeal_grain_unique", dup == 0, f"duplicates={dup}"))

    events = result.table("sim_workflow_events")
    orphan_events = set(events["claim_sk"].tolist()) - claim_keys
    out.append(
        _check(
            "referential:no_orphan_workflow_events",
            not orphan_events,
            f"orphans={len(orphan_events)}",
        )
    )
    covered = set(events["claim_sk"].tolist())
    out.append(
        _check(
            "referential:every_claim_has_events",
            covered == claim_keys,
            f"claims without events={len(claim_keys - covered)}",
        )
    )

    payer_ids = set(result.table("sim_payer")["sim_payer_id"].tolist())
    out.append(
        _check(
            "referential:payer_id_in_dimension",
            set(adj["sim_payer_id"].tolist()).issubset(payer_ids),
            f"unknown={sorted(set(adj['sim_payer_id'].tolist()) - payer_ids)}",
        )
    )
    sl_ids = set(result.table("sim_service_line")["sim_service_line_id"].tolist())
    out.append(
        _check(
            "referential:service_line_id_in_dimension",
            set(adj["sim_service_line_id"].tolist()).issubset(sl_ids),
            f"unknown={sorted(set(adj['sim_service_line_id'].tolist()) - sl_ids)}",
        )
    )
    return out


# ---------------------------------------------------------------------------
# Provenance hygiene
# ---------------------------------------------------------------------------
# Latent generator internals. Stored so the simulation can be validated, never
# usable as a model feature (CLAUDE.md §4). Published in
# docs/simulated_forbidden_columns.md so ml-engineer can copy the list into
# config/model.yaml WITHOUT reading src/simulation/ (§4.5).
LATENT_ONLY_COLUMNS = (
    "sim_latent_p",
    "sim_provider_quality_latent",
    "sim_appeal_latent_p",
)


def provenance_checks(result: SimulationResult) -> list[CheckResult]:
    """Every generated column carries the sim_ prefix, except the join keys.

    `claim_sk` and `clm_id` are deliberately exempt: they are the DERIVED
    surrogate and the SOURCE degenerate key from the warehouse, not values this
    layer invented, and renaming them would misrepresent their provenance.
    """
    exempt = {"claim_sk", "clm_id"}
    out: list[CheckResult] = []
    for name, table in sorted(result.tables.items()):
        offenders = [c for c in table.columns if c not in exempt and not c.startswith("sim_")]
        out.append(
            _check(
                f"provenance:sim_prefix:{name}",
                not offenders,
                f"unprefixed={offenders}",
            )
        )
        out.append(
            _check(
                f"provenance:table_name_prefix:{name}",
                name.startswith("sim_"),
                f"table={name}",
            )
        )
    adj = result.table("sim_claim_adjudication")
    present = [
        c
        for c in LATENT_ONLY_COLUMNS
        if c in adj.columns or c in result.table("sim_appeals").columns
    ]
    out.append(
        _check(
            "provenance:latent_columns_present_for_validation",
            set(present) == set(LATENT_ONLY_COLUMNS),
            f"present={present}",
        )
    )
    return out


def run_validation(cfg: SimulationConfig, result: SimulationResult) -> list[CheckResult]:
    """The full suite. Any failure fails `make simulate`."""
    return (
        directional_checks(cfg, result)
        + distribution_checks(cfg, result)
        + temporal_checks(result)
        + referential_checks(result)
        + provenance_checks(result)
    )
