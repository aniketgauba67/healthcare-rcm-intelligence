"""Download public REFERENCE files (facility/provider directories).

These are real-entity directories (real CCNs, real NPIs). Per CLAUDE.md §3.4
they are linked to the synthetic claims ONLY through the seeded, SIMULATED
crosswalk — never joined directly.
"""

from __future__ import annotations

from .http import download
from .logging_utils import get_logger, log_event
from .manifest import Manifest, ManifestEntry, count_csv_data_rows
from .paths import DATA_RAW, REPO_ROOT
from .sources import get_source

_LOGGER = get_logger("ingestion.reference")


def download_hospital_general_information(manifest: Manifest, *, force: bool = False) -> Manifest:
    """Download CMS Hospital General Information (real facility directory)."""
    source = "hospital_general_information"
    cfg = get_source(source)
    url = cfg["csv_url"]
    dest = DATA_RAW / "reference" / "hospital_general_information.csv"
    key = f"{source}:hospital_general_information"

    prior = manifest.get(key)
    digest = download(
        url, dest, _LOGGER, expected_sha256=prior.sha256 if prior else None, force=force
    )
    size = dest.stat().st_size
    rows = count_csv_data_rows(dest)
    manifest.put(
        ManifestEntry(
            key=key,
            source=source,
            role="reference",
            classification=cfg["classification"],
            url=url,
            filename=str(dest.relative_to(REPO_ROOT)),
            vintage=cfg.get("vintage", ""),
            sha256=digest,
            size_bytes=size,
            row_count=rows,
            license_note=cfg["license_note"],
            notes="Real CCNs; linked to synthetic claims only via SIMULATED crosswalk.",
        )
    )
    log_event(
        _LOGGER,
        "reference.recorded",
        source=source,
        size_bytes=size,
        row_count=rows,
        sha256=digest,
    )
    return manifest
