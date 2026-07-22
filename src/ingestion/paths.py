"""Canonical filesystem paths for the ingestion layer.

Every path is derived from the repository root so that scripts behave
identically regardless of the current working directory (agent bash calls
reset cwd between invocations).
"""

from __future__ import annotations

from pathlib import Path

# src/ingestion/paths.py -> repo root is three parents up.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

CONFIG_DIR: Path = REPO_ROOT / "config"
SOURCES_YAML: Path = CONFIG_DIR / "sources.yaml"

DATA_DIR: Path = REPO_ROOT / "data"
DATA_RAW: Path = DATA_DIR / "raw"
DATA_VALIDATED: Path = DATA_DIR / "validated"
DATA_CURATED: Path = DATA_DIR / "curated"

# Machine-readable download manifest. Lives under data/raw so it is gitignored
# (never committed); the committed, human-readable record is config/sources.yaml.
RAW_MANIFEST: Path = DATA_RAW / "manifest.json"


def ensure_raw_dirs() -> None:
    """Create the raw-data subtree if it does not yet exist."""
    for sub in ("nppes", "cms_synthetic", "reference"):
        (DATA_RAW / sub).mkdir(parents=True, exist_ok=True)
