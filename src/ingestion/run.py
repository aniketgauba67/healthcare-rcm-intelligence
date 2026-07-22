"""Ingestion CLI: idempotent download of raw sources into data/raw.

Usage:
    python -m src.ingestion.run                 # download the full Phase-1 subset
    python -m src.ingestion.run --only cms_synthetic
    python -m src.ingestion.run --only nppes --state RI
    python -m src.ingestion.run --force         # re-download and re-verify

Re-runnable: files already present with a matching checksum are skipped.
"""

from __future__ import annotations

import argparse

from .cms_synthetic import download_synthetic_subset
from .logging_utils import get_logger, log_event
from .manifest import Manifest
from .nppes import download_nppes_extract
from .paths import RAW_MANIFEST, ensure_raw_dirs

_LOGGER = get_logger("ingestion.run")

_SOURCES = ("cms_synthetic", "nppes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download raw RCM sources.")
    parser.add_argument(
        "--only",
        choices=_SOURCES,
        action="append",
        help="Restrict to one source (repeatable). Default: all.",
    )
    parser.add_argument("--state", default=None, help="Override NPPES filter state (e.g. RI).")
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if checksum matches."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_raw_dirs()
    manifest = Manifest.load()
    selected = args.only or list(_SOURCES)
    log_event(_LOGGER, "ingest.start", sources=selected, force=args.force)

    if "cms_synthetic" in selected:
        download_synthetic_subset(manifest, force=args.force)
        manifest.save()  # persist after each source so partial runs are durable
    if "nppes" in selected:
        download_nppes_extract(manifest, state=args.state, force=args.force)
        manifest.save()

    log_event(
        _LOGGER,
        "ingest.done",
        artifacts=len(manifest.entries),
        manifest=str(RAW_MANIFEST),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
