-- ============================================================================
-- 00_schema.sql — warehouse schema for the Healthcare RCM Intelligence Platform
-- Grain:        n/a (namespace + conventions)
-- Sources:      CMS Synthetic Medicare RIF (validated Parquet layer)
-- Provenance:   SOURCE / DERIVED (see per-table comment blocks; simulated
--               tables are added separately by simulation-engineer as sim_*)
-- Notes:        Idempotent — safe to re-run. PostgreSQL 16.
-- ============================================================================

create schema if not exists rcm;

comment on schema rcm is
  'RCM warehouse: SOURCE/DERIVED star schema over CMS synthetic Medicare RIF. '
  'Synthetic provider/facility identifiers are NOT real NPIs/CCNs; real-entity '
  'linkage happens only via the SIMULATED crosswalk (sim_*), never here.';
