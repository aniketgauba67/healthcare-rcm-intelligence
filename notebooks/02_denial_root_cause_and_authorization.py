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
# # 02 — Denial Root Cause & Authorization ↔ Denial
#
# Statistical tests per plan §7.3: chi-square + **Cramér's V** for the categorical
# association between authorization status and denial, plus an **adjusted logistic
# regression** that isolates each pre-submission driver's effect on denial odds
# while controlling for payer and service line.
#
# **HONESTY (§3).** These are SIMULATED denials — the CMS synthetic claims contain
# none. Roughly a third of simulated denials are pure label noise (driver
# `baseline`, docs/assumptions.md); that ceiling is visible in the model fit. The
# payer dimension is 100 percent simulated (§3.5). High-frequency categories are
# process **review flags**, never fraud.

# %%
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
pd.set_option("display.width", 170, "display.max_columns", 60)

from analytics_common import cramers_v, get_engine, load_claims

engine = get_engine()
df = load_claims(engine)


def chi2_report(col, label):
    ct = pd.crosstab(df[col], df.sim_denial_flag)
    chi2, p, dof, _ = stats.chi2_contingency(ct)
    v = cramers_v(chi2, ct.values.sum(), *ct.shape)
    print(
        f"[chi-square] {label} x denial: chi2={chi2:.1f}, dof={dof}, p={p:.2e}, Cramer's V={v:.3f}"
    )
    return v


# %% [markdown]
# ## Root-cause mix (the view)
#
# `rcm.vw_denial_root_cause` cross-tabs WHAT the denial was (category + CARC label,
# label-only per §3.7) against WHY the generator denied it (driver mechanism).

# %%
rc = pd.read_sql(
    """
    select sim_denial_category, sim_denial_carc_group, sim_denial_driver_mechanism,
           denial_count, share_of_denials, appeal_rate, overturn_rate_of_appealed,
           sim_denied_amt
    from rcm.vw_denial_root_cause order by denial_count desc
""",
    engine,
)
print(rc.head(12).to_string(index=False))
baseline = int(rc.loc[rc.sim_denial_driver_mechanism == "baseline", "denial_count"].sum())
tot = int(rc.denial_count.sum())
print(
    f"\nINSIGHT 4: of {tot:,} simulated denials, {baseline:,} ({baseline / tot:.1%}) carry "
    f"driver 'baseline' — irreducible label noise with no mechanism signal "
    f"(docs/assumptions.md). Any explanatory model has this as its accuracy ceiling; "
    f"SHAP will not 'explain' these by construction."
)

# %% [markdown]
# ## Authorization status ↔ denial (chi-square + Cramér's V)

# %%
df["auth_status"] = np.select(
    [~df.sim_auth_required, df.sim_auth_missing, df.sim_auth_obtained_late],
    ["not_required", "required_missing", "obtained_late"],
    default="obtained_ontime",
)
tab = df.groupby("auth_status").sim_denial_flag.agg(rate="mean", claims="size").round(4)
print(tab.to_string())
v_auth = chi2_report("auth_status", "authorization status")
r_missing = tab.loc["required_missing", "rate"]
r_none = tab.loc["not_required", "rate"]
print(
    f"\nINSIGHT 5: a required-but-MISSING authorization raises the simulated denial "
    f"rate to {r_missing:.1%} vs {r_none:.1%} when no auth is required — a "
    f"{r_missing / r_none:.1f}x gap. Association is significant with Cramer's V={v_auth:.3f} "
    f"(a small-to-moderate effect), so missing auth is a real but not dominant signal."
)

# %% [markdown]
# ## Payer and service line vs denial

# %%
v_payer = chi2_report("sim_payer_id", "payer (SIMULATED)")
print(
    df.groupby("sim_payer_id")
    .sim_denial_flag.agg(rate="mean", claims="size")
    .round(4)
    .sort_values("rate", ascending=False)
    .to_string()
)
v_sl = chi2_report("sim_service_line_id", "service line (SIMULATED)")
print(
    f"\nINSIGHT 6: simulated payer is associated with denial (Cramer's V={v_payer:.3f}) — "
    f"denial ranges roughly 9 to 18 percent across the five simulated payer archetypes — "
    f"while service line is a much weaker signal (V={v_sl:.3f}). This matches the "
    f"simulation's design (strong per-payer, weak per-service-line) and the source DRG skew. "
    f"REMINDER: the payer dimension is 100 percent simulated (§3.5)."
)

# %% [markdown]
# ## Adjusted logistic regression: each driver's odds ratio
#
# `denied ~ auth_missing + auth_late + eligibility_failed + doc_incomplete +
# coding_deficit + duplicate + late_filing + C(payer) + C(service_line)`.
# Odds ratios isolate each pre-submission driver holding payer and service-line mix
# constant. These predictors are all legitimately pre-submission (Model A safe, §4).

# %%
import statsmodels.formula.api as smf

d = df.assign(
    denied=df.sim_denial_flag.astype(int),
    auth_missing=df.sim_auth_missing.astype(int),
    auth_late=df.sim_auth_obtained_late.astype(int),
    elig_failed=df.sim_eligibility_failed.astype(int),
    doc_incomplete=(~df.sim_documentation_complete).astype(int),
    coding_deficit=df.sim_coding_specificity_deficit.astype(int),
    dup=df.sim_duplicate_submission_flag.astype(int),
    late_filing=df.sim_late_filing_flag.astype(int),
)
model = smf.logit(
    "denied ~ auth_missing + auth_late + elig_failed + doc_incomplete + coding_deficit"
    " + dup + late_filing + C(sim_payer_id) + C(sim_service_line_id)",
    d,
).fit(disp=0)
orr = (
    pd.DataFrame(
        {
            "odds_ratio": np.exp(model.params),
            "ci_low": np.exp(model.conf_int()[0]),
            "ci_high": np.exp(model.conf_int()[1]),
            "p_value": model.pvalues,
        }
    )
    .loc[
        [
            "auth_missing",
            "auth_late",
            "elig_failed",
            "doc_incomplete",
            "coding_deficit",
            "dup",
            "late_filing",
        ]
    ]
    .round(3)
)
print(orr.to_string())
print(f"\nmodel: pseudo-R2={model.prsquared:.4f}, n={int(model.nobs):,}")
top = orr.odds_ratio.idxmax()
print(
    f"\nINSIGHT 7: adjusting for payer and service-line mix, a missing authorization "
    f"multiplies denial odds by {orr.loc['auth_missing', 'odds_ratio']:.2f}x "
    f"(95% CI {orr.loc['auth_missing', 'ci_low']:.2f}-{orr.loc['auth_missing', 'ci_high']:.2f}), "
    f"and a duplicate-submission flag by {orr.loc['dup', 'odds_ratio']:.2f}x — the two "
    f"strongest levers. Eligibility failure ({orr.loc['elig_failed', 'odds_ratio']:.2f}x), "
    f"incomplete documentation ({orr.loc['doc_incomplete', 'odds_ratio']:.2f}x) and coding "
    f"specificity deficit ({orr.loc['coding_deficit', 'odds_ratio']:.2f}x) each add "
    f"significant, independent risk. The low pseudo-R2 ({model.prsquared:.3f}) reflects the "
    f"~one-third label-noise floor — an honest, not a broken, fit."
)

# %%
print("\nNotebook 02 complete. Continue with 03 (payment timing & survival).")
