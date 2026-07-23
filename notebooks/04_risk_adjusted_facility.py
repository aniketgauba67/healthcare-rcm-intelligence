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
# # 04 — Risk-Adjusted Facility (Provider) Comparison
#
# Plan §7.3 risk-adjusted comparison. Raw denial rates confound case mix: a provider
# skewed toward high-denial payers or complex service lines will look "worse" for
# reasons outside its control. We fit a **case-mix expected-denial model** (payer +
# service line + pre-submission risk, NO provider term), then compare each provider's
# **observed vs expected** denials by **indirect standardization** (O/E ratio) with a
# **Poisson funnel** flag.
#
# **MANDATORY KEYING (tasks.md).** Every provider aggregate keys on the SYNTHETIC
# `prvdr_num`, never on `facility_ccn`/`facility_name` (the crosswalk multiplexes up
# to 8 synthetic hospitals onto one real CCN). Real facility names are display-only
# and are **deliberately withheld from the flagged-outlier tables** below: attaching
# a SIMULATED denial outlier to a real hospital's name would misrepresent it. The
# denials here are simulated (§3); an O/E outlier is a **review flag**, never fraud
# and never a statement about the named real facility.

# %%
import warnings

import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

warnings.filterwarnings("ignore")
pd.set_option("display.width", 150, "display.max_columns", 40)

from analytics_common import get_engine, load_claims

engine = get_engine()
df = load_claims(engine)

# %% [markdown]
# ## Case-mix expected-denial model (no provider term)

# %%
d = df.assign(
    denied=df.sim_denial_flag.astype(int),
    auth_missing=df.sim_auth_missing.astype(int),
    elig_failed=df.sim_eligibility_failed.astype(int),
    doc_incomplete=(~df.sim_documentation_complete).astype(int),
)
expected_model = smf.logit(
    "denied ~ auth_missing + elig_failed + doc_incomplete"
    " + C(sim_payer_id) + C(sim_service_line_id)",
    d,
).fit(disp=0)
d["expected_p"] = expected_model.predict(d)
print(f"case-mix model: pseudo-R2={expected_model.prsquared:.4f}, n={int(expected_model.nobs):,}")

# %% [markdown]
# ## Indirect standardization by synthetic provider (O/E)

# %%
g = (
    d.groupby("prvdr_num")
    .agg(claims=("denied", "size"), observed=("denied", "sum"), expected=("expected_p", "sum"))
    .reset_index()
)
g["OE_ratio"] = g.observed / g.expected
MIN_CLAIMS = 30  # do not rank thin denominators
big = g[g.claims >= MIN_CLAIMS].copy()
print(f"providers total={len(g):,}; with >= {MIN_CLAIMS} claims (rankable)={len(big):,}")
print("O/E distribution (rankable providers):")
print(big.OE_ratio.describe().round(3).to_string())


def poisson_two_sided(o, e):
    return stats.poisson.sf(o - 1, e) if o >= e else stats.poisson.cdf(o, e)


big["poisson_p"] = [poisson_two_sided(o, e) for o, e in zip(big.observed, big.expected)]
flagged = big[big.poisson_p < 0.05]
print(
    f"\nINSIGHT 12: after adjusting for payer and service-line case mix, "
    f"{len(flagged)} of {len(big)} adequately-sized synthetic providers fall outside a "
    f"95% Poisson funnel (O/E significantly != 1). Case-mix explains only part of the "
    f"variation — provider-level process differences remain in the SIMULATED denials. "
    f"These are review flags for process attention, NOT accusations."
)

# %% [markdown]
# ## Flagged outliers — keyed on SYNTHETIC provider id only
#
# Real facility names are intentionally omitted here (see the honesty note at top).

# %%
cols = ["prvdr_num", "claims", "observed", "expected", "OE_ratio", "poisson_p"]
print("HIGH O/E review flags (more simulated denials than case mix predicts):")
print(
    flagged.sort_values("OE_ratio", ascending=False).head(6)[cols].round(3).to_string(index=False)
)
print("\nLOW O/E (fewer simulated denials than predicted):")
print(flagged.sort_values("OE_ratio").head(6)[cols].round(3).to_string(index=False))
worst = flagged.sort_values("OE_ratio", ascending=False).iloc[0]
print(
    f"\nINSIGHT 13: the most extreme high-side review flag (synthetic provider "
    f"{worst.prvdr_num}) shows {worst.observed:.0f} simulated denials vs "
    f"{worst.expected:.1f} expected on case mix — an O/E of {worst.OE_ratio:.2f}. "
    f"Indirect standardization surfaces it; a raw denial-rate league table would have "
    f"confounded it with payer mix. Keyed on prvdr_num so no real hospital is implicated."
)

# %%
print("\nNotebook 04 complete. Continue with 05 (process mining).")
