"""Streaming HTTP download with checksum verification and idempotency.

Downloads never buffer a whole file in memory; they stream to a temp file and
atomically rename on success so a partial download can never masquerade as a
complete one. A download is skipped when the destination already exists and
its SHA-256 matches the value recorded in the manifest.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from .logging_utils import log_event
from .manifest import sha256_file

_CHUNK = 1 << 20  # 1 MiB
_TIMEOUT = (30, 300)  # (connect, read) seconds
_HEADERS = {"User-Agent": "healthcare-rcm-intelligence/0.1 (data-engineer ingestion)"}


def head_content_length(url: str) -> int | None:
    """Return the server-reported Content-Length, or None if not advertised."""
    resp = requests.head(url, headers=_HEADERS, allow_redirects=True, timeout=_TIMEOUT)
    resp.raise_for_status()
    cl = resp.headers.get("Content-Length")
    return int(cl) if cl is not None else None


def download(
    url: str,
    dest: Path,
    logger: logging.Logger,
    *,
    expected_sha256: str | None = None,
    force: bool = False,
) -> str:
    """Stream `url` to `dest`, returning the SHA-256 of the stored file.

    Idempotent: if `dest` exists and (when provided) its checksum matches
    `expected_sha256`, the download is skipped. Passing `force=True` always
    re-downloads.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        existing = sha256_file(dest)
        if expected_sha256 is None or existing == expected_sha256:
            log_event(logger, "download.skip_cached", url=url, dest=str(dest), sha256=existing)
            return existing
        log_event(
            logger,
            "download.checksum_mismatch_redownload",
            url=url,
            dest=str(dest),
            found=existing,
            expected=expected_sha256,
        )

    tmp = dest.with_suffix(dest.suffix + ".part")
    bytes_written = 0
    log_event(logger, "download.start", url=url, dest=str(dest))
    with requests.get(
        url, headers=_HEADERS, stream=True, allow_redirects=True, timeout=_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if chunk:
                    fh.write(chunk)
                    bytes_written += len(chunk)
    tmp.replace(dest)  # atomic on same filesystem

    digest = sha256_file(dest)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"checksum mismatch for {url}: expected {expected_sha256}, got {digest}")
    log_event(
        logger,
        "download.done",
        url=url,
        dest=str(dest),
        size_bytes=bytes_written,
        sha256=digest,
    )
    return digest
