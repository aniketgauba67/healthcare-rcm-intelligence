"""Contracts for public hosted links and non-secret screenshot evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
HOSTED_GUIDE = REPO_ROOT / "docs" / "hosted_deployment.md"
IMAGE_DIR = REPO_ROOT / "docs" / "images" / "hosted"
SOURCE_SHA = "119828e8915044622faa65755a615375799df0fc"
PUBLIC_URLS = (
    "https://pzcgc7diz3azrawrcsxobm.streamlit.app/",
    "https://healthcare-rcm-intelligence-api.onrender.com/ready",
    "https://healthcare-rcm-intelligence-api.onrender.com/docs",
)
EXPECTED_IMAGES = (
    "api-openapi.png",
    "api-readiness.png",
    "neon-schema-contract.png",
    "render-service-status.png",
    "streamlit-application-status.png",
    "streamlit-ar-recovery.png",
    "streamlit-denial-prevention.png",
    "streamlit-executive-overview.png",
    "streamlit-model-data-quality.png",
    "streamlit-overview.png",
    "streamlit-reconciliation-17-of-17.png",
    "streamlit-work-queue.png",
)


def test_public_links_and_release_limitations_are_documented() -> None:
    readme = README.read_text()
    guide = HOSTED_GUIDE.read_text()
    normalized_readme = " ".join(readme.split())
    normalized_guide = " ".join(guide.split())

    for url in PUBLIC_URLS:
        assert url in readme
        assert url in guide

    combined = f"{readme}\n{guide}"
    assert "Phase 5 remains under QA" in combined
    assert "not production-grade or always-on" in readme
    assert "not accepted or deployed" not in readme
    assert "hosted portfolio demo is live" in normalized_readme
    assert "git_tree_dirty=true" in guide
    assert "remains non-final" in combined
    assert "No paid resource" in normalized_guide


def test_hosted_screenshot_manifest_is_complete_and_images_are_distinct() -> None:
    guide = HOSTED_GUIDE.read_text()
    assert SOURCE_SHA in guide

    digests: set[str] = set()
    for name in EXPECTED_IMAGES:
        assert f"`{name}`" in guide
        path = IMAGE_DIR / name
        assert path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(path) as screenshot:
            assert screenshot.format == "PNG"
            width, height = screenshot.size
            assert width >= 900
            assert height >= 600
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest not in digests, f"duplicate hosted screenshot: {name}"
        digests.add(digest)
