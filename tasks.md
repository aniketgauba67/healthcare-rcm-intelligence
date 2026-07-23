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
> MECHANISM (confirmed Phase 2 by qa-reviewer-p3): claim_sk is a SURROGATE key
> reassigned on every warehouse reload, so a concurrent or later `make warehouse`
> invalidates freshly-generated sim_ FKs — symptom is 100% claim_sk FK-join
> failures, which looks catastrophic but is just interleaving. Run
> `make warehouse-all` (warehouse → simulate → simulate-warehouse) as ONE
> quiet-window unit; never reload the warehouse alone while the sim_ layer must
> stay valid. Relevant to every Phase 3+ agent that touches the warehouse.
> TEAM MODEL (2026-07-23, team-lead per human): all teammates run on Opus 4.8,
> pinned via `model: opus` in every .claude/agents/*.md. Fable 5 is on a smaller
> usage pool and caused repeated mid-flight session-limit crashes; Opus/Sonnet
> share the main pool. NOTE: this team-lead session itself is still on Fable 5
> (a /model switch only affects NEW sessions, not the running one) — so the
> coordinator may still hit limits, but spawned teammates now come up on Opus.
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
> OPENED 2026-07-22 by human go-ahead after Phase 1 acceptance. Team re-spawned
> (prior data-engineer + qa-reviewer instances hit a session limit): Phase 2 =
> simulation-engineer (lead) + qa-reviewer-2 (review). data-engineer is NOT
> respawned — Phase 1 scope is closed; re-spawn only if sim work needs an
> ingestion/DDL change outside simulation-engineer's ownership.
> State verified on main 2dc4e92 by team-lead: make test 44 passed, ruff clean,
> main == origin/main, docker daemon up. Phase 1 team rules still in force
> (shared single-writer Postgres + quiet windows; live PG is the acceptance bar,
> DuckDB mirror is supplementary CI only).
> Kickoff item: simulation-engineer gets the deferred review pass on
> data-engineer's sql/ddl/30_sim_crosswalk.sql + linkage.crosswalk_seed.
> REVIEWER ROUTING (team-lead ruling 2026-07-22): the original `qa-reviewer`
> instance revived after its session limit reset, completed the Phase 1
> post-merge confirmation, and is STOOD DOWN. `qa-reviewer-2` is the sole
> reviewer for Phase 2 and for any later tech-debt fixes — one reviewer per
> phase, to avoid split verdicts and duplicate destructive runs on the single
> shared Postgres container. Send all review requests to qa-reviewer-2.
> CLAIMED 2026-07-22 by simulation-engineer: all 4 build tasks, branch
> feat/phase2-simulation. Build order: (a) simulation.yaml v0.2.0 parameter set
> + docs/assumptions.md anchors, (b) generator (pre-submission facts -> timeline
> -> latent denial -> adjudication -> appeals -> workflow events -> costs),
> (c) sql/ddl/50_sim_adjudication.sql + own loader in src/simulation/ (does NOT
> touch src/ingestion/), (d) validation suite + docs. Crosswalk review verdict
> and the crosswalk_seed decision are in the kickoff message to team-lead.
- [x] Generator: adjudication, denials, appeals, workflow events, timelines, costs
  — simulation-engineer, feat/phase2-simulation (6d708bd), SENT TO QA 2026-07-22.
  src/simulation/ (config, base, generator, validate, sim_sql_checks, load_sim,
  run) + sql/ddl/50_sim_adjudication.sql. 8 sim_ tables, causal generation order
  (pre-submission facts → timeline → latent p → outcome → money → appeals →
  events → costs). auth_required×auth_missing and payer×service_line
  interactions, 4 non-linear terms, 5% label noise, oracle AUC 0.68.
  Late filing is endogenous (generated submission date vs payer filing limit).
- [x] Calibration to cited benchmark ranges (docs/assumptions.md)
  — simulation-engineer, same commit. simulation.yaml v0.3.0; every range labeled
  DESIGN CHOICE and cited (KFF, Premier, Experian, Change Healthcare, MGMA/HFMA,
  Medicare 14-day payment floor + 12-month filing limit). Realized: denial rate
  12.8% (band 10-18%), appeal rate 36.3% of denials, overturn 48.0%, rework
  $29.88/denied claim. Documented where our output does NOT match a benchmark
  (cost to collect ~1% vs the 2-3% figure) and why, rather than tuning to it.
  Also documented the source-data DRG skew (DRG 951 = 44% of claims).
- [x] Validation suite: directional, distributional, temporal, reproducibility
  — simulation-engineer, same commit. 63 frame checks + 52 shared-SQL checks that
  run IDENTICALLY against live PG and the DuckDB mirror (warehouse_sql_checks
  single-source pattern). 28 new unit tests in tests/simulation/ (built on a
  synthetic base frame so they run in CI without the gitignored data layer) +
  2 live-PG integration tests. Reproducibility = SHA-256 of each table's
  canonical CSV, recorded in the run report.
- [x] Load sim_ tables into warehouse; provenance updated
  — simulation-engineer, same commit. `make simulate-warehouse` (own loader in
  src/simulation/, does NOT touch src/ingestion/). LIVE PG 52/52. provenance_
  register + data_dictionary updated in the same commit, plus new
  docs/simulated_forbidden_columns.md so ml-engineer gets the §4 leakage boundary
  without reading src/simulation/ (§4.5).
> POST-SUBMISSION DELTA (simulation-engineer, 2026-07-22): review HEAD is
> a242cdb, not the commit list first sent. Two self-audit commits landed after
> the review request: 3f48c18 adds a drift test that fails the build if any
> generated column is missing from docs/simulated_forbidden_columns.md (it caught
> 7 unclassified workflow-event columns, incl. sim_appeal_level which
> reconstructs the label via a max), plus `make warehouse-all` and
> `make validate-simulation`; a242cdb is simulation.yaml v0.4.0, capping the
> late-submission tail at 540 days after it was found reaching 5.3 years past the
> service date (mechanism unaffected — per-payer late-filing rates unchanged to
> 4 decimals) and documenting that payer denial rates do not rank by logit_offset
> because payer effects are multi-channel.
> FINDING FOR PHASE 4 (raised to team-lead, owner needed): config/model.yaml
> `forbidden_features` is a placeholder that does not match the schema that now
> exists. It misses 14 post-submission/latent columns on sim_claim_adjudication
> alone — including sim_provider_quality_latent, a pure answer key — plus all of
> sim_operating_costs; and 5 of its 11 patterns match zero real columns
> (sim_denial_reason and sim_recovered_* do not exist; adjudication_date and
> payment_date lack the sim_ prefix the real columns carry), so they look like
> coverage and provide none. config/model.yaml is ml-engineer's file (§5) and
> ml-engineer is not spawned, so simulation-engineer did NOT edit it. The correct
> list is published in docs/simulated_forbidden_columns.md. Recommendation:
> populating forbidden_features from that doc is the FIRST task of Phase 4,
> before any feature code, with a qa leakage test asserting the two agree.
> QA REVIEW OUTCOME (qa-reviewer-p2, 2026-07-22, branch HEAD 69c2736): build
> tasks 1-4 all PASS. Verified by commands I ran myself, not by reading:
> `make test` 78 unit pass; `ruff check`/`ruff format --check` clean;
> reproducibility = TWO independent `make simulate` builds → byte-identical
> table hashes (63/63 frame checks each, denial rate 12.76% in band); DuckDB
> mirror 52/52; LIVE PG in correct manual order warehouse 42/42 → simulate 63/63
> → simulate-warehouse 52/52 → `validate-warehouse` integration 6/6. DB end-state
> independently queried: 8 sim_ tables, 10 FKs intact, 0 orphans, all rows
> SIMULATED. Honesty pass CLEAN: every sim_ column prefixed (join-key carve-out
> only), provenance_register + data_dictionary updated in same commit as the DDL,
> assumptions.md labels every range DESIGN CHOICE with citations bracketing (not
> validating) ranges, §11 states validation proves nothing about realism, no
> "fraud" framing anywhere, forbidden-columns doc authoritative + complete
> (drift test enforces it). Leakage: sim_latent_p / sim_provider_quality_latent /
> sim_appeal_latent_p stored validation-only, each in exactly one place (unit test
> asserts no copies); no ML training path exists yet (Phase 4).
> TEST-ORDERING BUG (team-lead-assigned, tests/ = qa): FIXED and committed by me
> (69c2736). conftest.py collection-sort runs warehouse(10) before simulation(20);
> I added test_end_state.py(90) asserting the sim_ layer survived with FKs + no
> orphans, so the corruption is now an automated check, not a manual one. Verified
> the DB is coherent AFTER a full ordered integration run.
> NON-BLOCKING for Phase 2 (correctly deferred): (a) config/model.yaml forbidden_
> features placeholder is the Phase 4 GATE below, owned by ml-engineer — I will
> add the model.yaml-vs-doc leakage test then; (b) crosswalk strict-prefix tech
> debt is data-engineer's, tracked above.
- [x] ACCEPTANCE (qa-reviewer-p2): Phase 2 ACCEPTED. Signed off 2026-07-23 on
  MERGED main (HEAD ea3b747, code 58cc170), verified by commands I ran myself:
  live-PG ordered run warehouse 42/42 → simulate 63/63 (denial rate 12.7618%,
  identical metrics to the branch — reproducibility carried to main) →
  simulate-warehouse 52/52 → validate-warehouse integration 6/6; DB end-state
  queried directly (7 sim_ tables carry FKs, 20,867 adjudication rows, 0 orphans,
  0 non-SIMULATED rows). Earlier on-branch verification (69c2736): 78 unit pass,
  ruff clean, TWO independent builds byte-identical, DuckDB mirror 52/52, honesty
  pass clean (every sim_ column prefixed, register+dictionary in the DDL commit,
  every range DESIGN CHOICE with bracketing citations, §11 disclaims realism, no
  fraud framing, forbidden-columns doc authoritative+complete via drift test).
  LEAKAGE independently re-verified (team-lead requested): single-feature AUC of
  every PERMITTED column tops out at 0.557 (sim_auth_required); FORBIDDEN columns
  reconstruct the label (sim_denied_amount 1.000, sim_denial_review_date-not-null
  1.000, sim_latent_p 0.678) — the §4.5 firewall boundary is empirically correct.
  Directional validity (mechanism-strength bar) holds; per team-lead ruling,
  GB-not-beating-logistic is NOT a Phase 2 criterion (Phase 4 §7 = comparison
  REPORTED) and did not gate this. MERGE LANDED 2026-07-23 by team-lead
  (user-authorized; fast-forward feat/phase2-simulation → main, pushed to origin).
  Test-ordering bug FIXED + guarded by me (69c2736, on main). PHASE 2 DONE.

## Carry-forward / tech debt (team-lead tracked, not phase-gated)
- [ ] CROSSWALK STRICT COLUMN PREFIX (§3.2 NON-NEGOTIABLE, verified by team-lead
  2026-07-22 reading sql/ddl/30_sim_crosswalk.sql). §3.2 requires every simulated
  table AND column name prefixed `sim_`. In sim_facility_crosswalk only 3 of 11
  columns comply (facility_ccn/_name/_state/_type, match_rule, same_state,
  crosswalk_seed, provenance do not); sim_provider_crosswalk likewise. Raised by
  simulation-engineer at Phase 2 kickoff. Not merely cosmetic: the §4 leakage
  blacklist is column-name based, so an unprefixed column escaping into a
  flattened feature matrix loses its provenance marker. Owner: data-engineer
  (re-spawn; owns src/ingestion/ + sql/ddl/). MUST land before Phase 4 opens
  (leakage blacklist) and before the Phase 5 honesty pass. Deferred now only to
  avoid shared-Postgres contention with Phase 2 acceptance runs.
  NOTE: simulation-engineer adopts STRICT prefixing for all new sim_ tables —
  team-lead RATIFIED; that is the standard going forward.
- [ ] CROSSWALK SAMPLES WITH REPLACEMENT (analytic fidelity, not provenance).
  Distinct synthetic providers collide onto the same real CCN (within-state pools
  are small). Team-lead ruling: do NOT re-randomize the accepted Phase 1 crosswalk
  for this. Instead — the real facility/NPI is DISPLAY-ONLY enrichment; every
  facility- or provider-level analysis MUST key on the synthetic prvdr_num /
  claim_sk, never on facility_ccn or facility_name, or it silently merges several
  distinct synthetic hospitals. Binding on analytics-engineer (Phase 3) and
  app-engineer (Phase 5). If a 1:1 mapping is ever wanted, fix = sample without
  replacement within stratum then fall back.
  MEASURED by team-lead 2026-07-22 on live PG — the collision is MATERIAL, not
  theoretical: 4,876 synthetic billing providers map onto only 2,857 distinct
  real CCNs; 45.9% of those CCNs carry more than one synthetic provider, and the
  worst carries 8. So a naive `group by facility_ccn` merges up to 8 distinct
  synthetic hospitals into one row and inflates its volume ~8x. The keying rule
  above is therefore MANDATORY for Phase 3/5, not advisory.

## Phase 3 — Analytics + KPI Views (lead: analytics-engineer)
> CARRY-FORWARD from Phase 1 (team-lead, 2026-07-22): Phase 1 task 1 was scoped
> to "all sources in config/sources.yaml" but only the 4 data sources were
> ingested (CMS synthetic claims, NPPES-RI validation sample, Hospital General
> Information, Medicare Physician by Provider). The REFERENCE code sets —
> hcpcs, ms_drg, carc_codes (and icd10) — have NO vintage/sha256 recorded and
> are not downloaded. Not blocking Phase 2 (CARC is used as category LABELS
> only, no file needed; §3.7). It DOES bite Phase 3 (service-line/DRG naming)
> and any code-description enrichment. Owner when scheduled: data-engineer
> (re-spawn). Watch §2 vintage rule: claims are 2023-04, so ICD-10/HCPCS/MS-DRG
> references must match that period, NOT the current year.
> OPENED 2026-07-23 by human go-ahead after Phase 2 acceptance. Team (all on
> Opus 4.8 per the model pin): analytics-engineer (lead) + data-engineer
> (re-spawned for the reference-code-set prerequisite only) + a fresh
> qa-reviewer (one reviewer for the phase). Standard kickoff pattern; feature
> RE-SPAWN 2026-07-23 ~09:10 (team-lead): the first Phase 3 workers
> (data-engineer-p3, analytics-engineer) hit the ~5-hour account usage cap and
> died ~05:30 having committed no code; re-spawned as "data-engineer-refs" and
> "analytics-engineer-2" (both Opus). qa-reviewer-p4 survived (idle) and remains
> the reviewer. analytics-engineer's only WIP — an idempotent view-runner
> sql/views/apply_views.py — was preserved as 5bea9fe on feat/phase3-analytics.
> NOTE ON CRASHES: reset times run 2:50/7:50/12:50/5:50 — a hard ~5-hour ACCOUNT
> usage window, NOT Fable-specific; the Opus pin did not prevent it. Agents must
> commit early/often + post state to main before a suspected limit.
> branches; live PG single-writer + quiet-window rules in force (see the Phase 1
> TEAM RULE incl. the claim_sk warehouse-reload mechanism).
> MANDATORY RULING (from Phase 2 crosswalk audit, team-lead): every facility- or
> provider-level view MUST key on the SYNTHETIC ids (prvdr_num / claim_sk /
> sim_at_physn_npi), NEVER on facility_ccn or facility_name. The crosswalk maps
> 4,876 synthetic providers onto only 2,857 real CCNs (45.9% multiplexed, worst
> 8-to-1), so grouping by facility_ccn silently merges up to 8 distinct synthetic
> hospitals. Real CCN/name are DISPLAY-ONLY enrichment. qa-reviewer must reject
> any view violating this.
- [ ] PREREQUISITE (data-engineer): download + load REFERENCE code sets matching
  the 2023-04 claims vintage — ICD-10-CM/PCS FY2023, HCPCS 2023, MS-DRG (FY2023,
  ~v40), CARC codes (category labels only, §3.7). Record vintage + sha256 in
  config/sources.yaml per manifest rules; add REFERENCE dim tables in sql/ddl/;
  update provenance_register + data_dictionary same commit. §2 vintage rule is
  binding: FY2023 codes, NOT current year; NO CPT descriptions (AMA-licensed),
  HCPCS Level II public descriptions only. SCOPE CLARIFICATION (analytics-engineer
  verified 2026-07-23 on live PG): this blocks ONLY the code-NAME enrichment
  (dim_drg.drg_desc is 100% NULL; HCPCS/ICD-10/CARC human-readable text), NOT the
  8 core views — the sim layer already carries denial_category, sim_denial_carc_
  group (CARC as labels, §3.7-clean), driver_mechanism, and named service lines.
  So all 8 views build in parallel; only the DRG/diagnosis/procedure display-name
  enrichment waits for these tables.
  DONE 2026-07-23 (data-engineer, branch feat/phase3-references; pending qa-reviewer-p4).
  All FY2023 vintage (§2-clean, no ICD-9). Downloaded from www.cms.gov (curl
  works in-sandbox), parsed, loaded ADDITIVELY on live PG (no fact_/sim_ drop —
  verified fact_inpatient_claim=20,867 and sim_facility_crosswalk=4,876 unchanged
  across the load; idempotent). MEASURED (url + sha256 recorded in config/sources.yaml):
    - ICD-10-CM FY2023  → ref_icd10cm  73,674 dx  | zip sha256 cc7158228f6de01a…5cfe1e06
      (2023-code-descriptions-tabular-order.zip, 2,387,419 B)
    - ICD-10-PCS FY2023 → ref_icd10pcs 78,530 proc| zip sha256 e35b6e2e170ea1ef…61947c93e
      (2023-icd-10-pcs-codes-file.zip, 653,881 B)
    - HCPCS 2023 Lvl II → ref_hcpcs    7,404 codes| zip sha256 127c62b4f7745…77ca0f1cc8
      (january-2023-alpha-numeric-hcpcs-file.zip, 2,282,796 B). §3.7: CPT Lvl I,
      2-char modifiers, D-series (ADA) excluded at load.
    - MS-DRG v40 FY2023 → ref_msdrg    767 DRGs   | zip sha256 eda9acaa4b90339c…ba0fcb53
      (IPPS FY2023 Final Rule Table 5, fy2023-ipps-fr-table-5.zip, 78,312 B)
    - CARC (§3.7 labels-only, NO file, NO X12 text) → ref_carc 10 project-authored
      labels aligned to config/simulation.yaml carc_groups (16,18,22,27,29,50,96,97,181,197).
  dim_drg.drg_desc ENRICHED: 167/167 real DRGs matched ref_msdrg (0 unmatched);
  enriched rows now provenance='REFERENCE'. Files: sql/ddl/60_reference_codes.sql,
  src/ingestion/reference_codes.py, tests/contracts/test_reference_codes.py,
  tests/integration/test_reference_codes_postgres.py, Makefile `reference-codes`,
  docs/data_dictionary.md + docs/provenance_register.md updated same commit.
  Unit suite 81 passed / 5 skipped; new live-PG integration test PASS. analytics-
  engineer-2: naming enrichment can now join dim_drg.drg_desc + ref_* tables.
- [ ] 8 metric-contract views with control queries
- [ ] EDA notebooks: >= 12 insights with statistical support
- [ ] Statistical tests, survival analysis, process mining modules
- [ ] ACCEPTANCE (qa-reviewer): views reconcile, notebooks run clean

## Phase 4 — ML (lead: ml-engineer)
> GATE — FIRST TASK OF PHASE 4, BEFORE ANY FEATURE CODE (team-lead, verified
> 2026-07-22 by reading config/model.yaml against the real Phase 2 schema).
> §4 is NON-NEGOTIABLE and the current `forbidden_features` list is a
> PLACEHOLDER that does not match the schema that now exists. It is worse than
> empty: it looks like coverage and provides little. Found by simulation-engineer.
>   STALE patterns matching ZERO real columns — note two lack the sim_ prefix
>   that every generated column actually carries:
>     sim_denial_reason, sim_recovered_*, adjudication_date, payment_date,
>     post_submission_workflow_*
>   MISSING post-submission / latent columns, currently UNPROTECTED:
>     sim_provider_quality_latent (a pure answer key — provider latent quality),
>     sim_label_noise_applied (reveals whether the label was flipped),
>     sim_denial_type, sim_denial_carc_group, sim_denial_driver_mechanism,
>     sim_patient_responsibility_amount, sim_contractual_adjustment_amount,
>     sim_denied_amount, sim_ack_date, sim_adjudication_date,
>     sim_denial_review_date, sim_payment_date, sim_days_to_adjudication,
>     sim_days_to_payment, and ALL of sim_operating_costs
>     (sim_denial_rework_cost > 0 implies a denial).
> ACTION: ml-engineer populates forbidden_features from the authoritative
> docs/simulated_forbidden_columns.md (the §4.5 firewall interface — it exists
> precisely so ml-engineer never reads src/simulation/). qa-reviewer then adds a
> leakage test asserting config/model.yaml and that document AGREE, rather than
> trusting either alone. Do not begin feature work until this is done and green.
> ML-FACING CAUTIONS from simulation-engineer's Phase 2 self-audit (in
> docs/assumptions.md; surfaced here so ml-engineer reads them before modeling):
>   - ~33% of observed denials (892 of 2,663) are PURE LABEL NOISE with no
>     mechanism signal (latent mechanism denial rate ~8.8%; observed 12.76%).
>     Do not over-interpret the positive class or expect SHAP to explain every
>     denial — a third are unexplainable by construction. This is the source of
>     the ~0.64 realistic AUC ceiling (oracle ~0.68).
>   - Temporal split guidance (VERIFIED against generated data; authoritative
>     copy in docs/simulated_forbidden_columns.md §8, the §4.5 interface):
>     sim_submission_date spans 2015-2024; holding out calendar-2023 gives only
>     701 claims (3.36%) — too thin. Use an 80/20 QUANTILE split on
>     sim_submission_date (cut ~2021-12-28, ~4,173-claim / 20% forward test fold).
>     Not hold-out-last-year.
>   - Per-service-line denial ranking is a WEAK signal (Spearman latent-vs-
>     observed 0.59, p=0.056) vs per-payer strong (0.90). Ties to the
>     "show volumes alongside rates" rule and the DRG-951 concentration.
>   - ADVANCED ≈ BASELINE IS EXPECTED AND HONEST, NOT A FAILURE (team-lead
>     ruling 2026-07-22). Gradient boosting does not beat regularized logistic
>     on this layer (temporal 0.627 vs 0.636). Verified reason: the flagship
>     auth_required×auth_missing interaction is DEFINITIONALLY absorbed (you
>     cannot miss an auth that was not required, so auth_missing already equals
>     the interaction and a linear model captures it fully — correct domain
>     logic), and the one genuinely tree-only interaction (payer×service_line)
>     is thin due to source DRG skew. The generator was NOT tuned to manufacture
>     a tree edge (that would invert the §4.5 firewall and optimize impressive
>     over honest, §1). Phase 4 §7 DoD is "baseline vs advanced comparison
>     REPORTED", not "advanced must win" — report the near-null edge truthfully
>     with this domain explanation; a competitive logistic baseline is a realistic
>     and credible result for denial prediction. Documented in docs/assumptions.md
>     by simulation-engineer.
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
