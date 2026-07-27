"""The guard that stands between the feature store and a training matrix.

CLAUDE.md §4.1: the build fails if any forbidden column *or a column derived
from one* enters a training matrix. Two mechanisms, tested here:

* a name check, which catches a forbidden column passed through — renamed or not;
* a lineage check, which catches an engineered feature whose declared source
  columns include something forbidden. This one is the real protection, because
  `payer_prior_denial_rate` is a perfectly innocent-looking name.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.features.leakage import (
    LeakageError,
    assert_no_forbidden_columns,
    assert_sources_safe,
    forbidden_columns,
)

# A plausible pre-submission matrix: payer, authorization, documentation and
# coding facts, plus claim size. Nothing here is knowable only after submission.
_CLEAN_MATRIX_COLUMNS = [
    "sim_payer_id",
    "sim_service_line_id",
    "sim_auth_required",
    "sim_auth_missing",
    "sim_auth_obtained_late",
    "sim_eligibility_checked",
    "sim_eligibility_failed",
    "sim_secondary_payer_present",
    "sim_documentation_complete",
    "sim_documentation_score",
    "sim_coder_query_outstanding",
    "sim_coding_specificity_deficit",
    "sim_coding_complexity_score",
    "sim_duplicate_submission_flag",
    "sim_late_filing_flag",
    "sim_days_service_to_submission",
    "sim_filing_limit_days",
    "billed_charge_amt",
    "length_of_stay_days",
    "diagnosis_count",
    "drg_cd",
    "discharge_status_cd",
]


def test_a_clean_matrix_passes() -> None:
    frame = pd.DataFrame(columns=_CLEAN_MATRIX_COLUMNS)
    assert_no_forbidden_columns(frame.columns, model="A")


@pytest.mark.parametrize(
    "column",
    [
        "sim_denial_flag",  # the label
        "sim_latent_p",  # the answer key
        "sim_provider_quality_latent",  # the other answer key
        "sim_denied_amount",  # equivalent to the label
        "sim_paid_amount",
        "sim_adjudication_date",
        "sim_denial_review_date",  # non-null iff denied
        "sim_appeal_recovered_amount",
        "sim_denial_rework_cost",  # > 0 implies a denial
        "sim_total_cost_to_collect",
        "sim_appeal_level",
        "sim_event_sk",
        "claim_sk",  # assigned in source order: a smuggled date
        "clm_id",
        "sim_seed",
        "clean_claim_flag",  # DERIVED view column built on the label
        "first_pass_paid_flag",
        "ar_open_flag",
        "medicare_source_paid_amt",  # SOURCE, but still an adjudication output
        "facility_ccn",  # simulated display-only linkage
    ],
)
def test_forbidden_column_is_rejected(column: str) -> None:
    frame = pd.DataFrame(columns=[*_CLEAN_MATRIX_COLUMNS, column])
    with pytest.raises(LeakageError, match=column):
        assert_no_forbidden_columns(frame.columns, model="A")


@pytest.mark.parametrize(
    "column",
    ["log_sim_paid_amount", "sim_denied_amount_bucket", "sim_denial_flag_lag1"],
)
def test_a_renamed_forbidden_column_is_still_rejected(column: str) -> None:
    """Wrapping a forbidden column in a transform does not launder it."""
    with pytest.raises(LeakageError):
        assert_no_forbidden_columns([*_CLEAN_MATRIX_COLUMNS, column], model="A")


def test_lineage_guard_catches_an_innocent_looking_name() -> None:
    """The name says nothing; the declared sources say everything."""
    specs = {
        "payer_prior_period_denial_rate": ["sim_payer_id", "sim_submission_date"],
        "provider_clean_claim_rate": ["prvdr_num", "sim_denial_flag"],
    }
    with pytest.raises(LeakageError, match="provider_clean_claim_rate"):
        assert_sources_safe(specs, model="A")


def test_lineage_guard_accepts_pre_submission_sources() -> None:
    specs = {
        "auth_missing": ["sim_auth_required", "sim_auth_obtained"],
        "days_service_to_submission": ["sim_days_service_to_submission"],
        "claim_size_band": ["billed_charge_amt"],
    }
    assert_sources_safe(specs, model="A")


def test_model_c_matrix_may_see_the_denial() -> None:
    """The Model A guard would reject this matrix; the Model C guard must not."""
    triage_columns = [
        *_CLEAN_MATRIX_COLUMNS,
        "sim_denial_category",
        "sim_denial_carc_group",
        "sim_denied_amount",
        "sim_allowed_amount",
        "sim_days_to_adjudication",
    ]
    with pytest.raises(LeakageError):
        assert_no_forbidden_columns(triage_columns, model="A")
    assert_no_forbidden_columns(triage_columns, model="C")


def test_model_c_matrix_may_not_see_the_appeal_or_the_mechanism() -> None:
    for column in (
        "sim_appeal_filed_date",
        "sim_appeal_latent_p",
        "sim_denial_driver_mechanism",
        "sim_payment_date",
    ):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([*_CLEAN_MATRIX_COLUMNS, column], model="C")


def test_error_names_the_offender_and_points_at_the_rule() -> None:
    """A leakage failure has to be actionable without reading this module."""
    with pytest.raises(LeakageError) as excinfo:
        assert_no_forbidden_columns(["sim_latent_p"], model="A")
    message = str(excinfo.value)
    assert "sim_latent_p" in message
    assert "CLAUDE.md" in message
    assert "simulated_forbidden_columns.md" in message


def test_blacklist_is_not_empty() -> None:
    """Cheap insurance against a config edit that silently disables the guard."""
    assert len(forbidden_columns("A")) >= 50
    assert len(forbidden_columns("C")) >= 30
