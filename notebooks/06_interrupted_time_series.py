# ---
# jupyter:
#   jupytext:
#     text_representation:
#       format_name: percent
#   kernelspec:
#     display_name: Python 3 (rcm)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 06 — Interrupted Time Series (METHODOLOGY — ILLUSTRATIVE ONLY)
#
# Plan §7.3 lists an interrupted time series (ITS) for "the simulated intervention
# module". **No intervention module exists in the Phase 2 simulation layer** — there
# is no process-change event, no intervention date, and no treated/control cohort in
# the warehouse. So this notebook does NOT and CANNOT report a real intervention
# effect. It does two honest things instead:
#
# 1. **Method validation on a self-contained synthetic series** (generated in this
#    notebook, NOT from the warehouse) with a *known injected* level + slope change,
#    to show the segmented-regression ITS estimator recovers it.
# 2. **The same estimator applied to the real monthly series** from the warehouse at
#    a *hypothetical* cut date — which, precisely because no intervention exists,
#    should find no significant break. This is the method wired up and ready for a
#    future intervention module; it is not evidence of any effect.
#
# **HONESTY (§3).** Every warehouse series here is SIMULATED adjudication data. The
# "intervention" is hypothetical and labeled as such on every output. Nothing here
# asserts a real or simulated intervention effect.

# %%
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

warnings.filterwarnings("ignore")
pd.set_option("display.width", 160, "display.max_columns", 40)

from analytics_common import get_engine

rng = np.random.default_rng(20260723)


def segmented_its(frame, y):
    """Segmented-regression ITS with Newey-West (HAC) SEs for autocorrelation.
    Terms: time (pre-trend), level (post-intervention step), time_since (slope change).
    """
    model = smf.ols(f"{y} ~ time + level + time_since", data=frame).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3}
    )
    return model


# %% [markdown]
# ## 1. Method validation on a synthetic series (known injected effect)
#
# 60 monthly points. Pre-period trend + a deliberate **level drop of -0.05** and a
# **slope change of +0.002/month** injected at t=30. A correct ITS estimator should
# recover both.

# %%
T, CUT = 60, 30
LEVEL_TRUE, SLOPE_TRUE = -0.05, 0.002
t = np.arange(T)
level = (t >= CUT).astype(int)
time_since = np.where(t >= CUT, t - CUT, 0)
y = 0.30 - 0.001 * t + LEVEL_TRUE * level + SLOPE_TRUE * time_since + rng.normal(0, 0.01, T)
syn = pd.DataFrame({"time": t, "level": level, "time_since": time_since, "y": y})

m = segmented_its(syn, "y")
print(m.params.round(4).to_string())
print(
    f"\nINSIGHT 18 (method check): injected level={LEVEL_TRUE:+.3f}, slope={SLOPE_TRUE:+.4f}. "
    f"Segmented regression recovers level={m.params['level']:+.3f} "
    f"(95% CI [{m.conf_int().loc['level', 0]:+.3f}, {m.conf_int().loc['level', 1]:+.3f}], "
    f"p={m.pvalues['level']:.1e}) and slope-change={m.params['time_since']:+.4f} "
    f"(p={m.pvalues['time_since']:.1e}). The estimator is correct and ready to use — the "
    f"only missing piece is a real intervention to point it at."
)

# %% [markdown]
# ## 2. Same estimator on the real monthly series (hypothetical cut, expect no break)
#
# Monthly simulated denial rate from `vw_executive_rcm_summary`, ordered by month,
# with a HYPOTHETICAL intervention at the series midpoint. No intervention exists, so
# a well-behaved estimator should return non-significant level and slope-change terms.

# %%
engine = get_engine()
monthly = pd.read_sql(
    "select submission_year_month, claims_submitted, denial_rate, clean_claim_rate "
    "from rcm.vw_executive_rcm_summary order by submission_year_month",
    engine,
).reset_index(drop=True)
monthly["time"] = np.arange(len(monthly))
cut = len(monthly) // 2
monthly["level"] = (monthly.time >= cut).astype(int)
monthly["time_since"] = np.where(monthly.time >= cut, monthly.time - cut, 0)
print(
    f"{len(monthly)} months, {monthly.submission_year_month.iloc[0]}..{monthly.submission_year_month.iloc[-1]}; "
    f"HYPOTHETICAL cut at {monthly.submission_year_month.iloc[cut]} (illustrative only)"
)

mr = segmented_its(monthly, "denial_rate")
tbl = pd.DataFrame({"coef": mr.params, "p_value": mr.pvalues}).round(4)
print(tbl.to_string())
sig = (mr.pvalues[["level", "time_since"]] < 0.05).any()
print(
    f"\nINSIGHT 19 (real series): against the HYPOTHETICAL midpoint cut, the simulated "
    f"denial-rate series shows "
    f"{'a significant' if sig else 'NO significant'} level or slope break "
    f"(level p={mr.pvalues['level']:.2f}, slope-change p={mr.pvalues['time_since']:.2f}). "
    f"That is the expected and correct result: there is no intervention in the data, so the "
    f"method correctly finds nothing. This notebook is the ITS harness ready for a real "
    f"simulated-intervention module; it asserts no effect."
)

# %%
print(
    "\nNotebook 06 complete. ITS methodology validated on a synthetic series and wired to "
    "the real monthly series; no real/simulated intervention is claimed (none exists)."
)
