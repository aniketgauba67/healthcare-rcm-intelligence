# Task Board — Healthcare RCM Intelligence Platform

Rules: one owner per task; move tasks between sections with a one-line note;
a phase is DONE only when qa-reviewer checks its acceptance box.

## Phase 1 — Ingestion + Warehouse (lead: data-engineer)
- [ ] Download scripts + manifest + checksums for all sources in config/sources.yaml
- [ ] Typed Parquet staging for claims/enrollment files (validated layer)
- [ ] PostgreSQL DDL: facts, dims, constraints, indexes, Unknown members
- [ ] Simulated-linkage crosswalk (claims → real facilities/providers, seeded)
- [ ] Data-contract tests + quarantine table + reconciliation report
- [ ] docs: data_dictionary.md + provenance_register.md v1
- [ ] ACCEPTANCE (qa-reviewer): contracts pass, FKs pass, counts reconcile

## Phase 2 — Simulation Layer (lead: simulation-engineer)
- [ ] Generator: adjudication, denials, appeals, workflow events, timelines, costs
- [ ] Calibration to cited benchmark ranges (docs/assumptions.md)
- [ ] Validation suite: directional, distributional, temporal, reproducibility
- [ ] Load sim_ tables into warehouse; provenance updated
- [ ] ACCEPTANCE (qa-reviewer): seed-reproducible, validity checks pass

## Phase 3 — Analytics + KPI Views (lead: analytics-engineer)
- [ ] 8 metric-contract views with control queries
- [ ] EDA notebooks: >= 12 insights with statistical support
- [ ] Statistical tests, survival analysis, process mining modules
- [ ] ACCEPTANCE (qa-reviewer): views reconcile, notebooks run clean

## Phase 4 — ML (lead: ml-engineer)
- [ ] Point-in-time feature store + forbidden-column leakage tests
- [ ] Model A: baselines -> XGBoost, temporal splits, calibration, SHAP
- [ ] Model C: appeal success + Expected Net Recovery work-queue score
- [ ] Slice metrics, bootstrap CIs, model card
- [ ] ACCEPTANCE (qa-reviewer): leakage tests pass, baseline comparison reported

## Phase 5 — App + Packaging (lead: app-engineer)
- [ ] FastAPI endpoints with schemas + version metadata
- [ ] Streamlit dashboard (5 pages, synthetic banner on all)
- [ ] DuckDB/Parquet demo extract for hosted deployment
- [ ] docker-compose clean-clone start; CI green
- [ ] README final, screenshots, demo script
- [ ] ACCEPTANCE (qa-reviewer): full honesty pass + reconciliation pass

## Blocked / Questions for human
(agents write here instead of guessing)

## Done
