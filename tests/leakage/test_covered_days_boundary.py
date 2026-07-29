"""`clm_utlztn_day_cnt` is a benefit determination; `length_of_stay_days` is not.

The two columns look interchangeable — both count days of an inpatient stay, and
they differ on every one of the 20,867 claims, so neither is a copy of the other.
They fall on opposite sides of the pre-submission boundary anyway:

* `length_of_stay_days` is `(discharge - admission) + 1` on 100% of claims. It is
  calendar arithmetic over two dates a biller has in hand. PERMITTED.
* `clm_utlztn_day_cnt` is the COVERED-day count. It equals
  `(discharge - admission)` on 92.5% of claims and falls one day short of it on
  the other 7.53% (1,572 claims). That deficit cohort carries 7.2x the mean
  non-covered charge ($2,784 vs $385) and a higher rate of carrying any
  non-covered charge (47.2% vs 37.9%), so the missing day is the payer declining
  to count a day as covered — the same adjudication event that
  `nch_ip_ncvrd_chrg_amt` records in dollars, which is already forbidden.
  FORBIDDEN, under `forbidden_source_features`.

The distinction is not empirically load-bearing on this dataset (the label is
SIMULATED and was generated independently of the real CMS adjudication columns),
which is exactly why it needs a test: nothing about the metrics would move if it
were wrong, so nothing but this file would notice.

The integration test re-measures the two facts the classification rests on. If a
future warehouse load changes them, the call gets re-examined rather than
silently inherited.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text

from src.features.build import MODEL_A_FEATURES
from src.features.extract import CLAIM_QUERY
from src.features.leakage import LeakageError, assert_no_forbidden_columns, forbidden_columns

_COVERED_DAYS = "clm_utlztn_day_cnt"
_CALENDAR_DAYS = "length_of_stay_days"


def test_covered_day_count_is_forbidden_for_model_a() -> None:
    assert _COVERED_DAYS in forbidden_columns("A")
    with pytest.raises(LeakageError, match=_COVERED_DAYS):
        assert_no_forbidden_columns([_CALENDAR_DAYS, _COVERED_DAYS], model="A")


def test_calendar_length_of_stay_stays_permitted() -> None:
    """The sibling must not be swept up: it is knowable before submission."""
    assert _CALENDAR_DAYS not in forbidden_columns("A")
    assert_no_forbidden_columns([_CALENDAR_DAYS, "billed_charge_amt"], model="A")


def test_the_feature_store_neither_declares_nor_reads_the_covered_day_count() -> None:
    """Blocked at the query, not only at the guard — it is never read at all."""
    assert _COVERED_DAYS not in MODEL_A_FEATURES.names
    assert _COVERED_DAYS not in CLAIM_QUERY
    assert _CALENDAR_DAYS in CLAIM_QUERY


@pytest.mark.integration
def test_the_two_day_counts_still_behave_as_classified() -> None:
    """Re-measure the evidence behind the call, read-only, on the live warehouse."""
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            frame = pd.read_sql(
                text(
                    """
                    select f.length_of_stay_days,
                           f.clm_utlztn_day_cnt,
                           f.nch_ip_ncvrd_chrg_amt,
                           adm.full_date as admission_date,
                           dis.full_date as discharge_date
                    from rcm.fact_inpatient_claim f
                    left join rcm.dim_date adm on adm.date_key = f.admission_date_key
                    left join rcm.dim_date dis on dis.date_key = f.discharge_date_key
                    """
                ),
                conn,
            )
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")

    stay = (
        pd.to_datetime(frame["discharge_date"]) - pd.to_datetime(frame["admission_date"])
    ).dt.days

    # 1. The permitted column is pure calendar arithmetic.
    assert (frame[_CALENDAR_DAYS] == stay + 1).all(), (
        "length_of_stay_days is no longer (discharge - admission) + 1; it may have "
        "acquired an adjudication component and needs reclassifying"
    )

    # 2. The forbidden column is not a fixed offset from it — the gap moves, and
    #    it moves with the non-covered charge.
    deficit = frame[_CALENDAR_DAYS] - frame[_COVERED_DAYS] > 1
    share = float(deficit.mean())
    assert 0.01 < share < 0.25, f"covered-day deficit share moved to {share:.4f}; re-measure"
    assert (
        frame.loc[deficit, "nch_ip_ncvrd_chrg_amt"].mean()
        > 2 * frame.loc[~deficit, "nch_ip_ncvrd_chrg_amt"].mean()
    ), "the covered-day deficit no longer tracks non-covered charges; re-examine the call"
