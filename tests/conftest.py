"""Shared pytest fixtures for the Healthcare RCM Intelligence test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root (the directory containing pyproject.toml)."""
    return REPO_ROOT
