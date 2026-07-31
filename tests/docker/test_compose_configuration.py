"""Compose and README preserve the pinned bundle contract."""

from __future__ import annotations

import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
README = REPO_ROOT / "README.md"

DEFAULT_BUNDLE = "/app/dashboard/demo_data/rcm_demo.duckdb"
DEFAULT_SHA256 = "ef9d8013d84f74133153033a5e68f950cf51cc5e1e559cf80175f93a94c3e7e0"


def test_bundle_consumers_receive_blank_safe_compose_defaults() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    expected_path = f"${{RCM_DEMO_BUNDLE:-{DEFAULT_BUNDLE}}}"
    expected_sha = f"${{RCM_DEMO_BUNDLE_SHA256:-{DEFAULT_SHA256}}}"

    for service in ("api", "dashboard"):
        environment = services[service]["environment"]
        assert environment["RCM_DEMO_BUNDLE"] == expected_path
        assert environment["RCM_DEMO_BUNDLE_SHA256"] == expected_sha

    for service in ("postgres", "warehouse-init"):
        environment = services[service]["environment"]
        assert "RCM_DEMO_BUNDLE" not in environment
        assert "RCM_DEMO_BUNDLE_SHA256" not in environment


def test_readme_documents_bundle_recreation_and_container_path() -> None:
    readme = README.read_text()

    required = (
        DEFAULT_BUNDLE,
        DEFAULT_SHA256,
        "read-only bind mount",
        "does not hot-swap the serving connection",
        "--force-recreate api dashboard",
        "Restoring the exact original artifact can recover readiness without a restart",
        "switching to a genuinely different approved artifact requires recreation",
    )
    for statement in required:
        assert statement in readme


def test_readme_documents_every_empty_postgres_page() -> None:
    readme = README.read_text()

    for page in (
        "Executive Overview",
        "Denial Prevention",
        "A/R Recovery",
        "Work Queue",
        "Model & Data Quality",
    ):
        assert page in readme

    for statement in (
        "All five pages remain renderable without exceptions",
        "not a zero KPI book",
        "not zero denial or",
        "does not fabricate zero-dollar or zero-day measures",
        "missing model and heuristic queue inputs",
        "suppresses 17/17 success",
    ):
        assert statement in readme
