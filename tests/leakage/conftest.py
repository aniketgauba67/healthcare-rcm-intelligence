"""Fixtures for the leakage suite (docs/project_rules.md §4.1).

The suite runs without Postgres and without `data/`: the generator is exercised
against the same synthetic base frame the simulation tests use, so the leakage gate is
a CI gate rather than something that only works on a developer's machine with a loaded
warehouse. `tests/leakage/test_live_schema.py` re-checks the same agreement against
real Postgres and is marked `integration`.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.leakage import firewall_doc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "simulated_forbidden_columns.md"
MODEL_CONFIG_PATH = REPO_ROOT / "config" / "model.yaml"

LABEL_COLUMN = "sim_denial_flag"


@pytest.fixture(scope="session")
def firewall() -> firewall_doc.FirewallDoc:
    """The firewall document, parsed."""
    assert DOC_PATH.exists(), f"missing firewall document: {DOC_PATH}"
    return firewall_doc.parse(DOC_PATH)


@pytest.fixture(scope="session")
def model_config() -> dict:
    assert MODEL_CONFIG_PATH.exists(), f"missing model config: {MODEL_CONFIG_PATH}"
    return yaml.safe_load(MODEL_CONFIG_PATH.read_text())


@pytest.fixture(scope="session")
def sim_tables() -> dict[str, pd.DataFrame]:
    """Generated simulated tables, keyed by table name.

    Built here rather than imported from the simulation suite's `result` fixture
    because fixtures do not cross conftest directories; the base frame itself is shared
    so the two suites cannot drift apart in what they consider a claim.
    """
    from src.simulation.config import load_config
    from src.simulation.generator import generate
    from tests.simulation.conftest import make_base

    return dict(generate(load_config(), make_base()).tables)


@pytest.fixture(scope="session")
def generated_schema(sim_tables: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    return {table: list(frame.columns) for table, frame in sim_tables.items()}


@pytest.fixture(scope="session")
def forbidden_columns(
    firewall: firewall_doc.FirewallDoc, generated_schema: dict[str, list[str]]
) -> frozenset[str]:
    """Every column the firewall document forbids as a Model A feature."""
    return firewall.model_a_forbidden(generated_schema)


@pytest.fixture(scope="session")
def permitted_columns(
    firewall: firewall_doc.FirewallDoc, generated_schema: dict[str, list[str]]
) -> frozenset[str]:
    """Every column the firewall document permits as a Model A feature."""
    return firewall.model_a_permitted(generated_schema)


@pytest.fixture(scope="session")
def claim_frame(sim_tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per claim: the permitted tables joined to the adjudication truth.

    Used to build both a clean matrix and the poisoned matrices the detector tests are
    calibrated on. The join is on `claim_sk` and row order is fixed by sorting, so the
    truth frame and any matrix derived from it stay aligned.
    """
    adjudication = sim_tables["sim_claim_adjudication"].sort_values("claim_sk")
    frame = adjudication.reset_index(drop=True)
    for table in ("sim_authorization_eligibility", "sim_documentation_coding"):
        other = sim_tables[table].sort_values("claim_sk").reset_index(drop=True)
        duplicated = [c for c in other.columns if c in frame.columns and c != "claim_sk"]
        frame = frame.merge(other.drop(columns=duplicated), on="claim_sk", how="inner")
    return frame


@pytest.fixture(scope="session")
def label(claim_frame: pd.DataFrame) -> np.ndarray:
    return claim_frame[LABEL_COLUMN].astype(int).to_numpy()


@pytest.fixture(scope="session")
def truth_frame(claim_frame: pd.DataFrame, forbidden_columns: frozenset[str]) -> pd.DataFrame:
    """The forbidden columns, for the same rows as `claim_frame`."""
    return claim_frame[[c for c in claim_frame.columns if c in forbidden_columns]]


@pytest.fixture(scope="session")
def clean_matrix(claim_frame: pd.DataFrame, forbidden_columns: frozenset[str]) -> pd.DataFrame:
    """A matrix of permitted columns only — the negative control.

    Every probe must be silent on this. A detector that cannot stay quiet on honest
    input is a detector that will be switched off the first time it blocks a merge.
    """
    permitted = [
        c
        for c in claim_frame.columns
        if c not in forbidden_columns and c not in firewall_doc.JOIN_KEYS
    ]
    return claim_frame[permitted]


@pytest.fixture(scope="session")
def oracle_ceiling(claim_frame: pd.DataFrame, label: np.ndarray) -> float:
    """AUC of the generator's latent probability — the bound no feature may pass."""
    from tests.leakage import detectors

    return detectors.single_feature_auc(claim_frame["sim_latent_p"], label)
