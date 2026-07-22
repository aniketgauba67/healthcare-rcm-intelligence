"""Typed access to config/sources.yaml."""

from __future__ import annotations

from typing import Any

import yaml

from .paths import SOURCES_YAML


def load_sources() -> dict[str, Any]:
    """Load the full sources.yaml document."""
    with SOURCES_YAML.open() as fh:
        return yaml.safe_load(fh)


def get_source(name: str) -> dict[str, Any]:
    """Return the config block for one source, raising if it is absent."""
    doc = load_sources()
    sources = doc.get("sources", {})
    if name not in sources:
        raise KeyError(f"source '{name}' not found in {SOURCES_YAML}")
    return sources[name]
