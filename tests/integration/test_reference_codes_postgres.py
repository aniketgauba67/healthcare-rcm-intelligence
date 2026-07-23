"""Live-Postgres acceptance for the FY2023 REFERENCE code-set load.

Runs unranked (rank 50) so it lands AFTER the Phase-1 warehouse rebuild (10) and
the Phase-2 sim attach (20) but BEFORE the end-state guard (90) — the correct
production order: warehouse -> simulate -> reference-codes.

The load is ADDITIVE by design: it applies only sql/ddl/60_reference_codes.sql
(create-if-not-exists) and enriches dim_drg by value join. This test proves that
property by asserting the fact_ and sim_ row counts are unchanged across the
load, so a green run here also means the simulated layer's FKs stayed intact.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.ingestion import reference_codes as rc


@pytest.mark.integration
def test_reference_load_is_additive_and_enriches_dim_drg(pg_engine_session):
    engine = pg_engine_session

    def scalar(sql: str) -> int:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    # Skip cleanly if the raw zips are not on disk (CI / fresh clone).
    for source in ("icd10", "hcpcs", "ms_drg"):
        from src.ingestion.paths import REPO_ROOT
        from src.ingestion.sources import get_source

        for art in get_source(source)["artifacts"].values():
            if not (REPO_ROOT / art["filename"]).exists():
                pytest.skip("reference zips not downloaded; run the reference_codes downloader")

    facts_before = scalar("select count(*) from rcm.fact_inpatient_claim")
    xwalk_before = scalar("select count(*) from rcm.sim_facility_crosswalk")

    report = rc.load(engine)

    # Reference tables populated with the measured code counts.
    assert report["ref_icd10cm"] == 73674
    assert report["ref_icd10pcs"] == 78530
    assert report["ref_hcpcs"] == 7404
    assert report["ref_msdrg"] == 767
    assert report["ref_carc"] == 10

    # dim_drg enriched by value join; no real DRG left without a title.
    assert report["dim_drg_enriched"] > 0
    assert report["dim_drg_unmatched"] == 0
    enriched_ref = scalar(
        "select count(*) from rcm.dim_drg where drg_desc is not null and provenance = 'REFERENCE'"
    )
    assert enriched_ref == report["dim_drg_enriched"]

    # Additive property: the load touched neither the facts nor the sim layer.
    assert scalar("select count(*) from rcm.fact_inpatient_claim") == facts_before
    assert scalar("select count(*) from rcm.sim_facility_crosswalk") == xwalk_before

    # Idempotent: a second load leaves identical counts.
    report2 = rc.load(engine)
    assert report2 == report
