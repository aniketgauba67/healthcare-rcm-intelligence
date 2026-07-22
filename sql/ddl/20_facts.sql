-- ============================================================================
-- 20_facts.sql — fact tables, foreign keys, checks, indexes
-- Sources:      validated Parquet: inpatient
-- Provenance:   SOURCE measures; length_of_stay_days is DERIVED.
-- Notes:        Facts resolve every FK to a real member or the Unknown member
--               (key 0) — never null. Idempotent: drop-and-recreate. PG 16.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- fact_inpatient_claim — grain: one row per inpatient claim (CLM_ID).
-- Claim-header measures (payment/charges are constant across a claim's lines).
-- ---------------------------------------------------------------------------
drop table if exists rcm.fact_inpatient_claim cascade;
create table rcm.fact_inpatient_claim (
    claim_sk               bigint primary key,
    clm_id                 text not null unique,          -- degenerate dimension
    bene_key               bigint not null references rcm.dim_beneficiary,
    provider_key           bigint not null references rcm.dim_provider,
    drg_key                bigint not null references rcm.dim_drg,
    discharge_status_key   bigint not null references rcm.dim_discharge_status,
    from_date_key          integer not null references rcm.dim_date,
    thru_date_key          integer not null references rcm.dim_date,
    admission_date_key     integer not null references rcm.dim_date,
    discharge_date_key     integer not null references rcm.dim_date,
    nch_clm_type_cd        text,
    admtg_dgns_cd          text,
    prncpal_dgns_cd        text,
    clm_utlztn_day_cnt     integer,
    length_of_stay_days    integer,                        -- DERIVED
    clm_pmt_amt            numeric(14, 2),
    clm_tot_chrg_amt       numeric(14, 2),
    nch_ip_ncvrd_chrg_amt  numeric(14, 2),
    nch_bene_ip_ddctbl_amt numeric(14, 2),
    constraint ck_fic_util_nonneg   check (clm_utlztn_day_cnt is null or clm_utlztn_day_cnt >= 0),
    constraint ck_fic_los_nonneg    check (length_of_stay_days is null or length_of_stay_days >= 0),
    constraint ck_fic_pmt_nonneg    check (clm_pmt_amt is null or clm_pmt_amt >= 0),
    constraint ck_fic_chrg_nonneg   check (clm_tot_chrg_amt is null or clm_tot_chrg_amt >= 0),
    constraint ck_fic_ncvrd_nonneg  check (nch_ip_ncvrd_chrg_amt is null or nch_ip_ncvrd_chrg_amt >= 0),
    constraint ck_fic_ddctbl_nonneg check (nch_bene_ip_ddctbl_amt is null or nch_bene_ip_ddctbl_amt >= 0),
    -- Service dates must be ordered (Unknown key 0 exempt). date_key is yyyymmdd.
    constraint ck_fic_date_order    check (from_date_key = 0 or thru_date_key = 0
                                           or from_date_key <= thru_date_key)
);
create index ix_fic_bene     on rcm.fact_inpatient_claim (bene_key);
create index ix_fic_provider on rcm.fact_inpatient_claim (provider_key);
create index ix_fic_drg      on rcm.fact_inpatient_claim (drg_key);
create index ix_fic_from_dt  on rcm.fact_inpatient_claim (from_date_key);

-- ---------------------------------------------------------------------------
-- fact_claim_revenue_line — grain: one row per claim revenue-center line
-- (CLM_ID + CLM_LINE_NUM). Line-level rev code / HCPCS.
-- ---------------------------------------------------------------------------
drop table if exists rcm.fact_claim_revenue_line cascade;
create table rcm.fact_claim_revenue_line (
    claim_line_sk bigint primary key,
    claim_sk      bigint not null references rcm.fact_inpatient_claim (claim_sk),
    clm_id        text not null,                           -- degenerate dimension
    clm_line_num  integer not null,
    rev_cntr      text,
    hcpcs_cd      text,
    constraint uq_fcrl_line unique (clm_id, clm_line_num)
);
create index ix_fcrl_claim on rcm.fact_claim_revenue_line (claim_sk);

-- ---------------------------------------------------------------------------
-- fact_claim_diagnosis — bridge, grain: one row per (claim, diagnosis slot).
-- Long form of ICD_DGNS_CD1..25 with present-on-admission switch. Only
-- non-empty diagnosis codes are materialized.
-- ---------------------------------------------------------------------------
drop table if exists rcm.fact_claim_diagnosis cascade;
create table rcm.fact_claim_diagnosis (
    claim_dgns_sk bigint primary key,
    claim_sk      bigint not null references rcm.fact_inpatient_claim (claim_sk),
    clm_id        text not null,                           -- degenerate dimension
    dgns_seq      smallint not null,                       -- 1..25
    icd_dgns_cd   text not null,
    poa_ind_sw    text,
    constraint uq_fcd_slot unique (clm_id, dgns_seq)
);
create index ix_fcd_claim on rcm.fact_claim_diagnosis (claim_sk);
create index ix_fcd_code  on rcm.fact_claim_diagnosis (icd_dgns_cd);
