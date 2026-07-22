"""Fixtures for the simulation tests.

The generator is exercised against a SYNTHETIC claim base frame rather than the
real validated Parquet, because `data/validated/` is gitignored and absent in
CI. The frame below has the same columns and dtypes `src.simulation.base.claim_base`
produces, so the tests cover the generator rather than the file layout.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.simulation.config import load_config


def make_base(n: int = 2_000, seed: int = 7) -> pd.DataFrame:
    """A claim base frame shaped exactly like `claim_base()` output."""
    rng = np.random.default_rng(seed)
    start = dt.date(2019, 1, 1)
    anchor = [start + dt.timedelta(days=int(d)) for d in rng.integers(0, 900, size=n)]
    los = rng.integers(1, 25, size=n)
    # A DRG mix that spans every configured bucket, plus a null slice so the
    # UNKNOWN service line is exercised too.
    drg = rng.choice(
        ["005", "030", "200", "400", "500", "700", "800", "900", "951", "965", None],
        size=n,
        p=[0.05, 0.08, 0.15, 0.05, 0.12, 0.05, 0.08, 0.05, 0.22, 0.05, 0.10],
    )
    return pd.DataFrame(
        {
            "claim_sk": np.arange(1, n + 1, dtype="int64"),
            "clm_id": pd.array([f"CLM{i:09d}" for i in range(1, n + 1)], dtype="string"),
            "bene_key": rng.integers(1, n // 3 + 2, size=n).astype("int64"),
            "provider_key": rng.integers(1, 60, size=n).astype("int64"),
            "prvdr_num": pd.array(
                [f"P{v:05d}" for v in rng.integers(1, 60, size=n)], dtype="string"
            ),
            "drg_cd": pd.array(drg, dtype="string"),
            "billed_amount": np.round(rng.lognormal(9.5, 0.8, size=n), 2),
            "service_from_date": anchor,
            "thru_date": anchor,
            "discharge_date": anchor,
            "length_of_stay_days": los.astype("float64"),
            "diagnosis_count": rng.integers(1, 22, size=n).astype("int64"),
            "anchor_date": anchor,
        }
    )


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def base():
    return make_base()


@pytest.fixture(scope="session")
def result(cfg, base):
    from src.simulation.generator import generate

    return generate(cfg, base)
