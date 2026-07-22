"""Directional, distributional, temporal and provenance validity of the layer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.simulation.base import assign_service_line, solve_intercept
from src.simulation.generator import generate
from src.simulation.validate import (
    LATENT_ONLY_COLUMNS,
    directional_checks,
    distribution_checks,
    provenance_checks,
    referential_checks,
    run_validation,
    temporal_checks,
)


def _failures(checks):
    return [f"{c.name}: {c.detail}" for c in checks if not c.passed]


def test_full_validation_suite_passes(cfg, result):
    checks = run_validation(cfg, result)
    assert checks, "validation suite produced no checks"
    assert not _failures(checks), _failures(checks)


@pytest.mark.parametrize(
    "family",
    [directional_checks, distribution_checks],
)
def test_config_dependent_families_pass(cfg, result, family):
    assert not _failures(family(cfg, result))


@pytest.mark.parametrize("family", [temporal_checks, referential_checks, provenance_checks])
def test_standalone_families_pass(result, family):
    assert not _failures(family(result))


def test_missing_authorization_raises_denial_rate(result):
    """The headline mechanism, asserted directly rather than only via the suite."""
    adj = result.table("sim_claim_adjudication")
    auth = result.table("sim_authorization_eligibility")
    required = auth["sim_auth_required"].to_numpy()
    missing = auth["sim_auth_missing"].to_numpy()
    denial = adj["sim_denial_flag"].to_numpy()
    assert denial[required & missing].mean() > denial[required & ~missing].mean() + 0.05


def test_latent_probability_is_never_a_derived_output_column(result):
    """Latent generator internals live in exactly one place each.

    If a latent value were ever copied into a second column, a feature pipeline
    could pick up the copy while the forbidden list only named the original.
    """
    for table_name, df in result.tables.items():
        latent_here = [c for c in df.columns if c in LATENT_ONLY_COLUMNS]
        if table_name == "sim_claim_adjudication":
            assert set(latent_here) == {"sim_latent_p", "sim_provider_quality_latent"}
        elif table_name == "sim_appeals":
            assert set(latent_here) == {"sim_appeal_latent_p"}
        else:
            assert not latent_here, f"{table_name} carries latent columns {latent_here}"


def test_every_column_is_sim_prefixed_except_warehouse_keys(result):
    for name, df in result.tables.items():
        assert name.startswith("sim_")
        offenders = [
            c for c in df.columns if c not in {"claim_sk", "clm_id"} and not c.startswith("sim_")
        ]
        assert not offenders, f"{name}: {offenders}"


def test_money_invariants_hold_against_source_billed(result):
    adj = result.table("sim_claim_adjudication")
    billed = result.base.set_index("claim_sk")["billed_amount"].reindex(adj["claim_sk"]).to_numpy()
    assert (adj["sim_paid_amount"] <= adj["sim_allowed_amount"] + 0.005).all()
    assert (adj["sim_allowed_amount"].to_numpy() <= billed + 0.005).all()
    assert (adj[["sim_allowed_amount", "sim_paid_amount", "sim_denied_amount"]] >= 0).all().all()


def test_full_denials_pay_nothing_and_paid_claims_deny_nothing(result):
    adj = result.table("sim_claim_adjudication")
    full = adj[adj["sim_denial_type"] == "FULL"]
    assert (full["sim_paid_amount"] == 0).all()
    clean = adj[~adj["sim_denial_flag"]]
    assert (clean["sim_denied_amount"] == 0).all()


def test_observed_denial_rate_hits_the_target_despite_label_noise(cfg, result):
    """Label noise pulls the observed rate toward 0.5; the intercept solve
    inverts it, so the CONFIGURED rate is the one that actually shows up."""
    observed = float(result.table("sim_claim_adjudication")["sim_denial_flag"].mean())
    target = float(cfg.targets["overall_denial_rate"]["point"])
    assert abs(observed - target) <= float(cfg.validation["rate_tolerance"]) * 2


def test_label_noise_actually_flips_labels(cfg, result):
    adj = result.table("sim_claim_adjudication")
    flipped = float(adj["sim_label_noise_applied"].mean())
    assert abs(flipped - float(cfg.targets["label_noise"])) < 0.02


def test_denial_category_is_conditional_on_the_driving_mechanism(result):
    """A category drawn independently of the cause would carry no information."""
    adj = result.table("sim_claim_adjudication")
    denied = adj[adj["sim_denial_flag"]]
    auth_driven = denied[denied["sim_denial_driver_mechanism"] == "authorization_missing"]
    other = denied[denied["sim_denial_driver_mechanism"] != "authorization_missing"]
    if len(auth_driven) < 20:
        pytest.skip("too few authorization-driven denials in this sample")
    share_auth = (auth_driven["sim_denial_category"] == "PRIOR_AUTH_MISSING").mean()
    share_other = (other["sim_denial_category"] == "PRIOR_AUTH_MISSING").mean()
    assert share_auth > 0.5
    assert share_auth > share_other + 0.3


def test_appeals_exist_only_for_denied_claims_and_recover_at_most_the_dispute(result):
    adj = result.table("sim_claim_adjudication")
    appeals = result.table("sim_appeals")
    denied = set(adj.loc[adj["sim_denial_flag"], "claim_sk"])
    assert set(appeals["claim_sk"]).issubset(denied)
    assert (appeals["sim_appeal_recovered_amount"] <= appeals["sim_appeal_disputed_amount"]).all()
    upheld = appeals[appeals["sim_appeal_outcome"] == "UPHELD"]
    assert (upheld["sim_appeal_recovered_amount"] == 0).all()


def test_level_two_appeals_follow_a_level_one_that_was_upheld(result):
    appeals = result.table("sim_appeals")
    level2 = appeals[appeals["sim_appeal_level"] == 2]
    if level2.empty:
        pytest.skip("no level-2 appeals in this sample")
    level1 = appeals[appeals["sim_appeal_level"] == 1].set_index("claim_sk")
    prior = level1.reindex(level2["claim_sk"])
    assert (prior["sim_appeal_outcome"] == "UPHELD").all()
    assert (
        level2["sim_appeal_filed_date"].to_numpy() >= prior["sim_appeal_decision_date"].to_numpy()
    ).all()


def test_workflow_events_are_strictly_ordered_within_a_claim(result):
    events = result.table("sim_workflow_events").sort_values(["claim_sk", "sim_event_seq"])
    gaps = events.groupby("claim_sk")["sim_event_ts"].diff().dropna()
    assert (gaps > pd.Timedelta(0)).all()


def test_every_claim_starts_with_coding_and_ends_closed(result):
    events = result.table("sim_workflow_events").sort_values(["claim_sk", "sim_event_seq"])
    first = events.groupby("claim_sk")["sim_event_type"].first()
    last = events.groupby("claim_sk")["sim_event_type"].last()
    assert (first == "CODING_COMPLETE").all()
    assert (last == "CLAIM_CLOSED").all()


def test_operating_costs_reconcile_to_the_event_log(result):
    events = result.table("sim_workflow_events")
    costs = result.table("sim_operating_costs").set_index("claim_sk")
    minutes = events.groupby("claim_sk")["sim_touch_minutes"].sum()
    merged = costs["sim_touch_minutes_total"].reindex(minutes.index)
    assert np.allclose(merged.to_numpy(), minutes.to_numpy(), atol=0.05)
    components = costs[
        [
            "sim_coding_cost",
            "sim_submission_cost",
            "sim_payment_posting_cost",
            "sim_denial_rework_cost",
            "sim_appeal_cost",
        ]
    ].sum(axis=1)
    assert np.allclose(
        components.to_numpy(), costs["sim_total_cost_to_collect"].to_numpy(), atol=0.02
    )


def test_service_line_buckets_are_contiguous_and_total(cfg):
    """Every numeric DRG lands in exactly one bucket, so UNKNOWN can only ever
    mean "the source had no DRG" — never "fell in a gap between ranges"."""
    ranged = sorted((s for s in cfg.service_lines if s.lo is not None), key=lambda s: s.lo)
    for earlier, later in zip(ranged, ranged[1:]):
        assert later.lo == earlier.hi + 1, f"gap between {earlier.id} and {later.id}"

    codes = pd.Series([str(v) for v in range(1, 1000)], dtype="string")
    assigned = assign_service_line(codes, cfg.service_lines)
    assert not (assigned == "UNKNOWN").any()
    assert assign_service_line(pd.Series([None], dtype="string"), cfg.service_lines)[0] == "UNKNOWN"


def test_solve_intercept_hits_the_requested_mean():
    linear = np.linspace(-3.0, 3.0, 500)
    for target in (0.05, 0.13, 0.5, 0.9):
        from src.simulation.base import expit

        c = solve_intercept(linear, target)
        assert abs(float(expit(linear + c).mean()) - target) < 1e-6


def test_generator_rejects_a_target_rate_unreachable_under_the_noise(cfg, base):
    """Symmetric noise at rate ε bounds the observable rate to [ε, 1-ε]. Asking
    for something outside that is a config error, not something to silently
    approximate."""
    import copy

    broken = copy.deepcopy(cfg)
    broken.targets = {
        **cfg.targets,
        "label_noise": 0.20,
        "overall_denial_rate": {**cfg.targets["overall_denial_rate"], "point": 0.10},
    }
    with pytest.raises(ValueError, match="label_noise"):
        generate(broken, base)
