"""Hosted setup preserves the accepted bundle and PostgreSQL contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading
import time

import pytest

from scripts import initialize_hosted_postgres as hosted_initializer
from src.api import scoring
from src.demo.bundle import EXPECTED_BUNDLE_SHA256, open_bundle
from src.infra import postgres_contract


def test_postgres_contract_prefers_database_url(monkeypatch) -> None:
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setenv("DATABASE_URL", "dsn-from-provider-secret-control")
    monkeypatch.setattr(
        postgres_contract.psycopg2,
        "connect",
        lambda *args, **kwargs: calls.append((args, kwargs)) or object(),
    )

    postgres_contract.connect()

    assert calls == [
        (
            ("dsn-from-provider-secret-control",),
            {"connect_timeout": 3},
        )
    ]


def test_hosted_sql_manifest_matches_all_tracked_files() -> None:
    assert [path.name for path in hosted_initializer.DDL_FILES] == [
        "00_schema.sql",
        "10_dimensions.sql",
        "20_facts.sql",
        "30_sim_crosswalk.sql",
        "40_quarantine.sql",
        "50_sim_adjudication.sql",
        "60_reference_codes.sql",
    ]
    assert [path.name for path in hosted_initializer.VIEW_FILES] == [
        "vw_claim_enriched.sql",
        "vw_ar_aging.sql",
        "vw_clean_claim_performance.sql",
        "vw_data_quality_scorecard.sql",
        "vw_denial_root_cause.sql",
        "vw_executive_rcm_summary.sql",
        "vw_model_monitoring.sql",
        "vw_payer_performance.sql",
        "vw_work_queue_priority.sql",
    ]


def test_partial_hosted_schema_fails_before_ddl(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        hosted_initializer,
        "_schema_state",
        lambda _connection: (True, (("ref_carc", "r"),)),
    )
    applied: list[bool] = []
    monkeypatch.setattr(
        hosted_initializer,
        "_apply_fresh_schema",
        lambda _connection: applied.append(True),
    )

    with pytest.raises(postgres_contract.PostgresContractError, match="missing base table"):
        hosted_initializer.initialize_hosted_postgres(connection)

    assert applied == []


def test_absent_hosted_schema_is_initialized_then_validated(monkeypatch) -> None:
    connection = _FakeConnection()
    applied: list[bool] = []
    monkeypatch.setattr(hosted_initializer, "_schema_state", lambda _connection: (False, ()))
    monkeypatch.setattr(
        hosted_initializer,
        "_apply_fresh_schema",
        lambda _connection: applied.append(True),
    )
    monkeypatch.setattr(
        hosted_initializer,
        "validate_postgres_contract",
        lambda _connection: postgres_contract.ContractReport(
            postgres_contract.SCHEMA,
            postgres_contract.BASE_TABLES,
            postgres_contract.VIEWS,
        ),
    )

    result = hosted_initializer.initialize_hosted_postgres(connection)

    assert result.initialized
    assert applied == [True]
    assert result.contract.total_relations == 33


def test_valid_hosted_schema_is_reused_without_ddl(monkeypatch) -> None:
    connection = _FakeConnection()
    inventory = tuple(
        [(name, "r") for name in postgres_contract.BASE_TABLES]
        + [(name, "v") for name in postgres_contract.VIEWS]
    )
    monkeypatch.setattr(
        hosted_initializer,
        "_schema_state",
        lambda _connection: (True, inventory),
    )
    applied: list[bool] = []
    monkeypatch.setattr(
        hosted_initializer,
        "_apply_fresh_schema",
        lambda _connection: applied.append(True),
    )
    monkeypatch.setattr(
        hosted_initializer,
        "validate_postgres_contract",
        lambda _connection: postgres_contract.ContractReport(
            postgres_contract.SCHEMA,
            postgres_contract.BASE_TABLES,
            postgres_contract.VIEWS,
        ),
    )

    result = hosted_initializer.initialize_hosted_postgres(connection)

    assert not result.initialized
    assert applied == []
    assert result.contract.total_relations == 33


def test_validate_only_rejects_an_absent_schema_without_ddl(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(hosted_initializer, "_schema_state", lambda _connection: (False, ()))
    applied: list[bool] = []
    monkeypatch.setattr(
        hosted_initializer,
        "_apply_fresh_schema",
        lambda _connection: applied.append(True),
    )

    with pytest.raises(postgres_contract.PostgresContractError, match="missing application schema"):
        hosted_initializer.initialize_hosted_postgres(connection, validate_only=True)

    assert applied == []


def test_bundle_source_reports_the_open_artifact_sha() -> None:
    bundle = open_bundle()
    assert bundle.artifact_sha256 == EXPECTED_BUNDLE_SHA256


def test_api_health_does_not_import_training_or_explanation_modules() -> None:
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment.pop("RCM_REQUIRE_POSTGRES_READY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from src.api.main import health; "
                "health(); "
                "assert 'src.models.train' not in sys.modules; "
                "assert 'src.models.explain' not in sys.modules; "
                "assert 'shap' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        cwd=hosted_initializer.REPO_ROOT,
        env=environment,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_model_initialization_is_single_flight(monkeypatch) -> None:
    scoring._cached_denial_risk_model.cache_clear()
    workers = 8
    start = threading.Barrier(workers)
    fitted = object()
    fit_calls = 0

    def fit_once():  # noqa: ANN202
        nonlocal fit_calls
        fit_calls += 1
        time.sleep(0.05)
        return fitted

    monkeypatch.setattr(scoring, "_fit_denial_risk_model", fit_once)

    def load_after_barrier():  # noqa: ANN202
        start.wait()
        return scoring.load_denial_risk_model()

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            loaded = list(executor.map(lambda _: load_after_barrier(), range(workers)))
        assert fit_calls == 1
        assert all(model is fitted for model in loaded)
    finally:
        scoring._cached_denial_risk_model.cache_clear()


class _FakeCursor:
    def __init__(self) -> None:
        self._row = ("16.0",)

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None

    def execute(self, statement: str, parameters=None) -> None:  # noqa: ANN001
        if statement == "show server_version":
            self._row = ("16.0",)

    def fetchone(self):  # noqa: ANN201
        return self._row


class _FakeConnection:
    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor()
