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
> TEAM RULE (2026-07-22, from qa task-3 review): all agents share ONE local
> Postgres container; the loader is a single-writer batch job. Announce before
> running `make warehouse` / `make validate-warehouse`, and acceptance runs get
> a single-writer quiet window — never interleave loads with validation. A
> transient reconciliation failure from interleaving is expected noise; re-run
> in a quiet window before treating it as a bug. CI is unaffected (own service
> container).
- [x] Download scripts + manifest + checksums for all sources in config/sources.yaml
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (fc850f1).
  qa non-blocking notes folded: manifest `filename` now repo-relative (data/raw/…,
  fixed in task 2); closed-loop sha256/size/row_count-vs-sources.yaml reconciliation
  test deferred to Phase 1 task "Data-contract tests" / task 5 acceptance.
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
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (aaa601d).
  `make stage` (src/validation/): rule-based dtype resolution, chunked read,
  date parse (%d-%b-%Y), structured logging, idempotent. Row counts reconcile
  EXACTLY to raw: beneficiary_2024.parquet 9,660 rows/185 cols/3 date cols;
  inpatient.parquet 58,066 rows/197 cols/33 date cols. 0 unparseable dates.
  Codes/ids/ZIP/NPI/CCN kept as text (leading zeros + signs preserved).
- [x] PostgreSQL DDL: facts, dims, constraints, indexes, Unknown members
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (7002243,
  qa verified live PG16 independently). Live-load delta 7cc1dab (env-loading +
  integration test + independent live reconciliation) sent for qa delta-ack.
  sql/ddl/ (00_schema,10_dimensions,20_facts): star schema — dim_date/beneficiary/
  provider/drg/discharge_status (each with Unknown member key 0) + fact_inpatient_
  claim (header grain), fact_claim_revenue_line (line), fact_claim_diagnosis
  (unpivot); FKs, non-negative + date-order CHECKs, indexes. Idempotent loader
  (src/ingestion/load_postgres.py, `make warehouse`) + engine-agnostic transform.
  LIVE Postgres 16 (docker compose) acceptance PASSED 2026-07-22: `make warehouse`
  loads + reconciles (20,867 claims/58,066 lines/338,024 diagnoses), and
  `make validate-warehouse` (pytest -m integration) = 35/35 acceptance checks
  PASS against real PG (FK anti-joins, uniqueness, date-order, non-negative money,
  Unknown members, row counts) + idempotent re-load verified. Shared check SQL
  (src/ingestion/warehouse_sql_checks.py) runs identically in PG and the DuckDB
  CI mirror (`make warehouse-check`, 37/37) so they cannot drift. 910 null-provider
  + 2,741 null-DRG claims route to Unknown (metrics, not errors).
- [x] Simulated-linkage crosswalk (claims → real facilities/providers, seeded)
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (1d6205a,
  live PG verified). Merged to main. qa non-blocking notes folded into task 5:
  crosswalk checks now in the DuckDB CI mirror (parity, 42/42), reproducibility
  "same seed + same reference vintage" noted in provenance_register. Built per the
  team-lead+human resolution (all 3 Blocked items). References: Hospital General
  Information (5,432 facilities) + Medicare Physician by Provider (1,296,739 real
  providers, human-selected nationwide pool; full 485 MB source checksummed then
  discarded, 57 MB extract kept). `crosswalk_seed` added to config/simulation.yaml
  (delegated one-commit). src/ingestion/crosswalk.py: seeded, stratified,
  REPRODUCIBLE (same seed → identical). sql/ddl/30_sim_crosswalk.sql: sim_facility_
  crosswalk (sim_prvdr_num FK→dim_provider) + sim_provider_crosswalk, classified
  SIMULATED (sim_ prefix). LIVE Postgres load PASSED: 42/42 acceptance checks incl.
  5 crosswalk checks (FK, provenance=SIMULATED, counts); 4,876 facility + 2,463
  provider rows, 100% same-state (facility state+type; provider coherent-state +
  inpatient-plausible specialty). NPPES RI extract reclassified to validation
  sample. provenance_register + data_dictionary updated same commit.
