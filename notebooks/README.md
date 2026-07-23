# EDA Notebooks — Phase 3 (analytics-engineer)

Numbered, narrative, re-runnable-top-to-bottom analysis notebooks. They read the
live warehouse (`rcm.vw_claim_enriched` + the `sim_` tables) through the shared
`analytics_common.py` helper and reproduce every statistic on each run.

## Format

These are **jupytext _percent_ notebooks** (`# %%` code cells, `# %% [markdown]`
prose). They run as plain scripts and open as notebooks in Jupyter/VS Code.

```bash
# run a notebook end-to-end (prints every insight)
uv run python notebooks/01_data_quality_and_provenance.py

# optional: pair to .ipynb (requires jupytext, not a project dependency)
jupytext --to notebook notebooks/*.py
```

Prereqs: the docker Postgres warehouse is up and loaded (`make warehouse-all`),
`.env` has `POSTGRES_*`, and the KPI views are applied
(`uv run python sql/views/apply_views.py`).

## Contents (17 decision-relevant insights, each printed as `INSIGHT n:`)

| Notebook | Focus | Methods |
|---|---|---|
| `01_data_quality_and_provenance` | book overview, cleanliness, provenance mix, DRG skew | scorecard view, composition |
| `02_denial_root_cause_and_authorization` | denial drivers; authorization ↔ denial | chi-square + Cramér's V, adjusted logistic (odds ratios) |
| `03_payment_timing_and_survival` | time-to-payment; P(paid by 30/60/90/120d) | Kruskal-Wallis, Kaplan-Meier, Cox PH + PH-assumption check + stratified refit |
| `04_risk_adjusted_facility` | provider comparison free of case-mix confounding | case-mix expected model, indirect standardization (O/E), Poisson funnel |
| `05_process_mining` | dominant paths, rework, bottlenecks, automation | variant analysis, touch-minute bottlenecks |

## Honesty rules (CLAUDE.md §3, enforced in every notebook)

- The adjudication/denial/payment/appeal/workflow layer is **SIMULATED**; only the
  CMS claim attributes (charges, DRGs, dates, diagnoses) are SOURCE.
- The **payer dimension is 100 percent simulated** (§3.5); every payer-grouped
  finding says so.
- Provider/facility aggregates key on the **synthetic `prvdr_num`**, never on
  `facility_ccn`/`facility_name` (crosswalk multiplexes up to 8:1). Real facility
  names are display-only and are **withheld from flagged-outlier tables** so no
  simulated outlier is ever attached to a real hospital's name.
- Outliers and anomalies are **review flags**, never "fraud".

## Not yet included

Interrupted time series (§7.3) is pending a team-lead decision: the Phase 2 sim
layer contains no intervention module, so there is no real intervention to analyze.
See `tasks.md` Phase 3.
