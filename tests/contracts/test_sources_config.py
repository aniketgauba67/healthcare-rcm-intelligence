"""Contract tests for config/sources.yaml integrity."""

from __future__ import annotations

from src.ingestion.sources import load_sources

_VALID_CLASSIFICATIONS = {"SOURCE", "REFERENCE", "DERIVED", "SIMULATED"}


def test_all_sources_have_required_fields():
    doc = load_sources()
    for name, cfg in doc["sources"].items():
        assert cfg.get("name"), f"{name} missing name"
        assert cfg.get("url"), f"{name} missing url"
        assert cfg["classification"] in _VALID_CLASSIFICATIONS, name
        assert cfg.get("license_note"), f"{name} missing license_note"


def test_no_ingested_source_is_classified_simulated():
    # Provenance rule (CLAUDE.md §3): downloaded raw sources are never SIMULATED.
    doc = load_sources()
    for name, cfg in doc["sources"].items():
        assert cfg["classification"] != "SIMULATED", name


def test_synthetic_subset_members_exist_in_catalog():
    cfg = load_sources()["sources"]["cms_synthetic_claims"]
    available = cfg["available_files"]
    for member in cfg["download_subset"]:
        assert member in available, f"subset member {member} not in available_files"
        assert available[member].startswith("https://data.cms.gov/"), member


def test_synthetic_subset_covers_enrollment_and_a_claim_type():
    cfg = load_sources()["sources"]["cms_synthetic_claims"]
    subset = cfg["download_subset"]
    assert any(m.startswith("beneficiary_") for m in subset), "no enrollment file"
    assert any(not m.startswith("beneficiary_") for m in subset), "no claim file"


def test_nppes_config_has_filter_state_and_columns():
    cfg = load_sources()["sources"]["nppes_npi"]
    assert cfg["filter_state"]
    assert cfg["state_column"]
    assert cfg["monthly_file_url"].startswith("https://download.cms.gov/")
