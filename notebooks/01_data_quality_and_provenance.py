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
# # 01 — Data Quality & Provenance Overview
#
# **Analytics-engineer EDA notebook, Phase 3.** Re-runnable top to bottom against
# the live warehouse. Establishes what the book of business is and how clean it is
# before any KPI or model interpretation.
#
# **HONESTY (CLAUDE.md §3).** The CMS synthetic Medicare claims are real synthetic
# *source* data (charges, DRGs, service dates, diagnoses). Everything about
# adjudication — denials, payments, appeals, workflow, costs, and the multi-payer
# dimension — is **SIMULATED** and does not describe any real payer. The payer
# dimension is 100 percent simulated (§3.5). Anything flagged here is a data-quality
# **review flag**, never fraud.
#
# Format: jupytext *percent* notebook. Run `python 01_...py`, or pair to `.ipynb`
# with `jupytext --to notebook 01_...py`.

# %%
import warnings

import pandas as pd

warnings.filterwarnings("ignore")
pd.set_option("display.width", 160, "display.max_columns", 60)

from analytics_common import SIMULATED_BANNER, get_engine, load_claims

print(SIMULATED_BANNER)
engine = get_engine()
claims = load_claims(engine)
print(f"\nvw_claim_enriched: {len(claims):,} rows (one per claim)")

# %% [markdown]
# ## The data-quality scorecard view
#
# `rcm.vw_data_quality_scorecard` runs 14 warehouse integrity/completeness checks,
# each self-describing (numerator / denominator / metric / pass). Critical checks
# gate the analysis; `info` rows (Unknown-member routing, missing REFERENCE
# descriptions) are expected-by-design, not failures.

# %%
dq = pd.read_sql("select * from rcm.vw_data_quality_scorecard order by severity, check_id", engine)
print(
    dq[
        ["check_id", "dimension", "subject_provenance", "metric_value", "severity", "pass_flag"]
    ].to_string(index=False)
)

crit = dq[dq.severity == "critical"]
print(
    f"\nINSIGHT 1: all {len(crit)} critical data-quality checks PASS "
    f"(pass={bool(crit.pass_flag.all())}). Adjudication is 1:1 with the claim fact "
    f"(coverage={float(dq.loc[dq.check_id == 'adjudication_coverage', 'metric_value'].iloc[0]):.3f}), "
    f"zero orphans, zero quarantined rows, all sim money invariants hold. "
    f"The warehouse is analysis-ready."
)

# %% [markdown]
# ## Provenance mix of the book
#
# Composition and the amounts that are SOURCE (real Medicare) vs SIMULATED.

# %%
n = len(claims)
denied = int(claims.sim_denial_flag.sum())
unpaid = int(claims.ar_open_flag.sum())
print(f"claims                : {n:,}")
print(f"denied (SIMULATED)    : {denied:,}  ({denied / n:.4f})")
print(f"open AR / unpaid (SIM): {unpaid:,}  ({unpaid / n:.4f})")
print(f"distinct synthetic prvdr_num : {claims.prvdr_num.nunique():,}")
print(
    f"distinct real CCN (display)  : {claims.facility_ccn.nunique():,}  "
    f"<- fewer than providers: the crosswalk multiplexes, so we NEVER group on CCN"
)
print(f"\nSOURCE billed charges total   : ${claims.billed_charge_amt.sum():,.0f}")
print(f"SIMULATED allowed total       : ${claims.sim_allowed_amount.sum():,.0f}")
print(f"SIMULATED paid total          : ${claims.sim_paid_amount.sum():,.0f}")

print(
    f"\nINSIGHT 2: the simulated denial rate is {denied / n:.4f} (target band 10-18 percent, "
    f"docs/assumptions.md). Note {unpaid:,} claims ({unpaid / n:.1%}) carry no simulated "
    f"payment — almost all are full denials — which drives the AR and survival views."
)

# %% [markdown]
# ## Source DRG concentration (a modeling caution)
#
# The synthetic source data is dominated by one DRG. This is a property of the CMS
# file, not the simulation, and it thins every DRG/service-line interaction.

# %%
drg = (
    claims.groupby("drg_cd")
    .size()
    .sort_values(ascending=False)
    .head(8)
    .rename("claims")
    .reset_index()
)
drg["share"] = (drg.claims / n).round(4)
print(drg.to_string(index=False))
top = drg.iloc[0]
print(
    f"\nINSIGHT 3: DRG {top.drg_cd} alone is {top.share:.1%} of claims (SOURCE skew). "
    f"Per-service-line and DRG-interaction signals are therefore thin — quantify "
    f"effects with volumes shown alongside rates, and do not over-read a single DRG."
)

# %%
print(
    "\nNotebook 01 complete — warehouse is clean and analysis-ready. "
    "Continue with 02 (denial root cause & authorization)."
)
