"""GATE 1: `config/model.yaml` must agree with the §4.5 firewall document.

CLAUDE.md §4 puts the blacklist in `config/model.yaml`, and §4.5 forbids
ml-engineer from reading `src/simulation/`. Those two rules together mean the
blacklist is a copy of `docs/simulated_forbidden_columns.md`, and a copy drifts.
These tests are the anti-drift check in both directions: a column the document
forbids and the config omits is an unguarded leak, and a column the config
forbids that the document does not name is ml-engineer inventing a boundary it
is not allowed to have an opinion about.

qa-reviewer writes an independent version of this check (GATE 2). Two
implementations of the same assertion is the intent, not duplication to remove.
"""

from __future__ import annotations

import pytest

from src.features.leakage import (
    LeakageError,
    assert_config_agrees_with_doc,
    forbidden_columns,
    load_model_config,
    parse_firewall_doc,
)


@pytest.fixture(scope="module")
def config() -> dict:
    return load_model_config()


@pytest.fixture(scope="module")
def doc():
    return parse_firewall_doc()


def test_config_and_document_agree(config, doc) -> None:
    assert_config_agrees_with_doc(config)


def test_document_parse_is_not_vacuous(doc) -> None:
    """A parser that silently returns nothing would make every check above pass."""
    assert len(doc.latent_internals) == 4, doc.latent_internals
    assert len(doc.model_a_forbidden) >= 25, sorted(doc.model_a_forbidden)
    assert doc.whole_tables == {"sim_appeals", "sim_operating_costs", "sim_workflow_events"}
    assert doc.permitted_model_a, "no permitted columns parsed from §3"


def test_the_answer_keys_are_blocked_for_every_model(config) -> None:
    """§1 latent internals are forbidden in Model A *and* Model C."""
    for column in (
        "sim_latent_p",
        "sim_provider_quality_latent",
        "sim_label_noise_applied",
        "sim_appeal_latent_p",
    ):
        assert column in forbidden_columns("A", config), column
        assert column in forbidden_columns("C", config), column


def test_every_column_of_a_forbidden_table_is_blocked(config) -> None:
    """§2 whole-table forbids have to reach the name-based guard as columns."""
    blocked = forbidden_columns("A", config)
    for table in config["forbidden_tables"]:
        columns = config["forbidden_table_columns"][table]
        assert columns, f"{table} is forbidden wholesale but expands to nothing"
        missing = [c for c in columns if c not in blocked]
        assert not missing, f"{table}: {missing}"


def test_gaps_the_placeholder_config_left_open_are_closed(config) -> None:
    """Regression test for the specific holes GATE 1 exists to fix.

    The pre-Phase-4 `forbidden_features` was a placeholder: five of its eleven
    patterns matched zero real columns, and these columns — a pure answer key
    among them — were unprotected.
    """
    blocked = forbidden_columns("A", config)
    for column in (
        "sim_provider_quality_latent",
        "sim_label_noise_applied",
        "sim_denial_type",
        "sim_denial_carc_group",
        "sim_denial_driver_mechanism",
        "sim_patient_responsibility_amount",
        "sim_contractual_adjustment_amount",
        "sim_denied_amount",
        "sim_ack_date",
        "sim_adjudication_date",
        "sim_denial_review_date",
        "sim_payment_date",
        "sim_days_to_adjudication",
        "sim_days_to_payment",
        "sim_denial_rework_cost",
        "sim_total_cost_to_collect",
    ):
        assert column in blocked, f"{column} is still unprotected"


def test_permitted_columns_are_not_over_blocked(config, doc) -> None:
    """The guard must not be blunt enough to eat §3.

    An over-broad blacklist looks safe and quietly deletes the signal, so the
    document's permitted list is asserted to survive the guard intact.
    """
    from src.features.leakage import assert_no_forbidden_columns

    assert_no_forbidden_columns(doc.permitted_model_a, model="A", config=config)


def test_model_c_has_a_different_boundary(config) -> None:
    """§5: the denial has happened, so post-denial facts are legitimate."""
    model_a = forbidden_columns("A", config)
    model_c = forbidden_columns("C", config)

    for column in (
        "sim_denial_flag",
        "sim_denial_category",
        "sim_denied_amount",
        "sim_allowed_amount",
        "sim_denial_review_date",
    ):
        assert column in model_a, f"{column} must be forbidden pre-submission"
        assert column not in model_c, f"{column} is available to a denials analyst"

    # Everything in sim_appeals except the classification target postdates the
    # decision Model C predicts — including the recovered amount, which is the
    # regression target and never an input.
    for column in (
        "sim_appeal_filed_date",
        "sim_appeal_decision_date",
        "sim_appeal_disputed_amount",
        "sim_appeal_recovered_amount",
        "sim_appeal_level",
        "sim_appeal_latent_p",
    ):
        assert column in model_c, f"{column} must not be a Model C feature"

    # Payment timing postdates the triage moment too.
    assert "sim_payment_date" in model_c
    assert "sim_days_to_payment" in model_c


def test_model_c_targets_are_declared_and_excluded_as_features(config) -> None:
    targets = config["model_c"]["targets"]
    assert targets["classification"] == "sim_appeal_outcome"
    assert targets["regression"] == "sim_appeal_recovered_amount"
    for target in targets.values():
        assert target in forbidden_columns("C", config), f"{target} is a label, not a feature"


def test_drift_is_actually_detected() -> None:
    """The agreement check must fail when the config drifts — not just pass."""
    config = load_model_config()
    config["forbidden_features"] = [
        c for c in config["forbidden_features"] if c != "sim_provider_quality_latent"
    ]
    with pytest.raises(LeakageError, match="sim_provider_quality_latent"):
        assert_config_agrees_with_doc(config)

    config = load_model_config()
    config["forbidden_features"] = [*config["forbidden_features"], "sim_auth_required"]
    with pytest.raises(LeakageError, match="sim_auth_required"):
        assert_config_agrees_with_doc(config)


def test_workflow_events_are_row_filtered_not_dropped(config) -> None:
    """§4: the safe subset is defined by timestamp, not by event type."""
    rule = config["workflow_events"]
    assert rule["boundary_event"] == "CLAIM_SUBMITTED"
    assert "sim_event_ts" in rule["filter"]
    assert set(rule["forbidden_columns"]) == {"sim_event_sk", "sim_appeal_level"}
    # The columns that are safe per row and become answer keys once aggregated
    # over a claim's full history.
    assert set(rule["aggregate_after_filter_only"]) == {"sim_event_seq", "sim_touch_minutes"}
