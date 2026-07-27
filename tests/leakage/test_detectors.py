"""The leakage detectors must be shown to work before anything is gated on them.

A guard that has never been demonstrated to catch anything is worse than no guard: it
converts an unchecked property into one everybody believes is checked. So this module
does two things on every run.

It builds a matrix of permitted columns only and asserts that every probe stays silent
(specificity — a detector that cannot pass honest work will be switched off the first
time it blocks a merge), and it builds a matrix from each way a forbidden column can be
disguised and asserts the guard rejects it (sensitivity). The disguises are the ones
that actually happen: a rename, a log, a rescale, a ratio against the billed amount, a
re-binning, a null indicator, and the surrogate key under a new name.

The thresholds in `detectors.py` were calibrated by measuring these same quantities on
the live 20,867-claim layer. The assertions below re-measure the separation on every
run so the calibration is a property the suite maintains, not a number in a comment
that drifted three commits ago.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.leakage import detectors

# Measured on the live layer (20,867 claims) and re-measured on the generated frame by
# test_permitted_baseline_stays_below_the_thresholds.
LIVE_PERMITTED_MAX_AUC = 0.5859
LIVE_ORACLE_AUC = 0.6778


def _poisoned(claim_frame: pd.DataFrame, name: str, values) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(values).reset_index(drop=True)})


@pytest.fixture(scope="module")
def billed(claim_frame: pd.DataFrame) -> pd.Series:
    """A plausible pre-submission denominator, for the ratio disguise."""
    rng = np.random.default_rng(11)
    return pd.Series(rng.lognormal(9.5, 0.8, len(claim_frame)))


# --------------------------------------------------------------------------- clean


def test_clean_matrix_produces_no_findings(
    clean_matrix,
    truth_frame,
    claim_frame,
    label,
    oracle_ceiling,
    forbidden_columns,
    permitted_columns,
):
    """Specificity. A matrix of permitted columns must pass every probe."""
    findings = (
        detectors.name_findings(list(clean_matrix.columns), forbidden_columns)
        + detectors.dependency_findings(clean_matrix, truth_frame)
        + detectors.label_auc_findings(clean_matrix, label, oracle_ceiling)
        + detectors.identifier_findings(clean_matrix, claim_frame["claim_sk"])
        + detectors.unrecognised_date_findings(clean_matrix, permitted_columns)
    )
    assert not findings, "false positives on a permitted-only matrix:\n" + "\n".join(
        str(f) for f in findings
    )


def test_permitted_baseline_stays_below_the_thresholds(
    clean_matrix, truth_frame, label, oracle_ceiling
):
    """The separation the thresholds rely on is re-measured, not assumed.

    If the generator changes such that a permitted column starts approaching the oracle,
    this fails here rather than silently eroding the guard's specificity.
    """
    aucs = {c: detectors.single_feature_auc(clean_matrix[c], label) for c in clean_matrix}
    assert max(aucs.values()) < oracle_ceiling, (
        "a permitted column now reaches the oracle ceiling — either the generator "
        f"changed or a forbidden column is in the permitted set: {aucs}"
    )

    usable = [c for c in truth_frame.columns if not detectors._is_row_key(truth_frame[c])]
    worst = max(
        (detectors.uncertainty_coefficient(clean_matrix[c], truth_frame[f]), c, f)
        for c in clean_matrix.columns
        for f in usable
        if not (detectors._is_datelike(clean_matrix[c]) and detectors._is_datelike(truth_frame[f]))
    )
    assert worst[0] < detectors.DEPENDENCY_THRESHOLD, (
        f"a permitted column is now {worst[0]:.4f}-determined by forbidden column "
        f"{worst[2]} ({worst[1]}), at or above the {detectors.DEPENDENCY_THRESHOLD} "
        "threshold — the dependency probe would start rejecting honest work"
    )


def test_oracle_ceiling_matches_the_documented_irreducible_noise(oracle_ceiling):
    """docs/assumptions.md §2 and the firewall document §8 both state AUC ~= 0.68."""
    assert 0.62 <= oracle_ceiling <= 0.72, (
        f"oracle AUC {oracle_ceiling:.4f} is outside the documented ~0.68 ceiling; the "
        "label-AUC probe's threshold is derived from it and would be miscalibrated"
    )


# ----------------------------------------------------------------------- disguises


def test_catches_a_forbidden_column_pasted_in_verbatim(claim_frame, forbidden_columns):
    matrix = _poisoned(claim_frame, "sim_denied_amount", claim_frame["sim_denied_amount"])
    assert detectors.name_findings(list(matrix.columns), forbidden_columns)


def test_catches_a_forbidden_column_wearing_a_decorated_name(claim_frame, forbidden_columns):
    """`denial_flag_hist` is not a forbidden name, but it carries the stem."""
    matrix = _poisoned(claim_frame, "denial_flag_hist", claim_frame["sim_late_filing_flag"])
    assert detectors.name_findings(list(matrix.columns), forbidden_columns)


def test_catches_a_renamed_forbidden_column(claim_frame, truth_frame, label, oracle_ceiling):
    """The name probe is blind here; the value probes are not."""
    matrix = _poisoned(claim_frame, "risk_score_v2", claim_frame["sim_denied_amount"])
    assert detectors.dependency_findings(matrix, truth_frame)
    assert detectors.label_auc_findings(matrix, label, oracle_ceiling)


def test_catches_a_log_transformed_forbidden_column(claim_frame, truth_frame):
    matrix = _poisoned(claim_frame, "amount_log", np.log1p(claim_frame["sim_denied_amount"]))
    assert detectors.dependency_findings(matrix, truth_frame)


def test_catches_a_rescaled_forbidden_column(claim_frame, truth_frame, label, oracle_ceiling):
    matrix = _poisoned(claim_frame, "expected_revenue", claim_frame["sim_paid_amount"] * 1.1)
    findings = detectors.dependency_findings(matrix, truth_frame) + detectors.label_auc_findings(
        matrix, label, oracle_ceiling
    )
    assert findings


def test_catches_a_ratio_against_a_permitted_column(
    claim_frame, billed, truth_frame, label, oracle_ceiling
):
    """A ratio is not a function of the forbidden column alone, so the dependency probe
    can miss it. The oracle ceiling catches it regardless of how it was built."""
    matrix = _poisoned(claim_frame, "denial_share", claim_frame["sim_denied_amount"] / billed)
    findings = detectors.dependency_findings(matrix, truth_frame) + detectors.label_auc_findings(
        matrix, label, oracle_ceiling
    )
    assert findings


def test_catches_a_rebinned_forbidden_column(claim_frame, truth_frame, label, oracle_ceiling):
    """Discretising a forbidden column destroys its name and its scale, not its content."""
    matrix = _poisoned(
        claim_frame,
        "payment_speed_band",
        pd.cut(claim_frame["sim_days_to_payment"], 8, labels=False),
    )
    findings = detectors.dependency_findings(matrix, truth_frame) + detectors.label_auc_findings(
        matrix, label, oracle_ceiling
    )
    assert findings


def test_catches_a_null_indicator_of_a_forbidden_column(
    claim_frame, truth_frame, label, oracle_ceiling
):
    """`sim_denial_review_date` is non-null exactly when the claim was denied."""
    matrix = _poisoned(
        claim_frame, "was_reviewed", claim_frame["sim_denial_review_date"].notna().astype(int)
    )
    findings = detectors.dependency_findings(matrix, truth_frame) + detectors.label_auc_findings(
        matrix, label, oracle_ceiling
    )
    assert findings


def test_catches_a_column_that_only_inherits_a_forbidden_null_pattern(
    claim_frame, label, oracle_ceiling
):
    """A feature whose *values* are noise but whose missingness mirrors the label."""
    rng = np.random.default_rng(3)
    values = pd.Series(rng.normal(size=len(claim_frame)))
    values[claim_frame["sim_denial_review_date"].isna().to_numpy()] = np.nan
    matrix = _poisoned(claim_frame, "coder_note_score", values)
    assert detectors.label_auc_findings(matrix, label, oracle_ceiling)


def test_catches_the_surrogate_key_under_another_name(claim_frame):
    """§7: claim_sk is assigned in source-file order and acts as a hidden date."""
    matrix = _poisoned(claim_frame, "row_order", claim_frame["claim_sk"])
    assert detectors.identifier_findings(matrix, claim_frame["claim_sk"])


def test_catches_a_renamed_post_submission_date(claim_frame, permitted_columns):
    """The date carve-out in dependency_findings is closed here, not left open."""
    matrix = _poisoned(claim_frame, "service_ready_date", claim_frame["sim_adjudication_date"])
    assert detectors.unrecognised_date_findings(matrix, permitted_columns)


def test_permitted_dates_are_not_flagged_by_the_date_probe(clean_matrix, permitted_columns):
    """`sim_auth_request_date` is permitted wholesale via its table, not by name."""
    assert not detectors.unrecognised_date_findings(clean_matrix, permitted_columns)


# ------------------------------------------------------------------------ temporal


def test_temporal_probe_accepts_a_quantile_split(claim_frame):
    """The 80/20 quantile split on sim_submission_date the firewall document §8 prescribes."""
    dates = claim_frame["sim_submission_date"]
    cut = dates.quantile(0.8)
    assert not detectors.temporal_findings(dates, dates <= cut)


def test_temporal_probe_rejects_a_random_split(claim_frame):
    """CLAUDE.md §4.3 forbids a random split wherever time-dependent features exist."""
    rng = np.random.default_rng(5)
    is_train = pd.Series(rng.random(len(claim_frame)) < 0.8)
    findings = detectors.temporal_findings(claim_frame["sim_submission_date"], is_train)
    assert findings and findings[0].probe == "temporal"


# -------------------------------------------------------------------------- report


def test_auc_report_lists_features_strongest_first(clean_matrix, label, oracle_ceiling):
    report = detectors.auc_report(clean_matrix, label, oracle_ceiling)
    assert "oracle ceiling" in report
    assert len(report.splitlines()) > 1
