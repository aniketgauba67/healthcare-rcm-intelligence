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
# # 05 — Process Mining on the Workflow Event Log
#
# Plan deliverable: dominant paths (variants), rework loops, bottlenecks, and
# automation candidates from `rcm.sim_workflow_events` (one row per event, sequenced
# within a claim).
#
# **HONESTY (§3).** The entire workflow event log is SIMULATED — the CMS synthetic
# claims contain no workflow events. Touch-minutes, actor roles and activity labels
# are generated (docs/assumptions.md). A "bottleneck" or "automation candidate" here
# is a property of the simulation, offered as methodology, not a real operational finding.

# %%
import warnings

import pandas as pd

warnings.filterwarnings("ignore")
pd.set_option("display.width", 180, "display.max_columns", 40, "display.max_colwidth", 90)

from analytics_common import get_engine, load_workflow_events

engine = get_engine()
ev = load_workflow_events(engine)
ev["sim_event_ts"] = pd.to_datetime(ev["sim_event_ts"])
n_claims = ev.claim_sk.nunique()
print(f"events={len(ev):,}  claims={n_claims:,}  event types={ev.sim_event_type.nunique()}")

# %% [markdown]
# ## Dominant paths (process variants)
#
# The ordered sequence of event types per claim is its variant. A healthy RCM process
# is dominated by one "happy path"; everything else is exception handling.

# %%
paths = (
    ev.sort_values(["claim_sk", "sim_event_seq"])
    .groupby("claim_sk")
    .sim_event_type.apply(lambda s: " -> ".join(s))
)
variants = paths.value_counts()
vt = variants.head(6).rename("claims").reset_index()
vt["share"] = (vt.claims / n_claims).round(4)
print(f"{paths.nunique()} distinct variants; top 6:")
for _, r in vt.iterrows():
    print(f"  {r.claims:6,} ({r.share:.3f})  {r['index'] if 'index' in r else r.iloc[0]}")
happy = variants.iloc[0]
print(
    f"\nINSIGHT 14: {happy / n_claims:.1%} of claims follow the single happy path "
    f"(code -> submit -> acknowledge -> adjudicate -> pay -> close). The remaining "
    f"{(1 - happy / n_claims):.1%} split across {paths.nunique() - 1} exception variants, all "
    f"entered through a denial. Exception handling — not the happy path — is where the "
    f"simulated labor and delay concentrate."
)

# %% [markdown]
# ## Rework loops

# %%
rework_claims = ev[ev.sim_event_type.isin(["DENIAL_REVIEWED", "APPEAL_FILED"])].claim_sk.nunique()
appeal_claims = ev[ev.sim_event_type == "APPEAL_FILED"].claim_sk.nunique()
recovery_claims = ev[ev.sim_event_type == "APPEAL_RECOVERY_POSTED"].claim_sk.nunique()
print(f"claims entering denial/appeal rework : {rework_claims:,} ({rework_claims / n_claims:.1%})")
print(f"claims with an appeal filed          : {appeal_claims:,}")
print(f"claims with a recovery posted        : {recovery_claims:,}")
print(
    f"\nINSIGHT 15: {rework_claims:,} claims ({rework_claims / n_claims:.1%}) re-enter the "
    f"workflow through denial review, and {appeal_claims:,} escalate to a filed appeal — "
    f"of which {recovery_claims:,} ({recovery_claims / max(appeal_claims, 1):.0%}) recover money. "
    f"Rework volume tracks the {rework_claims / n_claims:.1%} simulated denial rate: every "
    f"denial is a re-worked claim, so denial prevention is the highest-leverage lever."
)

# %% [markdown]
# ## Bottlenecks — where manual touch-time concentrates

# %%
touch = (
    ev.groupby("sim_activity")
    .sim_touch_minutes.agg(total_minutes="sum", mean_minutes="mean", events="count")
    .sort_values("total_minutes", ascending=False)
)
touch = touch[touch.total_minutes > 0]  # drop AUTOMATED (0-minute) steps
print(touch.round(1).to_string())
top_act = touch.index[0]
print(
    f"\nINSIGHT 16: manual touch-time is dominated by '{top_act}' "
    f"({touch.loc[top_act, 'total_minutes']:,.0f} minutes total, "
    f"{touch.loc[top_act, 'mean_minutes']:.0f} min/claim across all claims). Per-claim, "
    f"appeal preparation is the most expensive single step "
    f"({touch.loc['appeal_preparation', 'mean_minutes']:.0f} min each) and denial review "
    f"adds {touch.loc['denial_review', 'mean_minutes']:.0f} min to every denied claim — so "
    f"the denial cohort is doubly costly (extra steps AND expensive steps)."
)

# %% [markdown]
# ## Automation candidates

# %%
auto_rows = int((ev.sim_activity == "AUTOMATED").sum())
manual_rows = int((ev.sim_activity != "AUTOMATED").sum())
# high-volume, low-complexity manual steps = candidates to automate further
cand = touch[touch.index.isin(["billing_submission", "payment_posting", "coding"])]
print(f"event rows — automated={auto_rows:,}  manual={manual_rows:,}")
print("\nhigh-volume manual steps (automation candidates):")
print(cand.round(1).to_string())
print(
    f"\nINSIGHT 17: {auto_rows:,} of {len(ev):,} event rows are already system-automated "
    f"(0 touch-minutes). Among manual steps, billing_submission runs on every claim at "
    f"{touch.loc['billing_submission', 'mean_minutes']:.0f} min each "
    f"({touch.loc['billing_submission', 'total_minutes']:,.0f} min total) — a high-volume, "
    f"low-variance step and the clearest further-automation candidate. Denial review and "
    f"appeal prep are high-cost but judgement-heavy: better prevented (via the denial "
    f"drivers in notebook 02) than automated."
)

# %%
print(
    "\nNotebook 05 complete. Twelve+ decision-relevant insights delivered across "
    "notebooks 01-05 (data quality, denial drivers, payment survival, risk-adjusted "
    "facility, process mining). ITS pending the simulated-intervention-module decision."
)
