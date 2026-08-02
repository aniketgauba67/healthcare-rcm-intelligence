"""Compose and README preserve the pinned bundle contract."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
README = REPO_ROOT / "README.md"

DEFAULT_BUNDLE = "/app/dashboard/demo_data/rcm_demo.duckdb"
DEFAULT_SHA256 = "66456ebf4e52e4c5f5565cf6085efb89d80bc264710b3783bd1eb2e491a03e95"
BUNDLE_ENVIRONMENT = ("RCM_DEMO_BUNDLE", "RCM_DEMO_BUNDLE_SHA256")


def _docker_compose_command() -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker executable is unavailable; cannot run Docker Compose config")

    command = [docker, "compose"]
    probe = subprocess.run(
        [*command, "version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip() or probe.stdout.strip() or "no diagnostic output"
        pytest.skip(f"Docker Compose plugin is unavailable: {detail}")
    return command


def _resolved_compose(overrides: dict[str, str]) -> dict[str, object]:
    environment = os.environ.copy()
    for variable in BUNDLE_ENVIRONMENT:
        environment.pop(variable, None)
    environment.update(overrides)

    command = [*_docker_compose_command(), "config"]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Docker Compose configuration failed"
            f"\ncommand: {' '.join(command)}"
            f"\nstdout:\n{completed.stdout}"
            f"\nstderr:\n{completed.stderr}"
        )

    resolved = yaml.safe_load(completed.stdout)
    assert isinstance(resolved, dict), completed.stdout
    return resolved


def _assert_bundle_service_scope(resolved: dict[str, object], *, bundle: str, sha256: str) -> None:
    services = resolved["services"]
    for service in ("api", "dashboard"):
        environment = services[service]["environment"]
        assert environment["RCM_DEMO_BUNDLE"] == bundle
        assert environment["RCM_DEMO_BUNDLE_SHA256"] == sha256

    for service in ("postgres", "warehouse-init"):
        environment = services[service]["environment"]
        assert "RCM_DEMO_BUNDLE" not in environment
        assert "RCM_DEMO_BUNDLE_SHA256" not in environment


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


def test_compose_resolves_unset_bundle_variables_to_committed_defaults() -> None:
    resolved = _resolved_compose({})

    _assert_bundle_service_scope(resolved, bundle=DEFAULT_BUNDLE, sha256=DEFAULT_SHA256)


def test_compose_resolves_blank_bundle_variables_to_committed_defaults() -> None:
    resolved = _resolved_compose({"RCM_DEMO_BUNDLE": "", "RCM_DEMO_BUNDLE_SHA256": ""})

    _assert_bundle_service_scope(resolved, bundle=DEFAULT_BUNDLE, sha256=DEFAULT_SHA256)


def test_compose_resolves_explicit_bundle_overrides() -> None:
    alternate_bundle = "/opt/rcm/alternate.duckdb"
    alternate_sha256 = "a" * 64
    resolved = _resolved_compose(
        {
            "RCM_DEMO_BUNDLE": alternate_bundle,
            "RCM_DEMO_BUNDLE_SHA256": alternate_sha256,
        }
    )

    _assert_bundle_service_scope(resolved, bundle=alternate_bundle, sha256=alternate_sha256)


def test_compose_keeps_default_pin_for_alternate_path_with_blank_sha() -> None:
    alternate_bundle = "/opt/rcm/alternate.duckdb"
    resolved = _resolved_compose(
        {"RCM_DEMO_BUNDLE": alternate_bundle, "RCM_DEMO_BUNDLE_SHA256": ""}
    )

    _assert_bundle_service_scope(resolved, bundle=alternate_bundle, sha256=DEFAULT_SHA256)


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
