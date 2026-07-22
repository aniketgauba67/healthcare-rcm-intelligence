# Task Board — Healthcare RCM Intelligence Platform

Rules: one owner per task; move tasks between sections with a one-line note;
a phase is DONE only when qa-reviewer checks its acceptance box.

## Phase 1 — Ingestion + Warehouse (lead: data-engineer)
> ASSIGNED 2026-07-22 by team-lead: all Phase 1 tasks → data-engineer;
> qa-reviewer reviews each task as it lands (PASS or numbered fix list, max 3
> cycles, then escalate to Blocked). Ownership ruling: data-engineer may write
> `tests/contracts/` for its own modules; qa-reviewer owns `tests/` overall
> and may amend. First task in flight: download scripts (NPPES state-filtered
> extract + CMS synthetic claims ZIP), checksums + vintages recorded in
> config/sources.yaml, actual file sizes and row counts posted here.
- [x] Download scripts + manifest + checksums for all sources in config/sources.yaml
  — data-engineer, branch feat/phase1-ingestion; awaiting qa-reviewer PASS.
  Measured 2026-07-22 (`uv run python -m src.ingestion.run`):
  | artifact | class | rows | size | sha256 (12) |
  |---|---|---|---|---|
  | cms_synthetic/beneficiary_2024.csv | SOURCE | 9,660 | 5,336,856 B (5.09 MB) | 7b32aaca2def |
  | cms_synthetic/inpatient.csv | SOURCE | 58,066 | 35,534,745 B (33.89 MB) | 4085f4ee4519 |
  | nppes monthly zip (deleted after extract) | REFERENCE | 9,671,888 NPIs | 1,145,146,362 B (1.14 GB) | 82b43e035045 |
  | nppes/nppes_ri_extract.csv (state=RI, 330 cols) | REFERENCE | 31,847 | 17,552,843 B (16.74 MB) | 04ebdbc8f14e |
  CMS synthetic vintage 2023-04 (ICD-10 RIF, pipe-delimited, 8,671 synth benes);
  NPPES vintage 2026-07 (July V2). Subset = enrollment + inpatient claims.
  Scripts idempotent (checksum-skip); NPPES streams 9 GB main file, keeps RI only.
- [x] Typed Parquet staging for claims/enrollment files (validated layer)
  — data-engineer, feat/phase1-ingestion; awaiting qa-reviewer PASS.
  `make stage` (src/validation/): rule-based dtype resolution, chunked read,
  date parse (%d-%b-%Y), structured logging, idempotent. Row counts reconcile
  EXACTLY to raw: beneficiary_2024.parquet 9,660 rows/185 cols/3 date cols;
  inpatient.parquet 58,066 rows/197 cols/33 date cols. 0 unparseable dates.
  Codes/ids/ZIP/NPI/CCN kept as text (leading zeros + signs preserved).
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
- [x] Test gate green on clean clone (qa-reviewer, merged to main bc2a7ab, pushed):
  smoke tests + pytest config; scope-expanded dependency fix (numpy<2.1 cap,
  [tool.uv] environments bounded to CPython 3.11–3.12, uv.lock committed,
  .python-version=3.11) to unbreak `uv sync` — RATIFIED by team-lead 2026-07-22;
  requires-python ">=3.11" unchanged, locked decisions intact.
