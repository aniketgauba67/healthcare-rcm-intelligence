"""Download + load the official code-set REFERENCE tables (FY2023 vintage).

Covers the code sets that give SOURCE claim attributes human-readable names:
ICD-10-CM (diagnoses), ICD-10-PCS (procedures), HCPCS Level II, MS-DRG v40, and
a §3.7-clean CARC label taxonomy. Every code set matches the 2023-04 claims
vintage (CLAUDE.md §2): FY2023 ICD-10, 2023 HCPCS, MS-DRG v40 / IPPS FY2023.

Two phases, both idempotent and re-runnable:

    uv run python -m src.ingestion.reference_codes --download   # fetch + checksum zips
    uv run python -m src.ingestion.reference_codes --load       # (re)load into Postgres
    uv run python -m src.ingestion.reference_codes              # download then load

The LOAD is deliberately ADDITIVE: it applies only sql/ddl/60_reference_codes.sql
(create-if-not-exists) and truncate+inserts the ref_* tables, then enriches
dim_drg.drg_desc by value join. It NEVER drops or reloads fact_* / sim_* tables,
so a live simulation layer and its FKs stay valid (contrast `make warehouse`).

CLAUDE.md §3.7 compliance:
  - HCPCS: only Level II codes (letter + 4 digits) are kept. CPT Level I
    (5-digit numeric, AMA-licensed), 2-char modifiers, and the D-series
    (dental CDT, ADA-copyright) are excluded at parse time.
  - CARC: no X12 file is downloaded and NO X12 description text is stored; the
    ref_carc table pairs public CARC code identifiers with project-authored
    category labels only.
"""

from __future__ import annotations

import argparse
import io
import re
import zipfile

import pandas as pd

from .http import download
from .logging_utils import get_logger, log_event
from .manifest import Manifest, ManifestEntry
from .paths import DATA_RAW, REPO_ROOT
from .sources import get_source

_LOGGER = get_logger("ingestion.reference_codes")

# CMS/X12 flat files use legacy single-byte encoding; latin-1 round-trips.
_ENCODING = "latin-1"

_DDL_FILE = REPO_ROOT / "sql" / "ddl" / "60_reference_codes.sql"
_SCHEMA = "rcm"

# Level II HCPCS: one letter + four digits. Excludes CPT Level I (numeric),
# 2-char modifiers, and the ADA-copyright D-series (CLAUDE.md §3.7).
_HCPCS_LEVEL2 = re.compile(r"^[A-CE-Z][0-9]{4}$")

# CARC code identifiers used by the simulation's denial taxonomy, paired with
# PROJECT-AUTHORED short labels (NOT copyrighted X12 description text — §3.7).
# Kept in sync with config/simulation.yaml denial_categories[*].carc_group.
_CARC_LABELS: dict[str, str] = {
    "16": "Missing or incomplete documentation",
    "18": "Duplicate claim or service",
    "22": "Coordination of benefits — other payer primary",
    "27": "Coverage terminated or not in effect",
    "29": "Timely filing limit exceeded",
    "50": "Not medically necessary",
    "96": "Non-covered service",
    "97": "Payment bundled into another service",
    "181": "Invalid or non-specific coding",
    "197": "Prior authorization absent or invalid",
}


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _download_one(manifest: Manifest, source: str, artifact_key: str, *, force: bool) -> None:
    """Download and checksum one reference zip, recording it in the manifest."""
    cfg = get_source(source)
    art = cfg["artifacts"][artifact_key]
    dest = REPO_ROOT / art["filename"]
    key = f"{source}:{artifact_key}"

    prior = manifest.get(key)
    digest = download(
        art["url"], dest, _LOGGER, expected_sha256=prior.sha256 if prior else None, force=force
    )
    manifest.put(
        ManifestEntry(
            key=key,
            source=source,
            role="reference",
            classification=cfg["classification"],
            url=art["url"],
            filename=art["filename"],
            vintage=art["vintage"],
            sha256=digest,
            size_bytes=dest.stat().st_size,
            row_count=art.get("row_count"),
            license_note=cfg["license_note"],
            notes=f"Code-set zip; parsed member: {art['member']}.",
        )
    )
    log_event(_LOGGER, "reference_codes.downloaded", key=key, sha256=digest)


