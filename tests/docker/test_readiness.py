"""Liveness stays process-only while readiness fails closed on dependencies."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from docker.dashboard_server import evaluate_dashboard_readiness
from src.api import main as api_main
from src.infra.postgres_contract import PostgresContractError


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
