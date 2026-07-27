"""Feature declarations are checked when a feature is DEFINED, not after it leaks."""

from __future__ import annotations

import pytest

from src.features.build import MODEL_A_FEATURES
from src.features.leakage import LeakageError, forbidden_columns
from src.features.spec import FeatureSet, FeatureSpec, assert_frame_matches, validate_feature_set


def _spec(**kwargs) -> FeatureSpec:
    base = dict(name="f", kind="numeric", description="d", sources=("sim_payer_id",))
    return FeatureSpec(**{**base, **kwargs})


def test_the_real_model_a_feature_set_is_valid() -> None:
    validate_feature_set(MODEL_A_FEATURES)


def test_every_declared_feature_has_sources() -> None:
    """A feature with no declared lineage cannot be checked, so it is not allowed."""
    for spec in MODEL_A_FEATURES.specs:
        assert spec.sources, f"{spec.name} declares no sources"


def test_the_label_is_not_a_feature() -> None:
    assert MODEL_A_FEATURES.label not in MODEL_A_FEATURES.names
    assert MODEL_A_FEATURES.label in forbidden_columns("A")


def test_passthrough_columns_are_not_features() -> None:
    """Split keys and join keys ride along; the estimator never sees them."""
    assert not set(MODEL_A_FEATURES.passthrough) & set(MODEL_A_FEATURES.names)
    assert "claim_sk" in MODEL_A_FEATURES.passthrough
    assert "claim_sk" not in MODEL_A_FEATURES.names


def test_a_feature_reading_the_label_is_rejected() -> None:
    bad = FeatureSet(
        model="A",
        specs=(_spec(name="denial_rate", sources=("sim_denial_flag",)),),
        label="sim_denial_flag",
        time_column="sim_submission_date",
    )
    with pytest.raises(LeakageError, match="denial_rate"):
        validate_feature_set(bad)


def test_a_prior_period_feature_must_name_its_outcome() -> None:
    with pytest.raises(ValueError, match="must name their outcome"):
        _spec(point_in_time="prior_period")


def test_only_the_outcome_may_be_lagged() -> None:
    """A "historical average latent probability" is not a historical rate."""
    with pytest.raises(ValueError, match="no historical-rate reading"):
        _spec(point_in_time="prior_period", prior_period_sources=("sim_latent_p",))


def test_the_outcome_may_not_be_declared_as_a_direct_source_too() -> None:
    """Declaring it both ways would let a direct read hide behind the exemption."""
    bad = FeatureSet(
        model="A",
        specs=(
            _spec(
                name="sneaky",
                sources=("sim_payer_id", "sim_denial_flag"),
                point_in_time="prior_period",
                prior_period_sources=("sim_denial_flag",),
            ),
        ),
        label="sim_denial_flag",
        time_column="sim_submission_date",
    )
    with pytest.raises(LeakageError, match="sneaky"):
        validate_feature_set(bad)


def test_a_non_forbidden_label_is_rejected() -> None:
    """If the thing being predicted is safe to use as a feature, it is not a label."""
    bad = FeatureSet(
        model="A",
        specs=(_spec(),),
        label="sim_auth_missing",
        time_column="sim_submission_date",
    )
    with pytest.raises(LeakageError, match="not a label"):
        validate_feature_set(bad)


def test_duplicate_feature_names_are_rejected() -> None:
    bad = FeatureSet(
        model="A",
        specs=(_spec(), _spec()),
        label="sim_denial_flag",
        time_column="sim_submission_date",
    )
    with pytest.raises(LeakageError, match="duplicate"):
        validate_feature_set(bad)


def test_an_undeclared_column_in_the_frame_is_rejected() -> None:
    """The lineage check only means something if nothing can bypass it."""
    columns = [*MODEL_A_FEATURES.names, *MODEL_A_FEATURES.passthrough, "sim_latent_p"]
    with pytest.raises(LeakageError, match="never declared"):
        assert_frame_matches(columns, MODEL_A_FEATURES)


def test_a_missing_declared_feature_is_rejected() -> None:
    columns = [*MODEL_A_FEATURES.names[1:], *MODEL_A_FEATURES.passthrough]
    with pytest.raises(LeakageError, match="absent from the frame"):
        assert_frame_matches(columns, MODEL_A_FEATURES)


def test_prior_period_features_are_the_only_ones_touching_the_outcome() -> None:
    """Exactly the historical rates use the §4.2 exemption — nothing else."""
    exempt = set(MODEL_A_FEATURES.prior_period_features())
    assert exempt, "no prior-period features declared"
    for name in exempt:
        assert "prior" in name, f"{name} claims the §4.2 exemption but is not a historical rate"
    for spec in MODEL_A_FEATURES.specs:
        if spec.name not in exempt:
            assert not spec.prior_period_sources


def test_audit_rows_cover_every_feature() -> None:
    from src.features.spec import audit_rows

    rows = audit_rows(MODEL_A_FEATURES)
    assert len(rows) == len(MODEL_A_FEATURES.names)
    assert all(row["description"] for row in rows)
