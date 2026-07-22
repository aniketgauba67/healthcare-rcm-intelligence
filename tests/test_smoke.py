"""Smoke tests that make the ``make test`` gate real on a clean clone.

These assert the package imports and the load-bearing project files exist, so
``uv run pytest`` collects at least one test and exits 0 instead of the
"no tests collected" exit code 5. Deeper contract, leakage, and reconciliation
suites live in the sibling ``tests/`` subpackages and are added per phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REQUIRED_FILES = [
    "CLAUDE.md",
    "tasks.md",
    "pyproject.toml",
    "Makefile",
    "config/sources.yaml",
    "config/simulation.yaml",
    "config/model.yaml",
]


def test_package_imports() -> None:
    """The top-level source package imports without side effects."""
    import src  # noqa: F401


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(repo_root: Path, relative_path: str) -> None:
    """Each load-bearing config/doc file is present at the expected path."""
    target = repo_root / relative_path
    assert target.is_file(), f"required project file is missing: {relative_path}"
