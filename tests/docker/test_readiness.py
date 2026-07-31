"""Liveness stays process-only while readiness fails closed on dependencies."""

from __future__ import annotations

import hashlib
import pathlib
import shutil

import duckdb
import pytest
from fastapi import HTTPException

from docker.dashboard_server import ReadinessHandler, evaluate_dashboard_readiness
from src.api import main as api_main
from src.demo import bundle as bundle_module
from src.demo import source as source_module
from src.demo.bundle import BundleReadinessError, validate_bundle_readiness
from src.infra.postgres_contract import PostgresContractError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMITTED_BUNDLE = REPO_ROOT / "dashboard" / "demo_data" / "rcm_demo.duckdb"


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_openapi_distinguishes_liveness_and_readiness() -> None:
    paths = api_main.app.openapi()["paths"]
    assert "/live" in paths
    assert "/ready" in paths
    assert "/health" in paths


def test_api_liveness_is_separate_from_postgres_readiness(monkeypatch) -> None:
    monkeypatch.setenv("RCM_REQUIRE_POSTGRES_READY", "true")

    def unavailable() -> None:
        raise PostgresContractError("missing view rcm.vw_model_monitoring")

    monkeypatch.setattr(api_main, "validate_postgres_contract", unavailable)

    assert api_main.live().status == "live"
    with pytest.raises(HTTPException) as raised:
        api_main.health()
    assert raised.value.status_code == 503
    assert raised.value.detail["error"] == "postgres_not_ready"


def test_api_readiness_passes_when_the_contract_and_bundle_are_ready(monkeypatch) -> None:
    monkeypatch.setenv("RCM_REQUIRE_POSTGRES_READY", "true")
    monkeypatch.setattr(api_main, "validate_postgres_contract", lambda: None)
    response = api_main.health()
    assert response.status == "ok"
    assert response.data_source.kind == "bundle"


def test_fresh_bundle_probe_rejects_deleted_and_corrupt_files_then_recovers(tmp_path) -> None:
    candidate = tmp_path / "demo.duckdb"
    shutil.copy2(COMMITTED_BUNDLE, candidate)
    expected = _digest(candidate)

    ready = validate_bundle_readiness(candidate, expected_sha256=expected)
    assert ready.sha256 == expected

    original = candidate.read_bytes()
    candidate.unlink()
    with pytest.raises(BundleReadinessError, match="path is unavailable"):
        validate_bundle_readiness(candidate, expected_sha256=expected)

    candidate.write_bytes(b"")
    with pytest.raises(BundleReadinessError, match="empty"):
        validate_bundle_readiness(candidate, expected_sha256=expected)

    candidate.write_bytes(b"not a DuckDB database")
    with pytest.raises(BundleReadinessError, match="could not be validated"):
        validate_bundle_readiness(candidate, expected_sha256=_digest(candidate))

    candidate.write_bytes(original)
    assert validate_bundle_readiness(candidate, expected_sha256=expected).sha256 == expected


def test_fresh_bundle_probe_rejects_real_duckdb_with_wrong_structure(tmp_path) -> None:
    candidate = tmp_path / "wrong-structure.duckdb"
    connection = duckdb.connect(str(candidate))
    try:
        connection.execute("create table unrelated(value integer)")
    finally:
        connection.close()

    with pytest.raises(BundleReadinessError, match="inventory mismatch"):
        validate_bundle_readiness(candidate, expected_sha256=_digest(candidate))


def test_fresh_bundle_probe_rejects_missing_required_metadata(tmp_path) -> None:
    from src.demo import spec

    candidate = tmp_path / "missing-metadata.duckdb"
    connection = duckdb.connect(str(candidate))
    try:
        for dataset in spec.DATASETS_BY_NAME:
            connection.execute(f'create table "{dataset}"(placeholder integer)')
    finally:
        connection.close()

    with pytest.raises(BundleReadinessError, match="demo_build_info is missing columns"):
        validate_bundle_readiness(candidate, expected_sha256=_digest(candidate))


