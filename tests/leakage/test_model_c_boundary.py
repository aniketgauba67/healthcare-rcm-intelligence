"""Model C's boundary is the DENIAL, and that is the subtlest rule in Phase 4.

Model A's guard is easy to reason about: nothing after submission. Model C's is
not, because a denials analyst opening a worklist legitimately sees the
remittance advice. Widening the guard is therefore the correct thing to do and
also the easiest place in this project to widen it one column too far.

Every assertion below is about a column that is nearly-but-not-quite on the
right side of that line, because the columns that are obviously wrong were never
the risk.
"""

from __future__ import annotations

import pytest

from src.features.leakage import (
    LeakageError,
    assert_no_forbidden_columns,
    forbidden_columns,
    load_model_config,
)

# What the remittance advice actually carries: what was denied, how much, under
# which category and CARC group, and when the payer adjudicated it.
_REMITTANCE = (
    "sim_denial_flag",
    "sim_denial_type",
    "sim_denial_category",
    "sim_denial_carc_group",
    "sim_denied_amount",
    "sim_allowed_amount",
    "sim_paid_amount",
    "sim_patient_responsibility_amount",
    "sim_contractual_adjustment_amount",
    "sim_adjudication_date",
    "sim_denial_review_date",
    "sim_days_to_adjudication",
)


@pytest.mark.parametrize("column", _REMITTANCE)
def test_the_remittance_advice_is_available_to_model_c(column: str) -> None:
    """Forbidden to A, permitted to C. If this fails, the boundary collapsed onto A's."""
    with pytest.raises(LeakageError):
        assert_no_forbidden_columns([column], model="A")
    assert_no_forbidden_columns([column], model="C")


def test_the_denial_mechanism_stays_forbidden_to_model_c() -> None:
    """sim_denial_driver_mechanism is the generator's account of WHY.

    It sits next to the denial category in the schema and is forbidden while the
    category is permitted, which is exactly why it needs its own test. A category
    is a CARC group on a remittance advice that a human being can read. The
    driver mechanism is the simulation's internal statement of what caused the
    denial, appears on no remittance advice anyone has ever worked, and admitting
    it would invert the CLAUDE.md §4.5 firewall through a column name.
    """
    with pytest.raises(LeakageError, match="sim_denial_driver_mechanism"):
        assert_no_forbidden_columns(["sim_denial_driver_mechanism"], model="C")


@pytest.mark.parametrize(
    "column",
    [
        "sim_appeal_sk",
        "sim_appeal_level",
        "sim_appeal_filed_date",
        "sim_appeal_decision_date",
        "sim_appeal_outcome",
        "sim_appeal_disputed_amount",
        "sim_appeal_recovered_amount",
        "sim_appeal_latent_p",
    ],
)
def test_no_appeal_column_is_a_model_c_feature(column: str) -> None:
    """Every sim_appeals column postdates the decision being predicted.

    Including the two that are targets: a target is not a feature, and the only
    thing standing between those two roles is this guard.
    """
    with pytest.raises(LeakageError, match=column):
        assert_no_forbidden_columns([column], model="C")


def test_the_disputed_amount_stays_out_and_the_denied_amount_is_the_substitute() -> None:
    """The two coincide numerically; they do not coincide in time.

    sim_appeal_disputed_amount equals sim_denied_amount on all 967 level-1
    appeals — verified against live Postgres, max absolute difference 0.00 — so
    the recoverable amount the Expected Net Recovery score needs is available
    from the permitted column. That is not a workaround. The denied amount is on
    the remittance advice at triage time; the disputed amount is a fact about an
    appeal nobody has filed yet, and reading it would mean the score could only
    be computed for claims that were going to be appealed anyway.
    """
    with pytest.raises(LeakageError, match="sim_appeal_disputed_amount"):
        assert_no_forbidden_columns(["sim_appeal_disputed_amount"], model="C")
    assert_no_forbidden_columns(["sim_denied_amount"], model="C")


@pytest.mark.parametrize(
    "column",
    [
        "sim_latent_p",
        "sim_provider_quality_latent",
        "sim_label_noise_applied",
        "sim_appeal_latent_p",
    ],
)
def test_the_latent_internals_are_forbidden_to_every_model(column: str) -> None:
    """§1 of the firewall document does not relax at any boundary."""
    for model in ("A", "C"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model=model)


def test_the_payment_is_still_forbidden_after_the_denial() -> None:
    """A payment posting after an appeal IS the appeal's result."""
    for column in ("sim_payment_date", "sim_days_to_payment"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model="C")


def test_model_c_relaxes_the_guard_and_does_not_replace_it() -> None:
    """C's blacklist must be A's minus the remittance, not a shorter list of its own.

    Written as a set relation rather than a count so that a future column added
    to A's list is automatically covered here: the only permitted difference
    between the two blacklists is the configured `permitted_beyond_model_a`.
    """
    config = load_model_config()
    relaxed = forbidden_columns("A", config) - forbidden_columns("C", config)
    assert relaxed == set(config["model_c"]["permitted_beyond_model_a"]), (
        "Model C's guard differs from Model A's by something other than the configured "
        f"post-denial facts: unexpected relaxations {sorted(relaxed - set(config['model_c']['permitted_beyond_model_a']))}"
    )


def test_the_crosswalk_stays_forbidden_to_model_c_too() -> None:
    """Display-only linkage is display-only at every boundary (CLAUDE.md §3.4)."""
    for column in ("sim_facility_ccn", "facility_ccn", "real_npi"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model="C")
