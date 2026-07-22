"""NPPES state-filtered extract.

The full NPPES monthly dissemination is ~1.14 GB zipped / ~9 GB unzipped. We
never materialise the 9 GB CSV: the main entity file is streamed straight out
of the zip, filtered to a single state row-by-row, and only the matching rows
are written to a compact extract. Both the source zip and the extract are
recorded in the manifest (the zip is deleted afterwards by default).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import TextIO

from .http import download
from .logging_utils import get_logger, log_event
from .manifest import Manifest, ManifestEntry, count_csv_data_rows, sha256_file
from .paths import DATA_RAW, REPO_ROOT
from .sources import get_source

_LOGGER = get_logger("ingestion.nppes")
_SOURCE = "nppes_npi"

# NPPES flat files carry legacy single-byte encoding in some name fields;
# latin-1 round-trips every byte and never raises, preserving source fidelity
# in the raw extract. Encoding normalisation happens in the validated layer.
_ENCODING = "latin-1"


def _find_main_member(zf: zipfile.ZipFile, prefix: str) -> str:
    """Return the main npidata member (excludes the *_fileheader.csv stub)."""
    candidates = [
        n
        for n in zf.namelist()
        if Path(n).name.lower().startswith(prefix.lower())
        and n.lower().endswith(".csv")
        and "fileheader" not in n.lower()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"no member starting with '{prefix}' found in zip: {zf.namelist()[:8]}"
        )
    # The bulk data file is by far the largest matching member.
    return max(candidates, key=lambda n: zf.getinfo(n).file_size)


def stream_filter(
    src: TextIO, dst: TextIO, *, state_column: str, state_value: str
) -> tuple[int, int]:
    """Copy header + rows whose `state_column` equals `state_value`.

    Returns (rows_scanned, rows_kept). Streams row-by-row so it is safe on
    multi-GB inputs. Raises KeyError if the state column is absent.
    """
    reader = csv.reader(src)
    writer = csv.writer(dst)
    header = next(reader)
    try:
        state_idx = header.index(state_column)
    except ValueError as exc:
        raise KeyError(
            f"state column '{state_column}' not in NPPES header ({len(header)} cols)"
        ) from exc
    writer.writerow(header)

    scanned = 0
    kept = 0
    target = state_value.upper()
    for row in reader:
        scanned += 1
        if state_idx < len(row) and row[state_idx].strip().upper() == target:
            writer.writerow(row)
            kept += 1
    return scanned, kept


def download_nppes_extract(
    manifest: Manifest, *, state: str | None = None, force: bool = False
) -> Manifest:
    """Download the NPPES monthly zip and build a single-state extract."""
    cfg = get_source(_SOURCE)
    state_value = (state or cfg["filter_state"]).upper()
    url = cfg["monthly_file_url"]
    dest_dir = DATA_RAW / "nppes"
    zip_path = dest_dir / Path(url).name
    extract_path = dest_dir / f"nppes_{state_value.lower()}_extract.csv"

    # 1. Download the monthly zip (idempotent via recorded checksum).
    zip_key = f"{_SOURCE}:monthly_zip"
    prior_zip = manifest.get(zip_key)
    zip_sha = download(
        url,
        zip_path,
        _LOGGER,
        expected_sha256=prior_zip.sha256 if prior_zip else None,
        force=force,
    )
    zip_size = zip_path.stat().st_size
    manifest.put(
        ManifestEntry(
            key=zip_key,
            source=_SOURCE,
            role="source_zip",
            classification=cfg["classification"],
            url=url,
            filename=str(zip_path.relative_to(REPO_ROOT)),
            vintage=cfg["vintage"],
            sha256=zip_sha,
            size_bytes=zip_size,
            row_count=None,
            license_note=cfg["license_note"],
            notes="Full monthly NPPES dissemination; not committed; deleted after extract by default.",
        )
    )

    # 2. Stream-filter the main member to the state extract.
    log_event(_LOGGER, "nppes.filter_start", state=state_value, zip=str(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        member = _find_main_member(zf, cfg["main_member_prefix"])
        log_event(_LOGGER, "nppes.main_member", member=member)
        with zf.open(member, "r") as raw:
            src = io.TextIOWrapper(raw, encoding=_ENCODING, newline="")
            with extract_path.open("w", encoding=_ENCODING, newline="") as dst:
                scanned, kept = stream_filter(
                    src,
                    dst,
                    state_column=cfg["state_column"],
                    state_value=state_value,
                )
    log_event(_LOGGER, "nppes.filter_done", state=state_value, scanned=scanned, kept=kept)

    extract_sha = sha256_file(extract_path)
    extract_size = extract_path.stat().st_size
    extract_rows = count_csv_data_rows(extract_path)
    if extract_rows != kept:  # cross-check writer vs. independent line count
        raise ValueError(f"NPPES extract row mismatch: wrote {kept}, counted {extract_rows}")
    manifest.put(
        ManifestEntry(
            key=f"{_SOURCE}:extract_{state_value.lower()}",
            source=_SOURCE,
            role="reference",
            classification=cfg["classification"],
            url=url,
            filename=str(extract_path.relative_to(REPO_ROOT)),
            vintage=cfg["vintage"],
            sha256=extract_sha,
            size_bytes=extract_size,
            row_count=extract_rows,
            license_note=cfg["license_note"],
            notes=(
                f"State={state_value} extract of main member '{Path(member).name}'; "
                f"{kept} of {scanned} providers retained."
            ),
        )
    )
    log_event(
        _LOGGER,
        "nppes.recorded",
        state=state_value,
        providers=extract_rows,
        size_bytes=extract_size,
        sha256=extract_sha,
    )

    # 3. Drop the 1.14 GB zip unless explicitly kept.
    if not cfg.get("keep_zip_after_extract", False):
        zip_path.unlink(missing_ok=True)
        log_event(_LOGGER, "nppes.zip_removed", zip=str(zip_path))

    return manifest