def test_fresh_bundle_probe_rejects_a_structurally_valid_wrong_fingerprint(tmp_path) -> None:
    candidate = tmp_path / "wrong-identity.duckdb"
    shutil.copy2(COMMITTED_BUNDLE, candidate)
    with pytest.raises(BundleReadinessError, match="fingerprint mismatch"):
        validate_bundle_readiness(candidate, expected_sha256="0" * 64)


def test_readiness_ignores_a_warm_cached_source_after_bundle_deletion(
    tmp_path, monkeypatch
) -> None:
    candidate = tmp_path / "cached.duckdb"
    shutil.copy2(COMMITTED_BUNDLE, candidate)
    expected = _digest(candidate)
    monkeypatch.setenv(bundle_module.BUNDLE_PATH_ENV, str(candidate))
    monkeypatch.setenv(bundle_module.BUNDLE_SHA256_ENV, expected)
    monkeypatch.delenv("RCM_REQUIRE_POSTGRES_READY", raising=False)
    source_module.reset_cache()
    bundle_module.reset_cache()

    cached = source_module.get_source()
    assert "vw_executive_rcm_summary" in cached.available()
    candidate.unlink()
    # The already-open descriptor is intentionally still readable. Readiness
    # must nevertheless reject what a new process could no longer open.
    assert "vw_executive_rcm_summary" in cached.available()
    with pytest.raises(HTTPException) as raised:
        api_main.health()
    assert raised.value.status_code == 503
    assert raised.value.detail["error"] == "bundle_not_ready"

    shutil.copy2(COMMITTED_BUNDLE, candidate)
    api_main._assert_bundle_readiness()
    assert "vw_executive_rcm_summary" in cached.available()
    cached.bundle.close()
    source_module.reset_cache()
    bundle_module.reset_cache()


def test_fresh_bundle_probe_always_closes_its_temporary_connection(monkeypatch) -> None:
    real_connect = duckdb.connect
    closed: list[bool] = []

    class TrackedConnection:
        def __init__(self, connection) -> None:  # noqa: ANN001
            self._connection = connection

        def __getattr__(self, name: str):  # noqa: ANN204
            return getattr(self._connection, name)

        def close(self) -> None:
            self._connection.close()
            closed.append(True)

    monkeypatch.setattr(
        duckdb,
        "connect",
        lambda *args, **kwargs: TrackedConnection(real_connect(*args, **kwargs)),
    )
    validate_bundle_readiness(COMMITTED_BUNDLE)
    assert closed == [True]


def test_dashboard_readiness_requires_streamlit_and_api(monkeypatch) -> None:
    monkeypatch.setenv("RCM_STREAMLIT_LIVENESS_URL", "http://streamlit/live")
    monkeypatch.setenv("RCM_API_READINESS_URL", "http://api/ready")

    def ready(url: str) -> tuple[int, bytes]:
        return (200, b"ok") if "streamlit" in url else (200, b'{"status":"ok"}')

    status, dependencies = evaluate_dashboard_readiness(ready)
    assert status
    assert dependencies == {"streamlit": "ready", "api": "ready"}

    def api_down(url: str) -> tuple[int, bytes]:
        return (200, b"ok") if "streamlit" in url else (503, b"not ready")

    status, dependencies = evaluate_dashboard_readiness(api_down)
    assert not status
    assert dependencies["api"] == "unready (HTTP 503)"

    def streamlit_down(url: str) -> tuple[int, bytes]:
        return (503, b"down") if "streamlit" in url else (200, b'{"status":"ok"}')

    status, dependencies = evaluate_dashboard_readiness(streamlit_down)
    assert not status
    assert dependencies["streamlit"] == "unready (HTTP 503)"


def test_dashboard_readiness_ignores_client_disconnect_while_writing() -> None:
    class DisconnectedWriter:
        def write(self, body: bytes) -> None:
            raise BrokenPipeError

    handler = object.__new__(ReadinessHandler)
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.wfile = DisconnectedWriter()

    handler._write(200, {"status": "ready"})
