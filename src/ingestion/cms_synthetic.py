"""Download the CMS Synthetic Medicare RIF subset (enrollment + claims)."""

from __future__ import annotations

from .http import download
from .logging_utils import get_logger, log_event
from .manifest import Manifest, ManifestEntry, count_csv_data_rows
from .paths import DATA_RAW, REPO_ROOT
from .sources import get_source

_LOGGER = get_logger("ingestion.cms_synthetic")
_SOURCE = "cms_synthetic_claims"

# Which subset members are enrollment vs. claims, for the manifest `role`.
_ENROLLMENT_PREFIX = "beneficiary_"


def _role_for(member: str) -> str:
    if member.startswith(_ENROLLMENT_PREFIX):
        return "enrollment"
    return f"claims_{member}"


def download_synthetic_subset(
    manifest: Manifest, *, members: list[str] | None = None, force: bool = False
) -> Manifest:
    """Download the configured synthetic RIF subset and record manifest entries.

    Idempotent: files already present with a matching recorded checksum are
    skipped.
    """
    cfg = get_source(_SOURCE)
    available: dict[str, str] = cfg["available_files"]
    subset = members if members is not None else cfg["download_subset"]
    dest_dir = DATA_RAW / "cms_synthetic"

    for member in subset:
        if member not in available:
            raise KeyError(f"{member} not in cms_synthetic_claims.available_files")
        url = available[member]
        dest = dest_dir / f"{member}.csv"
        key = f"{_SOURCE}:{member}"
        prior = manifest.get(key)
        expected = prior.sha256 if prior else None

        digest = download(url, dest, _LOGGER, expected_sha256=expected, force=force)
        size = dest.stat().st_size
        rows = count_csv_data_rows(dest)
        manifest.put(
            ManifestEntry(
                key=key,
                source=_SOURCE,
                role=_role_for(member),
                classification=cfg["classification"],
                url=url,
                filename=str(dest.relative_to(REPO_ROOT)),
                vintage=cfg["vintage"],
                sha256=digest,
                size_bytes=size,
                row_count=rows,
                license_note=cfg["license_note"],
            )
        )
        log_event(
            _LOGGER,
            "cms_synthetic.recorded",
            member=member,
            size_bytes=size,
            row_count=rows,
            sha256=digest,
        )
    return manifest
