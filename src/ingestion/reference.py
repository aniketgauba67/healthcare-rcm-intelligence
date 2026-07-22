"""Download public REFERENCE files (facility/provider directories).

These are real-entity directories (real CCNs, real NPIs). Per CLAUDE.md §3.4
they are linked to the synthetic claims ONLY through the seeded, SIMULATED
crosswalk — never joined directly.
"""

from __future__ import annotations

import csv

from .http import download
from .logging_utils import get_logger, log_event
from .manifest import Manifest, ManifestEntry, count_csv_data_rows, sha256_file
from .paths import DATA_RAW, REPO_ROOT
from .sources import get_source

_LOGGER = get_logger("ingestion.reference")

# NPPES/CMS flat files carry legacy single-byte encoding; latin-1 round-trips.
_ENCODING = "latin-1"


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


def _extract_columns(src_path, dst_path, keep_columns: list[str]) -> int:
    """Stream a big CSV to a compact extract with only `keep_columns`."""
    with (
        src_path.open(encoding=_ENCODING, newline="") as fin,
        dst_path.open("w", encoding="utf-8", newline="") as fout,
    ):
        reader = csv.reader(fin)
        writer = csv.writer(fout)
        header = next(reader)
        try:
            idx = [header.index(c) for c in keep_columns]
        except ValueError as exc:
            raise KeyError(f"missing expected column: {exc}") from exc
        writer.writerow(keep_columns)
        kept = 0
        for row in reader:
            writer.writerow([row[i] for i in idx])
            kept += 1
    return kept


def download_medicare_providers(manifest: Manifest, *, force: bool = False) -> Manifest:
    """Download the nationwide Medicare provider pool (real NPIs/specialty/state).

    Checksums the full source CSV, keeps only a compact column extract for the
    SIMULATED crosswalk, and deletes the multi-hundred-MB source unless kept.
    """
    source = "medicare_providers"
    cfg = get_source(source)
    url = cfg["csv_url"]
    full = DATA_RAW / "reference" / "medicare_providers_full.csv"
    extract = DATA_RAW / "reference" / "medicare_providers_extract.csv"

    src_key = f"{source}:source_csv"
    prior = manifest.get(src_key)
    digest = download(
        url, full, _LOGGER, expected_sha256=prior.sha256 if prior else None, force=force
    )
    full_size = full.stat().st_size
    full_rows = count_csv_data_rows(full)
    manifest.put(
        ManifestEntry(
            key=src_key,
            source=source,
            role="source_csv",
            classification=cfg["classification"],
            url=url,
            filename=str(full.relative_to(REPO_ROOT)),
            vintage=cfg["vintage"],
            sha256=digest,
            size_bytes=full_size,
            row_count=full_rows,
            license_note=cfg["license_note"],
            notes="Full source; checksummed then deleted after column extraction.",
        )
    )

    kept = _extract_columns(full, extract, cfg["extract_columns"])
    ex_sha = sha256_file(extract)
    ex_size = extract.stat().st_size
    manifest.put(
        ManifestEntry(
            key=f"{source}:extract",
            source=source,
            role="reference",
            classification=cfg["classification"],
            url=url,
            filename=str(extract.relative_to(REPO_ROOT)),
            vintage=cfg["vintage"],
            sha256=ex_sha,
            size_bytes=ex_size,
            row_count=kept,
            license_note=cfg["license_note"],
            notes=f"Compact provider pool ({', '.join(cfg['extract_columns'])}).",
        )
    )
    log_event(
        _LOGGER,
        "reference.recorded",
        source=source,
        providers=kept,
        size_bytes=ex_size,
        sha256=ex_sha,
    )
    if not cfg.get("keep_full", False):
        full.unlink(missing_ok=True)
        log_event(_LOGGER, "reference.full_removed", path=str(full))
    return manifest
