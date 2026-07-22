-- ============================================================================
-- 40_quarantine.sql — data-quality quarantine table
-- Grain:        one row per contract violation (a row that failed a data
--               contract in the validated layer)
-- Sources:      DERIVED from validated Parquet by src/validation/contracts.py
-- Provenance:   DERIVED (data-quality metadata; no SOURCE values copied beyond
--               the offending entity key)
-- Notes:        Failing rows are isolated here, never silently dropped nor let
--               through. Idempotent drop/recreate. PostgreSQL 16.
-- ============================================================================

drop table if exists rcm.dq_quarantine cascade;
create table rcm.dq_quarantine (
    quarantine_id bigint generated always as identity primary key,
    table_name    text not null,
    contract      text not null,   -- key_uniqueness | date_order | non_negative_money | ...
    entity_key    text,            -- identifying key of the offending row
    reason        text,
    loaded_at     timestamptz not null default now()
);
create index ix_dq_table    on rcm.dq_quarantine (table_name);
create index ix_dq_contract on rcm.dq_quarantine (contract);
comment on table rcm.dq_quarantine is
  'DERIVED data-quality quarantine: one row per validated-layer data-contract '
  'violation. Failing rows are isolated here, never silently dropped.';
