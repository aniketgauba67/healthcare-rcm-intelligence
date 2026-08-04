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
    "https://3a3xhz4rrshqdjapzwflxg.streamlit.app/?embed=true",
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

    # Normalize away line wrapping AND markdown blockquote markers before matching.
    # These assertions are about what the page SAYS, not how it is laid out; a
    # sentence wrapped across two `>` lines is the same sentence.
    def _flat(text: str) -> str:
        return " ".join(line.lstrip("> ").strip() for line in text.splitlines()).replace("  ", " ")

    normalized_readme = " ".join(_flat(readme).split())
    normalized_guide = " ".join(_flat(guide).split())

    for url in PUBLIC_URLS:
        assert url in readme
        assert url in guide

    # LIMITATIONS MUST BE DISCLOSED. These are the honest-framing requirements and
    # they stay.
    assert "not production-grade or always-on" in normalized_readme
    assert "git_tree_dirty=false" in guide
    assert "No paid resource" in normalized_guide

    # ATTRIBUTION, NOT VERDICT.
    #
    # This block used to require the README to SAY "Phase 5 is independently
    # QA-accepted" and to forbid the strings "Phase 5 remains under QA", "pending
    # final artifact QA" and "not accepted or deployed". That is a test asserting
    # the absence of caution, and it had teeth: correcting the status claim to
    # something accurate made this file RED, so the test would have argued for
    # keeping an overstatement on a public artifact. A gate may require a claim to
    # be SUPPORTED. It must never require the claim to be POSITIVE.
    #
    # What is worth enforcing is that "QA-accepted" never appears unqualified. The
    # phrase means an internal review by this project's own reviewer agent, and a
    # reader cannot know that unless the page says so.
    if "QA-accepted" in normalized_readme or "QA accepted" in normalized_readme:
        assert "internal review process, not an audit by an outside party" in normalized_readme, (
            "the README claims QA acceptance without saying what that means. It is an internal "
            "review by this project's own agent, not third-party assurance, and an unqualified "
            "claim on a public page reads as the latter."
        )


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