def download_all(*, force: bool = False) -> Manifest:
    """Download every reference code-set zip (idempotent via manifest checksums)."""
    (DATA_RAW / "reference").mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load()
    _download_one(manifest, "icd10", "icd10cm_2023", force=force)
    _download_one(manifest, "icd10", "icd10pcs_2023", force=force)
    _download_one(manifest, "hcpcs", "hcpcs_2023", force=force)
    _download_one(manifest, "ms_drg", "msdrg_v40_table5", force=force)
    manifest.save()
    return manifest


# --------------------------------------------------------------------------- #
# Parse (from the on-disk zips, streaming the one member we need)
# --------------------------------------------------------------------------- #
def _read_member(source: str, artifact_key: str) -> list[str]:
    """Return the decoded lines of the configured member inside a source zip."""
    cfg = get_source(source)
    art = cfg["artifacts"][artifact_key]
    zip_path = REPO_ROOT / art["filename"]
    if not zip_path.exists():
        raise FileNotFoundError(f"{zip_path} missing — run `--download` first")
    with zipfile.ZipFile(zip_path) as zf:
        raw = zf.read(art["member"])
    return io.TextIOWrapper(io.BytesIO(raw), encoding=_ENCODING).read().splitlines()


def parse_icd10cm() -> pd.DataFrame:
    """Parse FY2023 ICD-10-CM: `<code><spaces><long description>`."""
    rows = []
    for line in _read_member("icd10", "icd10cm_2023"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0]:
            rows.append((parts[0].strip(), parts[1].strip()))
    return pd.DataFrame(rows, columns=["icd10cm_code", "long_desc"]).drop_duplicates("icd10cm_code")


def parse_icd10pcs() -> pd.DataFrame:
    """Parse FY2023 ICD-10-PCS: `<7-char code> <long description>`."""
    rows = []
    for line in _read_member("icd10", "icd10pcs_2023"):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0]:
            rows.append((parts[0].strip(), parts[1].strip()))
    return pd.DataFrame(rows, columns=["icd10pcs_code", "long_desc"]).drop_duplicates(
        "icd10pcs_code"
    )


def parse_hcpcs() -> pd.DataFrame:
    """Parse 2023 HCPCS Level II (fixed-width); keep Level II codes only (§3.7).

    A long description can span multiple contiguous records: record-identifier
    '3' (position 11) is the first line, '4' the continuation lines, each
    carrying 80 chars of long-description text at positions 12-91. We
    reconstruct the full description by concatenating those pieces in order.
    """
    acc: dict[str, dict] = {}
    order: list[str] = []
    for line in _read_member("hcpcs", "hcpcs_2023"):
        code = line[0:5].strip()
        if not _HCPCS_LEVEL2.match(code):
            continue  # skip CPT Level I, modifiers, D-series (§3.7)
        long_piece = line[11:91].rstrip()
        if code not in acc:
            acc[code] = {"long_parts": [], "short": line[91:119].strip()}
            order.append(code)
        if long_piece:
            acc[code]["long_parts"].append(long_piece)
    rows = []
    for code in order:
        entry = acc[code]
        long_desc = " ".join(entry["long_parts"]).strip()
        rows.append((code, long_desc or None, entry["short"] or None))
    return pd.DataFrame(rows, columns=["hcpcs_code", "long_desc", "short_desc"])


def parse_msdrg() -> pd.DataFrame:
    """Parse MS-DRG v40 (IPPS FY2023 Table 5, tab-delimited, quoted titles)."""
    import csv

    lines = _read_member("ms_drg", "msdrg_v40_table5")
    reader = csv.reader(lines, delimiter="\t")
    rows = []
    seen_header = False
    for rec in reader:
        if not rec:
            continue
        head = rec[0].strip()
        if head == "MS-DRG":
            seen_header = True
            continue
        if not seen_header or not head.isdigit():
            continue  # title lines, blanks, and any stray non-data rows
        drg_cd = head.zfill(3)  # match rcm.dim_drg.drg_cd (3-digit zero-padded)
        mdc = rec[3].strip() if len(rec) > 3 else None
        drg_type = rec[4].strip() if len(rec) > 4 else None
        title = rec[5].strip() if len(rec) > 5 else ""
        rows.append((drg_cd, title, mdc or None, drg_type or None))
    return pd.DataFrame(rows, columns=["drg_cd", "drg_title", "mdc", "drg_type"]).drop_duplicates(
        "drg_cd"
    )


