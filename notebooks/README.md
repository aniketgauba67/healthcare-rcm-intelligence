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
| `06_interrupted_time_series` | ITS **methodology, illustrative only** — no intervention module exists | segmented regression with Newey-West SEs; validated on a synthetic series, run on the real series (finds no break, as expected) |

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

## Interrupted time series (§7.3) — illustrative

`06_interrupted_time_series` implements the ITS estimator but asserts **no effect**:
the Phase 2 sim layer has no intervention module, so there is nothing real to
analyze. The notebook validates the segmented-regression estimator on a synthetic
series with a known injected effect, then runs it on the real monthly series at a
hypothetical cut (correctly finding no break). It is the harness ready for a future
simulated-intervention module. Subject to the team-lead scope decision noted in
`tasks.md` Phase 3.
