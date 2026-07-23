-- ============================================================================
-- 60_reference_codes.sql — official code-set REFERENCE dimensions
-- Grain:        one row per code (ICD-10-CM dx, ICD-10-PCS proc, HCPCS Level II,
--               MS-DRG, CARC group)
-- Sources:      Public CMS/X12 code files matching the 2023-04 claims vintage
--               (FY2023 ICD-10-CM/PCS, 2023 HCPCS Level II, MS-DRG v40 / IPPS
--               FY2023 Final Rule Table 5). CARC = X12 code identifiers only.
-- Provenance:   REFERENCE (unmodified public code/description text), EXCEPT
--               ref_carc.category_label which is a project-authored taxonomy
--               label (DERIVED) — no X12 copyrighted CARC description text is
--               reproduced (CLAUDE.md §3.7).
-- Notes:        ADDITIVE and idempotent. Uses `create table if not exists` and
--               NEVER drops/cascades, so applying this file does NOT touch
--               fact_* or sim_* tables (their FKs / surrogate keys stay valid).
--               Row data is (re)loaded by src/ingestion/reference_codes.py via
--               truncate+insert; dim_drg.drg_desc is enriched by the same loader.
--               PostgreSQL 16. §2 vintage rule: FY2023 codes, never ICD-9.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- ref_icd10cm — ICD-10-CM diagnosis code long descriptions (FY2023). REFERENCE.
-- Code is the dotless tabular form (e.g. A000), matching the CMS codes file.
-- ---------------------------------------------------------------------------
create table if not exists rcm.ref_icd10cm (
    icd10cm_code text primary key,
    long_desc    text not null,
    code_system  text not null default 'ICD-10-CM',
    vintage      text not null default 'FY2023',
    provenance   text not null default 'REFERENCE'
);
comment on table rcm.ref_icd10cm is
  'REFERENCE: FY2023 ICD-10-CM diagnosis code long descriptions (CMS public '
  'code file, unmodified). Dotless tabular code form. Never mix ICD-9 (CLAUDE.md §2).';

-- ---------------------------------------------------------------------------
-- ref_icd10pcs — ICD-10-PCS procedure code long descriptions (FY2023). REFERENCE.
-- ---------------------------------------------------------------------------
create table if not exists rcm.ref_icd10pcs (
    icd10pcs_code text primary key,
    long_desc     text not null,
    code_system   text not null default 'ICD-10-PCS',
    vintage       text not null default 'FY2023',
    provenance    text not null default 'REFERENCE'
);
comment on table rcm.ref_icd10pcs is
  'REFERENCE: FY2023 ICD-10-PCS procedure code long descriptions (CMS public '
  'code file, unmodified). 7-character codes.';

-- ---------------------------------------------------------------------------
-- ref_hcpcs — HCPCS Level II code descriptions (2023). REFERENCE.
-- Level II ONLY (alphanumeric letter+4 digits). CPT Level I (5-digit numeric,
-- AMA-licensed) and 2-character modifiers are excluded at load (CLAUDE.md §3.7).
-- ---------------------------------------------------------------------------
create table if not exists rcm.ref_hcpcs (
    hcpcs_code  text primary key,
    long_desc   text,
    short_desc  text,
    code_system text not null default 'HCPCS-II',
    vintage     text not null default '2023',
    provenance  text not null default 'REFERENCE'
);
comment on table rcm.ref_hcpcs is
  'REFERENCE: 2023 HCPCS Level II public descriptions (CMS Jan-2023 Alpha-Numeric '
  'file). CPT Level I descriptions are AMA-licensed and are NOT stored (CLAUDE.md §3.7).';

-- ---------------------------------------------------------------------------
-- ref_msdrg — MS-DRG v40 (FY2023) titles + MDC/type. REFERENCE.
-- Source: IPPS FY2023 Final Rule Table 5. drg_cd is 3-digit zero-padded to
-- match rcm.dim_drg.drg_cd. Populates dim_drg.drg_desc via the loader.
-- ---------------------------------------------------------------------------
create table if not exists rcm.ref_msdrg (
    drg_cd      text primary key,       -- 3-digit zero-padded, matches dim_drg
    drg_title   text not null,
    mdc         text,                   -- Major Diagnostic Category (e.g. PRE, 05)
    drg_type    text,                   -- SURG | MED | ** (ungroupable)
    code_system text not null default 'MS-DRG-v40',
    vintage     text not null default 'FY2023',
    provenance  text not null default 'REFERENCE'
);
comment on table rcm.ref_msdrg is
  'REFERENCE: MS-DRG Version 40 (FY2023) titles, MDC and type from the CMS IPPS '
  'FY2023 Final Rule Table 5 (unmodified). Enriches dim_drg.drg_desc.';

-- ---------------------------------------------------------------------------
-- ref_carc — Claim Adjustment Reason Code taxonomy LABELS. §3.7-clean.
-- The CARC code identifiers are public X12 facts; category_label is a
-- PROJECT-AUTHORED short label (NOT the copyrighted X12 description text).
-- No X12 CARC description text is reproduced anywhere in this repo.
-- ---------------------------------------------------------------------------
create table if not exists rcm.ref_carc (
    carc_code      text primary key,    -- X12 CARC identifier as a LABEL (§3.7)
    category_label text not null,       -- project-authored taxonomy label (DERIVED)
    code_system    text not null default 'X12-CARC',
    provenance     text not null default 'REFERENCE'  -- code=REFERENCE; label=DERIVED (see comment)
);
comment on table rcm.ref_carc is
  'CARC code identifiers used as denial-category LABELS only (CLAUDE.md §3.7). '
  'category_label is project-authored (DERIVED); no copyrighted X12 CARC '
  'description text is reproduced. Join target for sim_denial_carc_group.';
