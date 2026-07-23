"""Shared helpers for the Phase 3 EDA notebooks (analytics-engineer, sql/views + notebooks).

Every notebook imports these loaders so the connection logic and the SIMULATED
labeling live in one place. All frames are pulled from the live warehouse
(rcm.vw_claim_enriched and the sim_ tables) via the read-only connection helper
from the ingestion package (import only; no writes).

HONESTY (CLAUDE.md §3): the adjudication/denial/payment/appeal/workflow columns
are SIMULATED and do NOT describe real payer behaviour. The payer dimension is
100 percent simulated (§3.5). Anything a notebook flags is a "review flag",
never fraud.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# notebooks/ is one level under the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SIMULATED_BANNER = (
    "SIMULATED DATA — the adjudication, denial, payment, appeal and workflow layer "
    "is generated (CLAUDE.md §3). The multi-payer dimension is 100 percent simulated "
    "(§3.5). Findings are review signals, never fraud, and do not describe any real payer."
)


def get_engine():
    """SQLAlchemy engine for the live warehouse (read-only use in notebooks)."""
    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        raise RuntimeError(
            "No database_url() — set POSTGRES_* / DATABASE_URL in .env and start "
            "the docker Postgres (`docker compose up`)."
        )
    return create_engine(url)


def load_claims(engine=None) -> pd.DataFrame:
    """One row per claim from rcm.vw_claim_enriched (the analytics base view)."""
    engine = engine or get_engine()
    return pd.read_sql("select * from rcm.vw_claim_enriched", engine)


def load_workflow_events(engine=None) -> pd.DataFrame:
    """Full simulated workflow event log, ordered within claim by sequence."""
    engine = engine or get_engine()
    return pd.read_sql(
        """
        select claim_sk, clm_id, sim_event_seq, sim_event_type, sim_activity,
               sim_event_date, sim_event_ts, sim_actor_role, sim_appeal_level,
               sim_touch_minutes
        from rcm.sim_workflow_events
        order by claim_sk, sim_event_seq
        """,
        engine,
    )


def load_appeals(engine=None) -> pd.DataFrame:
    engine = engine or get_engine()
    return pd.read_sql("select * from rcm.sim_appeals", engine)


def cramers_v(chi2: float, n: int, r: int, k: int) -> float:
    """Bias-corrected Cramér's V (Bergsma 2013) for a chi-square on an r x k table."""
    import numpy as np

    phi2 = chi2 / n
    phi2corr = max(0.0, phi2 - (k - 1) * (r - 1) / (n - 1))
    rcorr = r - ((r - 1) ** 2) / (n - 1)
    kcorr = k - ((k - 1) ** 2) / (n - 1)
    denom = min(kcorr - 1, rcorr - 1)
    if denom <= 0:
        return float("nan")
    return float(np.sqrt(phi2corr / denom))
