-- ============================================================================
-- vw_ar_aging.sql — accounts-receivable aging of open (unpaid) claims
--
-- Grain:        one row per AR aging bucket (0-30, 31-60, 61-90, 91-120, 120+
--               days outstanding). "Open" = no simulated payment posted
--               (sim_payment_date is null). Days outstanding is measured from
--               the simulated submission date to a SNAPSHOT date defined as the
--               latest activity date observed anywhere in the sim adjudication
--               timeline (so the newest claims age from "today" in the data).
--
-- Sources:      rcm.vw_claim_enriched (ar_open_flag, ar_balance_amt).
-- Provenance:   SIMULATED timeline + money. Columns:
--                 aging_bucket, bucket_sort            = DERIVED
--                 open_claims, denied_open, nondenied_open = DERIVED from SIMULATED
--                 sim_ar_balance_amt, sim_billed_at_risk  = SIMULATED / SOURCE billed
--                 avg_days_outstanding                 = DERIVED from SIMULATED dates
--
-- HONESTY:      This ages SIMULATED unpaid claims; the aging economics are not
--               real. Open AR here is almost entirely FULL denials (1,906 of
--               1,911) that were allowed a positive amount but paid $0, so
--               sim_ar_balance_amt (= allowed - paid) is the unpaid expected
--               reimbursement sitting in AR. source_billed_at_risk_amt is shown
--               alongside it (billed >= allowed) so both lenses are visible.
--               EXPECTED SHAPE: in this simulated book every unpaid claim is a
--               never-paid full denial, and denied claims stop in 2023, so
--               relative to the 2024-07 snapshot ALL open AR falls in the 120+
--               bucket (min ~481 days). The 0-30..91-120 buckets legitimately
--               show zero. That is a property of the simulation, not a bug — the
--               five buckets are always emitted (via a spine) so the empties are
--               visible rather than hidden.
--
-- Control query (must reconcile):
--   select sum(open_claims) from rcm.vw_ar_aging;      -- = 1911 (all unpaid claims)
--   equals: select count(*) from rcm.vw_claim_enriched where ar_open_flag;
--   select count(*) from rcm.vw_ar_aging;              -- = 5 (spine: all buckets)
-- ============================================================================

create or replace view rcm.vw_ar_aging as
with snapshot as (
    -- latest activity anywhere in the simulated timeline = the AR "as of" date
    select greatest(
             max(sim_adjudication_date),
             max(sim_payment_date),
             max(sim_denial_review_date)
           ) as as_of_date
    from rcm.sim_claim_adjudication
),
open_claims as (
    select
        e.claim_sk,
        e.sim_denial_flag,
        e.ar_balance_amt,
        e.billed_charge_amt,
        (s.as_of_date - e.sim_submission_date) as days_outstanding
    from rcm.vw_claim_enriched e
    cross join snapshot s
    where e.ar_open_flag
),
bucketed as (
    select *,
        case
            when days_outstanding <= 30  then '0-30'
            when days_outstanding <= 60  then '31-60'
            when days_outstanding <= 90  then '61-90'
            when days_outstanding <= 120 then '91-120'
            else '120+'
        end as aging_bucket,
        case
            when days_outstanding <= 30  then 1
            when days_outstanding <= 60  then 2
            when days_outstanding <= 90  then 3
            when days_outstanding <= 120 then 4
            else 5
        end as bucket_sort
    from open_claims
),
spine(aging_bucket, bucket_sort) as (
    values ('0-30', 1), ('31-60', 2), ('61-90', 3), ('91-120', 4), ('120+', 5)
)
select
    spine.aging_bucket,
    spine.bucket_sort,
    count(b.claim_sk)                                       as open_claims,
    count(b.claim_sk) filter (where b.sim_denial_flag)      as denied_open_claims,
    count(b.claim_sk) filter (where not b.sim_denial_flag)  as nondenied_open_claims,
    round(coalesce(sum(b.ar_balance_amt), 0), 2)            as sim_ar_balance_amt,
    round(coalesce(sum(b.billed_charge_amt), 0), 2)         as source_billed_at_risk_amt,
    round(avg(b.days_outstanding), 1)                       as avg_days_outstanding,
    min(b.days_outstanding)                                 as min_days_outstanding,
    max(b.days_outstanding)                                 as max_days_outstanding
from spine
left join bucketed b on b.bucket_sort = spine.bucket_sort
group by spine.aging_bucket, spine.bucket_sort;

comment on view rcm.vw_ar_aging is
  'SIMULATED AR aging of unpaid claims by days outstanding to a snapshot date. '
  'Timeline and money are simulated (CLAUDE.md §3). Most open AR is fully-denied '
  'claims (open but ~$0 balance); count and balance are shown separately.';