def parse_carc() -> pd.DataFrame:
    """Build the §3.7-clean CARC label table (project-authored labels only)."""
    return pd.DataFrame(
        [(code, label) for code, label in _CARC_LABELS.items()],
        columns=["carc_code", "category_label"],
    )


# --------------------------------------------------------------------------- #
# Load (additive; never drops fact_* / sim_*)
# --------------------------------------------------------------------------- #
def _truncate_and_load(conn, table: str, df: pd.DataFrame) -> None:
    """Truncate one ref_* table and bulk-insert `df` (idempotent reload)."""
    from sqlalchemy import text

    conn.execute(text(f"truncate table {_SCHEMA}.{table}"))
    df.to_sql(
        table,
        conn,
        schema=_SCHEMA,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )
    log_event(_LOGGER, "reference_codes.loaded", table=table, rows=int(len(df)))


def load(engine) -> dict[str, int]:
    """Apply the additive DDL, (re)load ref_* tables, enrich dim_drg.drg_desc.

    Returns a dict of table -> row count plus dim_drg enrichment coverage.
    Idempotent: safe to re-run; touches no fact_* / sim_* table.
    """
    from sqlalchemy import text

    frames = {
        "ref_icd10cm": parse_icd10cm(),
        "ref_icd10pcs": parse_icd10pcs(),
        "ref_hcpcs": parse_hcpcs(),
        "ref_msdrg": parse_msdrg(),
        "ref_carc": parse_carc(),
    }
    report: dict[str, int] = {}
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL_FILE.read_text())  # create-if-not-exists (additive)
        log_event(_LOGGER, "reference_codes.ddl_applied", file=_DDL_FILE.name)
        for table, df in frames.items():
            _truncate_and_load(conn, table, df)
            report[table] = int(len(df))
        # Enrich dim_drg.drg_desc by value join (no drop/recreate of dim_drg).
        conn.execute(
            text(
                f"update {_SCHEMA}.dim_drg d "
                f"set drg_desc = r.drg_title, provenance = 'REFERENCE' "
                f"from {_SCHEMA}.ref_msdrg r "
                f"where d.drg_cd = r.drg_cd"
            )
        )
        enriched = conn.execute(
            text(f"select count(*) from {_SCHEMA}.dim_drg where drg_desc is not null")
        ).scalar()
        still_null = conn.execute(
            text(f"select count(*) from {_SCHEMA}.dim_drg where drg_desc is null and drg_key <> 0")
        ).scalar()
    report["dim_drg_enriched"] = int(enriched)
    report["dim_drg_unmatched"] = int(still_null)
    log_event(
        _LOGGER,
        "reference_codes.dim_drg_enriched",
        **{"enriched": int(enriched), "unmatched_non_unknown": int(still_null)},
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download + load REFERENCE code sets (FY2023).")
    parser.add_argument("--download", action="store_true", help="Fetch + checksum the zips.")
    parser.add_argument("--load", action="store_true", help="(Re)load into Postgres.")
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if checksum matches."
    )
    args = parser.parse_args(argv)

    do_download = args.download or not args.load
    do_load = args.load or not args.download

    if do_download:
        download_all(force=args.force)

    if do_load:
        from .load_postgres import database_url

        url = database_url()
        if url is None:
            log_event(_LOGGER, "reference_codes.no_db", hint="set POSTGRES_* in .env")
            return 2
        from sqlalchemy import create_engine

        engine = create_engine(url)
        report = load(engine)
        log_event(_LOGGER, "reference_codes.done", **report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
