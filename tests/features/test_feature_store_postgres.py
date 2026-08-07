"""The feature store, built against the live warehouse.

The unit tests prove the mechanisms are correct on data small enough to check by
hand. These prove the mechanisms were actually applied to the real thing:
the frame reconciles to the warehouse, the split has the geometry the firewall
document prescribes, the point-in-time invariants survive contact with real
dates, and no single feature predicts the label well enough to be suspicious.

Read-only. Nothing here writes to the database.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from src.features.build import MODEL_A_FEATURES, build_model_a_frame, feature_matrix, labels
from src.features.historical import add_prior_period_rates
from src.features.leakage import assert_no_forbidden_columns, forbidden_columns, load_model_config
from src.features.splits import calibration_split, split_from_config

pytestmark = pytest.mark.integration

EXPECTED_CLAIMS = 20867


@pytest.fixture(scope="module")
def engine():
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")
    from sqlalchemy import create_engine

    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")
    return eng


@pytest.fixture(scope="module")
def config() -> dict:
    return load_model_config()


@pytest.fixture(scope="module")
def frame(engine, config) -> pd.DataFrame:
    return build_model_a_frame(engine, config)


def test_grain_and_row_count_reconcile_to_the_warehouse(frame, engine) -> None:
    with engine.connect() as conn:
        claims = conn.execute(text("select count(*) from rcm.sim_claim_adjudication")).scalar()
    assert len(frame) == claims == EXPECTED_CLAIMS
    assert frame["claim_sk"].is_unique


def test_base_rate_reconciles_to_the_warehouse(frame, engine) -> None:
    with engine.connect() as conn:
        denied = conn.execute(
            text("select count(*) from rcm.sim_claim_adjudication where sim_denial_flag")
        ).scalar()
    assert int(labels(frame).sum()) == denied


def test_no_forbidden_column_reaches_the_matrix(frame, config) -> None:
    matrix = feature_matrix(frame)
    assert_no_forbidden_columns(matrix.columns, model="A", config=config)
    assert MODEL_A_FEATURES.label not in matrix.columns


def test_the_label_is_carried_but_never_as_a_feature(frame) -> None:
    assert MODEL_A_FEATURES.label in frame.columns
    assert MODEL_A_FEATURES.label in MODEL_A_FEATURES.passthrough
    assert MODEL_A_FEATURES.label not in MODEL_A_FEATURES.names


def test_split_matches_the_documented_geometry(frame, config) -> None:
    """§8: the 80th-percentile cut lands near 2021-12-28 with a ~4,170-row fold."""
    split = split_from_config(frame, config)
    assert str(split.cut_date.date()) == "2021-12-28"
    assert int(split.test.sum()) == 4173
    assert split.test.mean() == pytest.approx(0.20, abs=0.01)
    earliest_test = frame.loc[split.test, "sim_submission_date"].min()
    latest_train = frame.loc[split.train, "sim_submission_date"].max()
    assert earliest_test > latest_train


def test_calibration_fold_precedes_the_test_fold(frame, config) -> None:
    split = split_from_config(frame, config)
    inner = calibration_split(frame, split, config)
    assert not (inner.test & split.test).any()
    assert (
        frame.loc[inner.test, "sim_submission_date"].max()
        < frame.loc[split.test, "sim_submission_date"].min()
    )


def test_truncation_invariance_on_real_dates(frame, config) -> None:
    """Rebuild the historical rates with the future deleted; the past must not move."""
    prior = config["feature_store"]["prior_period"]
    kwargs = dict(
        date_column="sim_submission_date",
        outcome_column="sim_denial_flag",
        definitions=prior["entities"],
        embargo_days=int(prior["embargo_days"]),
        smoothing=float(prior["smoothing"]),
    )
    source = frame[["claim_sk", "sim_submission_date", "prvdr_num", "sim_denial_flag"]].copy()
    source["sim_payer_id"] = frame["sim_payer_id"]
    source["sim_service_line_id"] = frame["sim_service_line_id"]

    cut = pd.Timestamp("2021-12-28")
    full = add_prior_period_rates(source, **kwargs).set_index("claim_sk")
    truncated = add_prior_period_rates(
        source.loc[source["sim_submission_date"] <= cut].copy(), **kwargs
    ).set_index("claim_sk")

    columns = [c for c in full.columns if c.endswith(("_prior_denial_rate", "_prior_claims"))]
    left = truncated[columns]
    right = full.loc[left.index, columns]
    assert ((left == right) | (left.isna() & right.isna())).all().all()


def test_no_single_feature_is_suspiciously_predictive(frame, config) -> None:
    """A leak canary.

    The label carries deliberate noise and the oracle tops out near AUC 0.68, so
    no single column should come close on its own. A feature above the config's
    `suspicious_roc_auc` is a leak to hunt, not a discovery — docs/project_rules.md §1.
    """
    from sklearn.metrics import roc_auc_score

    y = labels(frame)
    ceiling = float(config["sanity_bounds"]["suspicious_roc_auc"])
    offenders: list[tuple[str, float]] = []
    for name in MODEL_A_FEATURES.names:
        column = frame[name]
        if MODEL_A_FEATURES.spec(name).kind == "categorical":
            continue
        values = pd.to_numeric(column, errors="coerce")
        if values.notna().sum() < 100 or values.nunique(dropna=True) < 2:
            continue
        mask = values.notna()
        auc = roc_auc_score(y[mask], values[mask])
        auc = max(auc, 1 - auc)  # direction-agnostic
        if auc > ceiling:
            offenders.append((name, round(float(auc), 4)))
    assert not offenders, f"single features above the suspicion bound: {offenders}"


def test_categorical_features_do_not_separate_the_label(frame, config) -> None:
    """The same canary for categoricals: no level may be an all-or-nothing group."""
    y = labels(frame)
    problems: list[str] = []
    for name in MODEL_A_FEATURES.by_kind("categorical"):
        grouped = y.groupby(frame[name].astype("string")).agg(["mean", "size"])
        material = grouped.loc[grouped["size"] >= 100]
        if not material.empty and (
            (material["mean"] <= 0.001).any() or (material["mean"] >= 0.999).any()
        ):
            problems.append(name)
    assert not problems, f"categorical levels that determine the label: {problems}"


def test_prior_period_features_have_history_where_expected(frame) -> None:
    """The rates must actually accumulate, or they are dead weight pretending to work."""
    late = frame.loc[frame["sim_submission_date"] > pd.Timestamp("2019-01-01")]
    assert (late["sim_payer_prior_claims"] > 0).mean() > 0.99
    assert late["sim_payer_prior_denial_rate"].notna().mean() > 0.99
    # Provider coverage is much better than the per-provider counts suggest,
    # because claim volume is concentrated: the median PROVIDER has two claims,
    # but the top 10% of providers hold 53% of CLAIMS. So 83% of post-2019
    # claims do have provider history even though most providers never build
    # any. Pinned here so the distinction stays on the record — it is exactly
    # the kind of thing that gets described backwards in a model card.
    assert 0.75 < (late["sim_provider_prior_claims"] > 0).mean() < 0.95


def test_every_feature_column_is_usable(frame) -> None:
    """No all-null and no zero-variance columns: both are silent dead weight."""
    matrix = feature_matrix(frame)
    all_null = [c for c in matrix.columns if matrix[c].isna().all()]
    constant = [c for c in matrix.columns if matrix[c].nunique(dropna=True) <= 1]
    assert not all_null, f"all-null features: {all_null}"
    assert not constant, f"zero-variance features: {constant}"


def test_the_crosswalk_never_reaches_the_feature_store(frame, config) -> None:
    """Facility analysis keys on the synthetic prvdr_num, never the real CCN."""
    blocked = forbidden_columns("A", config)
    assert "prvdr_num" not in blocked, "the synthetic provider key must stay usable"
    assert "prvdr_num" in MODEL_A_FEATURES.passthrough
    for column in ("sim_facility_ccn", "facility_ccn", "sim_facility_name", "facility_name"):
        assert column not in frame.columns
        assert column in blocked


def test_feature_store_is_deterministic(engine, config) -> None:
    first = build_model_a_frame(engine, config)
    second = build_model_a_frame(engine, config)
    pd.testing.assert_frame_equal(first, second)


def test_numeric_features_are_finite_where_present(frame) -> None:
    matrix = feature_matrix(frame)
    for name in MODEL_A_FEATURES.by_kind("numeric"):
        values = matrix[name].to_numpy(dtype=float)
        present = values[~np.isnan(values)]
        assert np.isfinite(present).all(), f"{name} contains inf"
