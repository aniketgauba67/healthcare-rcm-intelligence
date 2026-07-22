"""Contract tests for the manifest primitives."""

from __future__ import annotations

import hashlib

from src.ingestion.manifest import (
    Manifest,
    ManifestEntry,
    count_csv_data_rows,
    sha256_file,
)


def _entry(key: str = "src:x") -> ManifestEntry:
    return ManifestEntry(
        key=key,
        source="cms_synthetic_claims",
        role="enrollment",
        classification="SOURCE",
        url="https://example/x.csv",
        filename="data/raw/cms_synthetic/x.csv",
        vintage="2023-04",
        sha256="0" * 64,
        size_bytes=10,
        row_count=1,
        license_note="note",
    )


def test_sha256_file_matches_hashlib(tmp_path):
    p = tmp_path / "f.bin"
    payload = b"hello world\n" * 1000
    p.write_bytes(payload)
    assert sha256_file(p) == hashlib.sha256(payload).hexdigest()


def test_count_csv_data_rows_excludes_header(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("h1,h2\na,1\nb,2\nc,3\n")
    assert count_csv_data_rows(p) == 3


def test_count_csv_data_rows_no_trailing_newline(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("h1,h2\na,1\nb,2")  # last row has no newline
    assert count_csv_data_rows(p) == 2


def test_count_csv_data_rows_header_only(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("h1,h2\n")
    assert count_csv_data_rows(p) == 0


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest()
    m.put(_entry("a"))
    m.put(_entry("b"))
    m.save(path)

    loaded = Manifest.load(path)
    assert set(loaded.entries) == {"a", "b"}
    assert loaded.get("a").vintage == "2023-04"
    assert loaded.get("a").classification == "SOURCE"


def test_manifest_load_missing_returns_empty(tmp_path):
    assert Manifest.load(tmp_path / "nope.json").entries == {}
