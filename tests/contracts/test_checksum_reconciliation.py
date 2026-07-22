"""Closed-loop artifact reconciliation against committed sources.yaml.

qa's Phase-1 acceptance note: on a fresh clone the gitignored manifest is
absent, so the first download verifies nothing against a known-good value. This
test closes the loop — every artifact recorded in config/sources.yaml with a
committed sha256/size/row_count is re-verified against the file on disk. Files
not yet downloaded are skipped, so CI (no data) stays green while any present
artifact is held to the committed checksum.
"""

from __future__ import annotations

import pytest

from src.ingestion.manifest import count_csv_data_rows, sha256_file
from src.ingestion.paths import REPO_ROOT
from src.ingestion.sources import load_sources


def _committed_artifacts():
    """Yield (source, name, artifact) for every artifact with a committed sha256."""
    doc = load_sources()
    for source, cfg in doc.get("sources", {}).items():
        for name, art in (cfg.get("artifacts") or {}).items():
            if isinstance(art, dict) and art.get("sha256") and art.get("filename"):
                yield source, name, art


_ARTIFACTS = list(_committed_artifacts())


def test_some_artifacts_are_committed():
    # Guard against the loader silently recording nothing.
    assert _ARTIFACTS, "no artifacts with committed sha256 in sources.yaml"


@pytest.mark.parametrize("source,name,art", _ARTIFACTS, ids=[f"{s}:{n}" for s, n, _ in _ARTIFACTS])
def test_downloaded_artifact_matches_committed_checksum(source, name, art):
    path = REPO_ROOT / art["filename"]
    if not path.exists():
        pytest.skip(f"{art['filename']} not downloaded; run `make ingest`")

    assert sha256_file(path) == art["sha256"], f"{name}: sha256 drift from sources.yaml"
    if "size_bytes" in art:
        assert path.stat().st_size == art["size_bytes"], f"{name}: size drift"
    if art.get("row_count") is not None and str(path).endswith(".csv"):
        assert count_csv_data_rows(path) == art["row_count"], f"{name}: row_count drift"
