"""Download manifest: the record of every raw artifact we fetch.

Each entry captures exactly what CLAUDE.md's data provenance rules require:
source URL, release vintage, SHA-256 checksum, byte size, and row count.
The manifest is the idempotency ledger — a re-run that finds a matching
checksum on disk skips the download.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .paths import RAW_MANIFEST

_CHUNK = 1 << 20  # 1 MiB streaming chunk; never load whole files into memory.


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file, read in streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def count_csv_data_rows(path: Path) -> int:
    """Count data rows (total newlines minus the header) in a text file.

    Streams the file so it works on multi-GB extracts. Assumes exactly one
    header line, which holds for the CMS and NPPES flat files.
    """
    size = path.stat().st_size
    if size == 0:
        return 0
    total = 0
    last_byte = b"\n"
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            total += block.count(b"\n")
            last_byte = block[-1:]
    # If the final line lacks a trailing newline, its row was not counted.
    if last_byte != b"\n":
        total += 1
    return max(total - 1, 0)


@dataclass
class ManifestEntry:
    """One downloaded artifact."""

    key: str  # logical id, e.g. "cms_synthetic:inpatient"
    source: str  # source group in sources.yaml, e.g. "cms_synthetic_claims"
    role: str  # semantic role, e.g. "enrollment", "claims_inpatient", "reference"
    classification: str  # SOURCE | REFERENCE | DERIVED | SIMULATED
    url: str
    filename: str  # path relative to data/raw
    vintage: str
    sha256: str
    size_bytes: int
    row_count: int | None
    license_note: str
    downloaded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    notes: str = ""


class Manifest:
    """Load/save the JSON manifest keyed by logical artifact id."""

    def __init__(self, entries: dict[str, ManifestEntry] | None = None) -> None:
        self.entries: dict[str, ManifestEntry] = entries or {}

    @classmethod
    def load(cls, path: Path = RAW_MANIFEST) -> "Manifest":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls({k: ManifestEntry(**v) for k, v in raw.items()})

    def save(self, path: Path = RAW_MANIFEST) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in sorted(self.entries.items())}
        path.write_text(json.dumps(payload, indent=2) + "\n")

    def get(self, key: str) -> ManifestEntry | None:
        return self.entries.get(key)

    def put(self, entry: ManifestEntry) -> None:
        self.entries[entry.key] = entry
