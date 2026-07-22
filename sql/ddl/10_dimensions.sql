-- ============================================================================
-- 10_dimensions.sql — conformed dimensions + Unknown members
-- Grain:        one row per dimension member (surrogate-keyed)
-- Sources:      validated Parquet: beneficiary_2024, inpatient
-- Provenance:   SOURCE (values copied unmodified from CMS synthetic RIF)
-- Notes:        Every dimension reserves key 0 for the 'Unknown' member so
--               facts can resolve unmatched/null natural keys without nulls.
--               Idempotent: drop-and-recreate. PostgreSQL 16.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- dim_date — calendar dimension. date_key is yyyymmdd (so key comparisons are
-- valid date-order checks); key 0 is the Unknown/undated member.
-- ---------------------------------------------------------------------------
drop table if exists rcm.dim_date cascade;
create table rcm.dim_date (
    date_key     integer primary key,   -- yyyymmdd; 0 = Unknown
    full_date    date,
    year         smallint,
    quarter      smallint,
    month        smallint,
    month_name   text,
    day          smallint,
    day_of_week  smallint,               -- 0=Mon .. 6=Sun
    day_name     text,
    is_weekend   boolean
);
insert into rcm.dim_date (date_key, full_date, year, quarter, month, month_name,
                          day, day_of_week, day_name, is_weekend)
values (0, null, null, null, null, null, null, null, null, null);

-- ---------------------------------------------------------------------------
-- dim_beneficiary — synthetic Medicare beneficiary (enrollment). SOURCE.
-- ---------------------------------------------------------------------------
drop table if exists rcm.dim_beneficiary cascade;
create table rcm.dim_beneficiary (
    bene_key           bigint primary key,        -- 0 = Unknown
    bene_id            text not null unique,       -- synthetic natural key
    birth_date         date,
    death_date         date,
    sex_ident_cd       text,
    race_cd            text,
    rti_race_cd        text,
    state_code         text,
    county_cd          text,
    zip_cd             text,
    age_at_end_ref_yr  integer,
    enrollmt_ref_yr    integer,
    part_a_cvrg_mons   integer,
    part_b_cvrg_mons   integer,
    hmo_cvrg_mons      integer,
    ptd_cvrg_mons      integer,
    provenance         text not null default 'SOURCE'
);
insert into rcm.dim_beneficiary (bene_key, bene_id, provenance)
values (0, 'UNKNOWN', 'DERIVED');

-- ---------------------------------------------------------------------------
-- dim_provider — synthetic billing provider from claims. SOURCE.
-- is_synthetic_id documents that prvdr_num/org_npi_num are NOT real CCNs/NPIs.
-- ---------------------------------------------------------------------------
drop table if exists rcm.dim_provider cascade;
create table rcm.dim_provider (
    provider_key      bigint primary key,          -- 0 = Unknown
    prvdr_num         text not null unique,         -- synthetic CCN-shaped id
    org_npi_num       text,                         -- synthetic NPI (not real)
    provider_state_cd text,
    is_synthetic_id   boolean not null default true,
    provenance        text not null default 'SOURCE'
);
insert into rcm.dim_provider (provider_key, prvdr_num, is_synthetic_id, provenance)
values (0, 'UNKNOWN', true, 'DERIVED');

-- ---------------------------------------------------------------------------
-- dim_drg — MS-DRG code. SOURCE (code only). drg_desc stays null until the
-- MS-DRG REFERENCE file is loaded (that update carries provenance REFERENCE).
-- ---------------------------------------------------------------------------
drop table if exists rcm.dim_drg cascade;
create table rcm.dim_drg (
    drg_key    bigint primary key,                 -- 0 = Unknown (null/blank DRG)
    drg_cd     text not null unique,
    drg_desc   text,
    provenance text not null default 'SOURCE'
);
insert into rcm.dim_drg (drg_key, drg_cd, provenance)
values (0, 'UNKNOWN', 'DERIVED');

-- ---------------------------------------------------------------------------
-- dim_discharge_status — patient discharge status code. SOURCE.
-- ---------------------------------------------------------------------------
drop table if exists rcm.dim_discharge_status cascade;
create table rcm.dim_discharge_status (
    discharge_status_key bigint primary key,        -- 0 = Unknown
    discharge_status_cd  text not null unique,
    provenance           text not null default 'SOURCE'
);
insert into rcm.dim_discharge_status (discharge_status_key, discharge_status_cd, provenance)
values (0, 'UNKNOWN', 'DERIVED');