- [x] Data-contract tests + quarantine table + reconciliation report
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (7bcf140).
  src/validation/
  contracts.py: required-columns, key-uniqueness, date-order (CLM_FROM_DT<=THRU),
  non-negative money; table-level checks gate the table, row-level failures →
  quarantine (never silently dropped). `make contracts` writes quarantine.parquet
  + data/validated/reconciliation_report.json (contracts + staged-vs-source
  reconciliation). Warehouse: sql/ddl/40_quarantine.sql (rcm.dq_quarantine,
  DERIVED) loaded by the loader. qa notes folded: closed-loop sources.yaml
  checksum test (66adccc) + money/int coercion counter (numeric_null_from_nonempty
  in stage_parquet). On this subset: all contracts pass, 0 quarantined, rows
  reconcile (9,660/58,066). LIVE PG load still 42/42; unit 42 pass, integration
  1 pass, ruff clean. docs updated same commit.
- [x] docs: data_dictionary.md + provenance_register.md v1
  — data-engineer, feat/phase1-ingestion; qa-reviewer PASS 2026-07-22 (952b521).
  Both maintained
  in-commit across tasks 1-5; v1 coherence pass done: data_dictionary has a
  pipeline overview + every layer (raw ×5 sources, validated, contracts/
  quarantine, warehouse dims/facts/sim_/dq_quarantine); provenance_register
  classifies every artifact + table/column (SOURCE/DERIVED/REFERENCE/SIMULATED)
  with the §3.4 crosswalk rule stated. assumptions.md left to simulation-engineer
  (Phase 2).
- [x] ACCEPTANCE (qa-reviewer): contracts pass, FKs pass, counts reconcile
  — qa-reviewer sign-off 2026-07-22 — live PG 42/42 + contracts + reconciliation.
  Phase 1 COMPLETE. All 6 tasks PASS. Post-merge follow-ups (non-blocking, folded):
  crosswalk checks in DuckDB mirror (done, bf35d5c) + COMMENT ON tables (73336bf).
  Phase 2 gated by the human (do not start).

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

- [RESOLVED 2026-07-22, team-lead + HUMAN] Task 4 crosswalk 3 items:
  1. SEED (team-lead ruling): add dedicated `crosswalk_seed` to
     config/simulation.yaml. data-engineer has one-commit delegated authority to
     add that single key (simulation-engineer not yet spawned; inherits the file
     and may revisit the value at Phase 2 kickoff). Do not read the generator
     `seed` for the crosswalk.
  2. sim_ DDL OWNERSHIP (team-lead ruling): data-engineer writes the
     sim_*_crosswalk DDL as part of task 4 (persona assigns the crosswalk build);
     qa-reviewer reviews now; simulation-engineer gets a review pass at Phase 2
     kickoff. §5 stands otherwise — all future sim_ DDL is simulation-engineer's.
  3. PROVIDER SOURCE (HUMAN decision — supersedes options a/b/c): use the CMS
     Medicare Physician & Other Practitioners "by Provider" dataset
     (data.cms.gov, latest year) as the nationwide provider dimension source
     (NPI, specialty, state; ~hundreds of MB; Medicare-aligned). Add to
     config/sources.yaml with checksum + vintage per manifest rules.
     Requirements: facility crosswalk state+type stratified nationwide as
     planned; provider crosswalk assigns providers stratified by the claim's
     crosswalked FACILITY state and specialty-to-service-type plausibility
     (facility/provider states coherent), seeded + reproducible; RI NPPES
     extract becomes a validation sample only (classify its role in the
     provenance register, or drop if unused); do NOT download the 9GB full
     NPPES file. FALLBACK if the dataset download fails or schema lacks
     state/specialty: facility-primary + RI-only provider crosswalk, with a
     documented limitation in docs/provenance_register.md (provider state may
     not match facility state) logged as a known issue for the Phase 5
     honesty pass.

## Done
- [x] Test gate green on clean clone (qa-reviewer, merged to main bc2a7ab, pushed):
  smoke tests + pytest config; scope-expanded dependency fix (numpy<2.1 cap,
  [tool.uv] environments bounded to CPython 3.11–3.12, uv.lock committed,
  .python-version=3.11) to unbreak `uv sync` — RATIFIED by team-lead 2026-07-22;
  requires-python ">=3.11" unchanged, locked decisions intact.
