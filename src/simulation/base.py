"""The claim base frame the simulation attaches to.

Reads the SOURCE star-schema frames and reduces them to the handful of columns
the generator conditions on. Nothing here is simulated — these are SOURCE and
DERIVED values, deliberately kept without a `sim_` prefix so it stays obvious
which side of the line each column is on.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.ingestion.star_transform import StarFrames, build_star


def _date_from_key(key: int) -> dt.date | None:
    """Invert star_transform's yyyymmdd date_key. 0 is the Unknown member."""
    if key is None or key == 0:
        return None
    key = int(key)
    return dt.date(key // 10000, (key // 100) % 100, key % 100)


def claim_base(frames: StarFrames | None = None) -> pd.DataFrame:
    """One row per claim, ordered by claim_sk, with the generator's inputs.

    Columns: claim_sk, clm_id, bene_key, provider_key, prvdr_num, drg_cd,
    billed_amount, service_from_date, anchor_date, length_of_stay_days,
    diagnosis_count.

    `anchor_date` is the date the whole simulated timeline hangs off: the
    discharge date where the source has one, else the claim thru date, else the
    from date. Claims with no usable date at all are dropped and reported by the
    caller rather than silently given a made-up anchor.
    """
    frames = frames or build_star()
    fic = frames.facts["fact_inpatient_claim"]
    fcd = frames.facts["fact_claim_diagnosis"]
    dim_drg = frames.dims["dim_drg"]
    dim_provider = frames.dims["dim_provider"]

    drg_by_key = dict(zip(dim_drg["drg_key"], dim_drg["drg_cd"]))
    prvdr_by_key = dict(zip(dim_provider["provider_key"], dim_provider["prvdr_num"]))
    dx_counts = fcd.groupby("claim_sk").size()

    base = pd.DataFrame(
        {
            "claim_sk": fic["claim_sk"].astype("int64"),
            "clm_id": fic["clm_id"].astype("string"),
            "bene_key": fic["bene_key"].astype("int64"),
            "provider_key": fic["provider_key"].astype("int64"),
            "prvdr_num": fic["provider_key"].map(prvdr_by_key).astype("string"),
            "drg_cd": fic["drg_key"].map(drg_by_key).astype("string"),
            "billed_amount": pd.to_numeric(fic["clm_tot_chrg_amt"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0.0)
            .round(2),
            "service_from_date": fic["from_date_key"].map(_date_from_key),
            "thru_date": fic["thru_date_key"].map(_date_from_key),
            "discharge_date": fic["discharge_date_key"].map(_date_from_key),
            "length_of_stay_days": pd.to_numeric(fic["length_of_stay_days"], errors="coerce"),
            "diagnosis_count": fic["claim_sk"].map(dx_counts).fillna(0).astype("int64"),
        }
    )

    anchor = base["discharge_date"]
    anchor = anchor.where(anchor.notna(), base["thru_date"])
    anchor = anchor.where(anchor.notna(), base["service_from_date"])
    base["anchor_date"] = anchor
    # Filing limits are measured from the date of service; fall back to the
    # anchor when the source has no from-date.
    base["service_from_date"] = base["service_from_date"].where(
        base["service_from_date"].notna(), base["anchor_date"]
    )
    base["length_of_stay_days"] = (
        base["length_of_stay_days"].fillna(1.0).clip(lower=0.0).astype("float64")
    )

    base = base[base["anchor_date"].notna()].copy()
    return base.sort_values("claim_sk").reset_index(drop=True)


def assign_service_line(drg_cd: pd.Series, service_lines) -> pd.Series:
    """Map MS-DRG codes to this simulation's coarse service-line buckets.

    The bucket boundaries are a design choice of the simulation layer, not an
    official CMS grouping, which is why the resulting column is classified
    SIMULATED (docs/assumptions.md §5).
    """
    numeric = pd.to_numeric(drg_cd, errors="coerce")
    unknown_id = next(s.id for s in service_lines if s.lo is None)
    out = pd.Series(unknown_id, index=drg_cd.index, dtype="object")
    for sl in service_lines:
        if sl.lo is None or sl.hi is None:
            continue
        hit = numeric.notna() & (numeric >= sl.lo) & (numeric <= sl.hi)
        out = out.mask(hit, sl.id)
    return out.astype("string")


def expit(x: np.ndarray) -> np.ndarray:
    """Overflow-safe logistic function."""
    out = np.empty_like(x, dtype="float64")
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    e = np.exp(x[~pos])
    out[~pos] = e / (1.0 + e)
    return out


def logit(p: float) -> float:
    return float(np.log(p / (1.0 - p)))


def solve_intercept(
    linear: np.ndarray, target_mean: float, lo: float = -25.0, hi: float = 25.0
) -> float:
    """Find the intercept c such that mean(expit(linear + c)) == target_mean.

    Bisection rather than a closed form because the mean of a logistic transform
    has none. Monotone in c, so 200 halvings is far past double precision.
    """
    if not 0.0 < target_mean < 1.0:
        raise ValueError(f"target_mean must be in (0,1), got {target_mean}")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if float(expit(linear + mid).mean()) < target_mean:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def zscore(values: np.ndarray) -> np.ndarray:
    sd = float(np.std(values))
    if sd == 0.0:
        return np.zeros_like(values, dtype="float64")
    return (values - float(np.mean(values))) / sd


def rounded_days(raw: np.ndarray, minimum: int = 0) -> np.ndarray:
    """Whole-day lags, floored at `minimum`, as int64."""
    return np.maximum(np.rint(raw), minimum).astype("int64")
