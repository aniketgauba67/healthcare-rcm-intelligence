"""Contract tests for the FY2023 REFERENCE code sets.

Config-level invariants (vintage, §3.7 CARC labels-only, alignment with the
simulation's CARC groups) run everywhere. Parser invariants require the raw
zips on disk, so they skip cleanly in CI (no data) — the same pattern as the
checksum-reconciliation test.
"""

from __future__ import annotations

import re

import pytest
import yaml

from src.ingestion import reference_codes as rc
from src.ingestion.paths import REPO_ROOT
from src.ingestion.sources import get_source, load_sources

_LEVEL2 = re.compile(r"^[A-CE-Z][0-9]{4}$")
_REF_SOURCES = ("icd10", "hcpcs", "ms_drg", "carc_codes")


def _zips_present() -> bool:
    for source in ("icd10", "hcpcs", "ms_drg"):
        for art in get_source(source).get("artifacts", {}).values():
            if not (REPO_ROOT / art["filename"]).exists():
                return False
    return True


# --------------------------------------------------------------------------- #
# Config invariants (no data files required)
# --------------------------------------------------------------------------- #
def test_reference_sources_present_and_classified():
    doc = load_sources()["sources"]
    for source in _REF_SOURCES:
        assert source in doc, f"{source} missing from sources.yaml"
        assert doc[source]["classification"] == "REFERENCE", source


def test_reference_vintages_match_claims_period():
    # §2 vintage rule: claims are 2023-04 → FY2023/2023 code sets, never ICD-9.
    doc = load_sources()["sources"]
    for source in ("icd10", "hcpcs", "ms_drg"):
        for name, art in doc[source]["artifacts"].items():
            assert art["vintage"] in {"FY2023", "2023"}, f"{source}:{name} wrong vintage"
            assert art.get("sha256"), f"{source}:{name} missing sha256"


def test_carc_is_labels_only_no_file():
    # §3.7: CARC used as category LABELS only; no X12 file, no reproduced text.
    carc = load_sources()["sources"]["carc_codes"]
    assert carc["artifacts"] == {}, "CARC must record no downloadable artifact (§3.7)"


def test_carc_labels_align_with_simulation_groups():
    # The ref_carc taxonomy must cover exactly the CARC groups the simulation
    # emits, so the naming enrichment join never misses or invents a category.
    sim = yaml.safe_load((REPO_ROOT / "config" / "simulation.yaml").read_text())
    sim_groups = {str(c["carc_group"]) for c in sim["denial_categories"]["catalog"]}
    carc = rc.parse_carc()
    assert set(carc["carc_code"]) == sim_groups, "ref_carc drifted from simulation carc_groups"
    # Labels are project-authored short strings, not copyrighted X12 descriptions.
    for label in carc["category_label"]:
        assert 0 < len(label) <= 60, f"CARC label not a short project label: {label!r}"


# --------------------------------------------------------------------------- #
# Parser invariants (need the raw zips; skip in CI)
# --------------------------------------------------------------------------- #
def test_hcpcs_is_level_two_only():
    if not _zips_present():
        pytest.skip("reference zips not downloaded")
    h = rc.parse_hcpcs()
    assert len(h) > 0
    assert h["hcpcs_code"].is_unique
    assert h["hcpcs_code"].str.match(_LEVEL2).all(), "non-Level-II HCPCS code present (§3.7)"
    assert not h["hcpcs_code"].str.startswith("D").any(), "D-series (ADA) must be excluded (§3.7)"


def test_icd10_codes_are_fy2023_dotless():
    if not _zips_present():
        pytest.skip("reference zips not downloaded")
    cm = rc.parse_icd10cm()
    pcs = rc.parse_icd10pcs()
    assert cm["icd10cm_code"].is_unique and pcs["icd10pcs_code"].is_unique
    assert not cm["icd10cm_code"].str.contains(r"\.").any(), "ICD-10-CM must be dotless tabular"
    assert (cm["long_desc"].str.len() > 0).all()
    assert (pcs["icd10pcs_code"].str.len() == 7).all(), "ICD-10-PCS codes are 7-char"


def test_msdrg_codes_zero_padded_and_titled():
    if not _zips_present():
        pytest.skip("reference zips not downloaded")
    d = rc.parse_msdrg()
    assert d["drg_cd"].is_unique
    assert (d["drg_cd"].str.match(r"^\d{3}$")).all(), "drg_cd must be 3-digit zero-padded"
    assert (d["drg_title"].str.len() > 0).all(), "every MS-DRG needs a title"
    assert {"001", "999"} <= set(d["drg_cd"]), "expected boundary DRGs present"
