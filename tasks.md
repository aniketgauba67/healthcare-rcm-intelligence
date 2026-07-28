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
> TEAM RULE — WORKTREE HYGIENE (2026-07-23, after an agent's Bash cwd resolved to
> the shared main checkout and accidentally committed dc35be0 onto main, bypassing
> review; team-lead reset it out, no work lost — content was safe on the branch).
> The primary checkout at repo root is on branch `main`. EVERY agent MUST operate
> in its own worktree (.claude/worktrees/feat+<branch>) and run
> `git branch --show-current` before ANY commit to confirm it is on its feature
> branch, NEVER main. Never commit/reset on the shared main checkout — merges to
> main happen only after qa PASS, performed by the author on their branch.
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
- [x] CROSSWALK STRICT COLUMN PREFIX (§3.2 NON-NEGOTIABLE) — DONE 2026-07-27 by
  data-engineer-p4 on `feat/crosswalk-sim-prefix`. qa-reviewer-p9 PASS at 554a35f,
  MERGED to main by team-lead (author offline on the usage cap; post-PASS merge
  per the established pattern; not pushed to origin). qa verified independently
  rather than from the author's report: 98 passed / 9 deselected, ruff clean,
  reconciliation 21/21, the live view-boundary test EXECUTING (2 passed, not
  skipped), both renamed notebooks exit 0, and both crosswalks rebuilt from
  crosswalk_seed=20260722 frame-identical to live (4,876 / 2,463, every column)
  so §7 "same seed ⇒ identical output" survives the rename. Zero-consumers claim
  for the display_* rename confirmed by grep; drop-cascade confirmed mechanical
  necessity (`create or replace view` cannot rename an output column) with
  apply_views.py unchanged and all 9 views rebuilding atomically in one
  transaction; synthetic-id keying ruling intact (4,877 rows == 4,877 distinct
  prvdr_num; 20,867 == 20,867 distinct claim_sk; no group-by on facility_ccn/name
  anywhere). qa accepted the skip-when-views-absent change CONDITIONALLY: it is
  tolerable only because always-on enforcement genuinely moved to the DB-free
  tests/contracts/test_view_sim_prefix.py, and because the drift guard below
  turns the skip's precondition into a hard failure. qa owns a non-blocking
  follow-up — the skip sits inside the per-view loop, so 8-of-9 views present
  would skip rather than fail; they will narrow it to "entire view layer absent"
  on feat/phase4-qa post-merge. Every column of BOTH crosswalk tables now
  carries `sim_`, AND the prefix propagates through the view layer with no
  alias-back, per the team-lead ruling below. Verified against LIVE PG, not source.
  This closes the Phase 4 gate on the leakage blacklist.
    - Renamed (sim_facility_crosswalk, 8): facility_ccn/_name/_state/_type,
      match_rule, same_state, crosswalk_seed, provenance → sim_ prefixed.
    - Renamed (sim_provider_crosswalk, 7): assigned_postal_state, real_npi,
      real_provider_state, real_specialty, match_rule, same_state,
      crosswalk_seed, provenance → sim_ prefixed.
    - PREFIX PROPAGATED (supersedes the 2026-07-24 Option A alias-back):
      `vw_claim_enriched` now emits sim_facility_ccn/_name/_state/_type under
      their prefixed names; `vw_clean_claim_performance` re-exports them as
      sim_display_facility_ccn/_name/_state; `vw_work_queue_priority` emits
      sim_facility_name. `create or replace view` cannot rename an output column,
      so vw_claim_enriched.sql now drop-cascades first — apply_views.py runs it
      first and rebuilds every dependent view in the SAME transaction.
    - Files: sql/ddl/30_sim_crosswalk.sql, src/ingestion/crosswalk.py,
      src/ingestion/warehouse_sql_checks.py (provenance→sim_provenance),
      sql/views/{vw_claim_enriched,vw_clean_claim_performance,vw_work_queue_priority}.sql,
      notebooks/{01,04,README}, docs/{data_dictionary,provenance_register}.md.
    - CARVE-OUTS KEPT (documented, not violations): `claim_sk`, `prvdr_num`,
      `clm_id` and the other join keys mirror a SOURCE/DERIVED warehouse column
      and stay unprefixed. Inside src/ingestion/crosswalk.py, load_facilities()/
      load_providers() keep bare facility_ccn/real_npi/... — those are the REAL
      reference-file frames (Hospital General Information / Medicare Physician),
      classified REFERENCE, not crosswalk output columns.
    - Regression guards ADDED: tests/contracts/test_crosswalk.py
      (test_every_crosswalk_column_carries_sim_prefix, builder frames);
      tests/contracts/test_view_sim_prefix.py (NEW — static, DB-free: no view SQL
      may read or alias a bare simulated-linkage column, base view still emits all
      four, drop-cascade present); tests/integration/test_crosswalk_prefix_postgres.py
      (live catalog: both crosswalk tables AND the three views' output columns).
    - LIVE VERIFICATION (data-engineer-p4, write window 2026-07-27): crosswalk
      DDL re-applied + loader re-run (not ALTER RENAME) so the shipped code path is
      what produced the live shape. Reproducibility RE-PROVEN before and after:
      rebuilding from crosswalk_seed=20260722 reproduces the accepted Phase-1
      assignment row-for-row (4,876 facility / 2,463 provider, all columns, both
      tables). Counts unchanged. `make views` clean, reconciliation 21/21 PASS.
    - Blast-radius note: team-lead's original `git grep -nE '\b...\b'` returns a
      FALSE NEGATIVE here (\b under -E); use a plain grep.
  Not merely cosmetic: the §4 leakage blacklist is column-name based, and
  vw_claim_enriched is exactly the flattened matrix the Phase 4 feature store
  consumes — an unprefixed simulated column arriving there loses its provenance
  marker. That is why the alias-back was overruled.
  NOTE: simulation-engineer adopts STRICT prefixing for all new sim_ tables —
  team-lead RATIFIED; that is the standard going forward.
  DELEGATED AUTHORITY (team-lead ruling, §5 exception, one task only): the rename
  has a downstream blast radius outside data-engineer's ownership — sql/views/ and
  notebooks/ (analytics-engineer's files) plus docs/. analytics-engineer is NOT
  being re-spawned for a mechanical rename, so data-engineer-p4 MAY edit those
  downstream references, in the SAME commit, RENAME-ONLY — no logic, grain, join,
  or metric change. qa-reviewer-p8 verifies by re-running the 21/21 reconciliation
  gate and executing the two touched notebooks.
  CARRY-FORWARD ITEM 2 (samples-with-replacement) is NOT reopened by this: the
  synthetic-id keying rule stands unchanged and the rename did not alter it.
- [ ] CROSSWALK SAMPLES WITH REPLACEMENT (analytic fidelity, not provenance).
  Distinct synthetic providers collide onto the same real CCN (within-state pools
  are small). Team-lead ruling: do NOT re-randomize the accepted Phase 1 crosswalk
  for this. Instead — the real facility/NPI is DISPLAY-ONLY enrichment; every
  facility- or provider-level analysis MUST key on the synthetic prvdr_num /
  claim_sk, never on sim_facility_ccn or sim_facility_name (renamed 2026-07-27;
  bare facility_* no longer exists anywhere), or it silently merges several
  distinct synthetic hospitals. Binding on analytics-engineer (Phase 3) and
  app-engineer (Phase 5). If a 1:1 mapping is ever wanted, fix = sample without
  replacement within stratum then fall back.
  MEASURED by team-lead 2026-07-22 on live PG — the collision is MATERIAL, not
  theoretical: 4,876 synthetic billing providers map onto only 2,857 distinct
  real CCNs; 45.9% of those CCNs carry more than one synthetic provider, and the
  worst carries 8. So a naive `group by facility_ccn` merges up to 8 distinct
  synthetic hospitals into one row and inflates its volume ~8x. The keying rule
  above is therefore MANDATORY for Phase 3/5, not advisory.
- [x] VIEW OUTPUT COLUMNS — STRICT §3.2 TIGHTENING. CLOSED 2026-07-27 by
  data-engineer-p4 in the same commit as the crosswalk prefix fix, NOT deferred to
  Phase 5. Originally split off under the 2026-07-24 Option A ruling (view OUTPUT
  names preserved, `fx.sim_facility_ccn as facility_ccn`); team-lead OVERRULED that
  on 2026-07-27 because the alias-back defeats the purpose — vw_claim_enriched is
  the flattened matrix the Phase 4 feature store consumes and the §4 blacklist is
  column-name based, so it IS a leakage-blacklist concern, not display polish.
  Resolution: `vw_claim_enriched` exposes sim_facility_ccn/_name/_state/_type;
  `vw_work_queue_priority` exposes sim_facility_name; `vw_clean_claim_performance`
  exposes sim_display_facility_ccn/_name/_state.
  DECISION on the display_* aliases (data-engineer-p4, flagged for qa): RENAMED to
  `sim_display_facility_*` rather than left alone. Reasons — (1) §3.2 is
  column-name based and these hold SIMULATED-linkage values; (2) §4.2 names
  "provider clean-claim rate" as a Phase 4 historical-rate feature, so this view is
  a plausible feature source and the same argument that overruled the alias-back
  applies one hop later; (3) they had ZERO consumers repo-wide at the time of the
  rename (grep: only the view's own SQL and this file), so the cost was nil now and
  only grows once Phase 4/5 read it; (4) leaving them would have made
  display_facility_* the single remaining unprefixed simulated column name in the
  whole view stack. `sim_` leads because §3.2 says "prefixed"; `display_` is kept
  so the display-only signal survives.

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
> RE-SPAWN #2 2026-07-23 ~14:40 (team-lead): ALL THREE Phase 3 agents hit the ~5h
> cap together at 13:41 (reset 2pm); qa-reviewer-p4 died too because it was ACTIVE
> mid-review, not idle. Re-spawned as data-engineer-refs2 / analytics-engineer-3 /
> qa-reviewer-p5 (Opus). PRESERVED: analytics fully committed (feat/phase3-
> analytics through 9dc31a5, incl. ITS notebook 06); data-engineer's uncommitted
> reference work saved as f32098e on feat/phase3-references. WAREHOUSE: the
> reference additive write HAD executed pre-crash — dim_drg.drg_desc 167/168 + 5
> ref_* tables live; analytics reconciled against the pre-populate (NULL) state,
> so qa must re-reconcile against the populated state. OPEN: analytics 70-vs-81
> unit-test regression to resolve; DRG name enrichment now unblocked.
> branches; live PG single-writer + quiet-window rules in force (see the Phase 1
> TEAM RULE incl. the claim_sk warehouse-reload mechanism).
> MANDATORY RULING (from Phase 2 crosswalk audit, team-lead): every facility- or
> provider-level view MUST key on the SYNTHETIC ids (prvdr_num / claim_sk /
> sim_at_physn_npi), NEVER on sim_facility_ccn or sim_facility_name (those columns
> were bare facility_ccn/facility_name until the §3.2 prefix fix on 2026-07-27; the
> rule itself is unchanged). The crosswalk maps
> 4,876 synthetic providers onto only 2,857 real CCNs (45.9% multiplexed, worst
> 8-to-1), so grouping by facility_ccn silently merges up to 8 distinct synthetic
> hospitals. Real CCN/name are DISPLAY-ONLY enrichment. qa-reviewer must reject
> any view violating this.
- [x] PREREQUISITE (data-engineer): REFERENCE code sets — qa-reviewer-p5 PASS
  2026-07-23; MERGED to main 4e0adea by team-lead (authorized post-PASS merge,
  author offline on usage cap). Live counts reconcile: ICD-10-CM 73,674 /
  ICD-10-PCS 78,530 / HCPCS L2 7,404 / MS-DRG v40 767 / CARC 10 labels; dim_drg.
  drg_desc 167/168 (Unknown NULL correct); §3.7-clean; additive/idempotent, no
  fact/sim touch. `make test` 85 passed / 8 skipped on merged main. Original task:
  download + load REFERENCE code sets matching
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
- [x] 8 metric-contract views with control queries
  — analytics-engineer, feat/phase3-analytics (f13285e). qa-reviewer-p7 PASS on
  merged main (72d74b7) 2026-07-24: 9 vw_ views live (base + 8 metric-contract),
  21/21 reconciliation gate PASS, synthetic-id keying verified (no group-by
  facility_ccn; grain_is_synthetic_prvdr_num check + distinct_claim_sk==rowcount).
  BUILT + reconciled on
  live PG, pending qa. vw_claim_enriched base (1:1, 20,867) + all 8 contract
  views. Every header: grain/sources/per-column provenance/control query.
  Facility+provider grain keyed on synthetic prvdr_num (real CCN/name display-only
  left join; verified no CCN merge: distinct prvdr_num == row count). Payer views
  carry the 100%-simulated banner (§3.5). work_queue_priority = HEURISTIC
  PLACEHOLDER, model_monitoring = DRIFT SCAFFOLD, both labeled (Phase 4 replaces).
  Control queries all reconcile (denied 2,663 / open AR 1,911 / 5 payers /
  baseline-driver 1,222). Applied via sql/views/apply_views.py. DRG/diagnosis/
  procedure DISPLAY-name enrichment wired after the ref_* merge (item below).
- [x] EDA notebooks: >= 12 insights with statistical support
  — analytics-engineer (43752ef). qa-reviewer-p7 PASS on merged main 2026-07-24:
  6 numbered notebooks live (01-06, ITS=06) + analytics_common.py, 19 distinct
  INSIGHT labels (>=12 required). 5 numbered jupytext-percent notebooks in
  notebooks/ (re-runnable top-to-bottom vs live PG via analytics_common.py),
  17 insights total, all printed `INSIGHT n:`. ruff check+format clean; all 5
  execute clean. Pending qa.
- [x] Statistical tests, survival analysis, process mining modules
  — analytics-engineer (43752ef). qa-reviewer-p7 PASS on merged main 2026-07-24:
  stat/survival/process-mining in nb02-05; ITS (nb06) confirmed NOTEBOOK-ONLY
  (0 intervention/ITS refs in sql/ddl or sql/views, no warehouse table).
  chi-square + Cramer's V + adjusted logistic
  (auth↔denial, nb02); Kruskal-Wallis payment times (nb03); KM + Cox PH with
  Schoenfeld PH-assumption check + stratified refit → P(paid by 30/60/90/120)
  (nb03); risk-adjusted facility via case-mix expected model + indirect
  standardization O/E + Poisson funnel, keyed on synthetic prvdr_num (nb04);
  process mining variants/rework/bottlenecks/automation (nb05). Reconciliation
  gate added: sql/quality/view_reconciliation.py (21/21 pass), wired into
  `make views`. ITS built as illustrative harness (notebooks/06). Pending qa.
  ITS RULING (team-lead 2026-07-23): §7.3 lists an
  interrupted time series for "the simulated intervention module", but Phase 2
  built NO intervention module (plan/build gap). Resolution: implement ITS
  methodology on a CLEARLY-LABELED ILLUSTRATIVE hypothetical intervention in a
  NOTEBOOK ONLY (no intervention field/table written to the warehouse; every
  caption states "illustrative, not real/simulated operational event, no causal
  claim"; best done by inserting a KNOWN synthetic step and showing ITS recover
  it, or by demonstrating on a no-effect date). Must not leak into the KPI views
  or headline metrics. qa-reviewer-p4 verifies the labeling. Optional FUTURE
  enhancement (NOT required for Phase 3, do not reopen Phase 2 now): a real
  sim-layer intervention module with a designed ground-truth effect + treated/
  control cohorts so ITS validates against known truth.
- [x] (milestone) 8 metric-contract views + vw_claim_enriched — BUILT + reconciled
  on live PG (analytics-engineer-2, f13285e, feat/phase3-analytics); synthetic-id
  keying verified (distinct prvdr_num == row count), payer=simulated banner,
  heuristic/drift scaffolds labeled. qa-reviewer-p7 PASS on merged main 2026-07-24.
- [x] ACCEPTANCE (qa-reviewer): views reconcile, notebooks run clean
  — qa-reviewer-p7 FINAL ACCEPTANCE on MERGED main (72d74b7) 2026-07-24. PHASE 3
  DONE. Evidence (commands I ran myself):
  * `make test` on merged-main tree GREEN: 74 passed / 19 skipped / 0 failed.
    (85/8 figure needs destructive live-PG integration + gitignored data/raw;
    deliberately NOT run to preserve the enriched DB per team-lead constraint. All
    19 skips are environmental: 7 live-PG integration [no .env in worktree] + 12
    data-file [raw not in fresh worktree]; zero failures. 74+19 == 85+8 == 93.)
  * REFERENCE stream live: dim_drg.drg_desc 167/168 (Unknown NULL correct);
    ref_icd10cm 73,674 / ref_icd10pcs 78,530 / ref_hcpcs 7,404 / ref_msdrg 767 /
    ref_carc 10. Enrichment intact (no warehouse reload performed).
  * ANALYTICS stream live: 9 vw_ views (vw_claim_enriched base + 8 metric-contract),
    6 EDA notebooks (01-06, ITS=06), 19 INSIGHT labels; stat/survival/process-mining
    in nb02-05; ITS notebook-only (0 refs in sql/, no warehouse table).
  * Reconciliation: `view_reconciliation.py` 21/21 PASS against live PG. Control
    spot-checks reconcile: denied 2,663; 5 payers; clean-claim rows 4877 ==
    distinct prvdr_num 4877 (no CCN merge).
  * Synthetic-id keying: no `group by facility_ccn/name` in any view; reconciliation
    asserts grain_is_synthetic_prvdr_num + distinct_claim_sk==rowcount.
  * Sim layer intact: fact_inpatient_claim 20,867, fact lines 58,066, diagnoses
    338,024, sim_claim_adjudication 20,867, 0 orphans (claim_sk FK), crosswalk 4,876.
  * Honesty pass CLEAN: payer view carries the §3.5 100%-SIMULATED banner; every
    "fraud" mention is an explicit negation ("never a fraud flag"); work_queue
    labeled HEURISTIC + model_monitoring labeled SCAFFOLD (reconciliation-enforced).
  * Docs: 9 views registered (data_dictionary 11 vw_ hits, provenance_register 4).

## Phase 4 — ML (lead: ml-engineer)
> OPENED 2026-07-27 by team-lead on human instruction ("re-spawn only the
> teammates needed for the current phase and continue"), after reconstructing
> state from tasks.md + git log + file tree. Phases 1-3 are all qa-ACCEPTED;
> Phase 4 is the current phase. Team (all Opus per the model pin):
> ml-engineer (lead) + data-engineer-p4 (crosswalk-prefix tech debt ONLY) +
> qa-reviewer-p8 (sole reviewer for the phase — one reviewer per phase rule).
> analytics-engineer and simulation-engineer are NOT re-spawned; their scope is
> closed. app-engineer waits for Phase 5.
> WAREHOUSE DRIFT FOUND + REPAIRED 2026-07-27 (team-lead, quiet window, no agents
> running). The live DB had lost BOTH Phase 3 materializations that qa-reviewer-p7
> signed off on 2026-07-24: 0 vw_ views existed and dim_drg.drg_desc was 100% NULL.
> Diagnosis: a `make warehouse` / `make warehouse-all` reload ran after acceptance
> — that drops+recreates dim_drg (null drg_desc) and drops the dependent views by
> CASCADE, exactly as the Makefile comment on `reference-codes` warns. The star
> schema and sim layer were NOT damaged: fact_inpatient_claim 20,867,
> sim_claim_adjudication 20,867, 20 FKs intact incl. all 6 sim_→fact_ FKs, 0
> claim_sk orphans, crosswalk 4,876. REPAIR (both additive, non-destructive):
> `make reference-codes` → drg_desc 167/168 enriched, 0 unmatched, ref_* reloaded
> (73,674 / 78,530 / 7,404 / 767 / 10); `make views` → 9 vw_ views re-applied and
> the reconciliation gate 21/21 PASS. `uv run pytest -q` on main: 85 passed /
> 8 skipped. Baseline for Phase 4 is therefore green and matches the Phase 3
> acceptance evidence.
>   STANDING RULE ADDED: `make warehouse` and `make warehouse-all` LEAVE THE
>   WAREHOUSE INCOMPLETE. The correct full-rebuild sequence is
>   `make warehouse-all && make reference-codes && make views`. Any agent that
>   reloads the warehouse MUST run those last two afterwards and re-check 21/21,
>   or the next agent inherits a silently degraded DB. Applies to Phase 4 and 5.
> WAREHOUSE WRITE WINDOW (data-engineer-p4, 2026-07-27): OPENED then CLOSED for the
> crosswalk-prefix rename. Scope was deliberately narrow — sql/ddl/30_sim_crosswalk.sql
> re-applied (drop/create the two sim_*_crosswalk tables only) + crosswalk loader
> re-run + `make views`. `make warehouse` / `make warehouse-all` were NOT run, so
> fact_/dim_/sim_ adjudication and the reference code sets were never dropped and no
> sim regeneration was triggered. Post-window state re-verified: fact_inpatient_claim
> 20,867, sim_claim_adjudication 20,867, crosswalk 4,876 / 2,463, 9 vw_ views,
> dim_drg.drg_desc 167 enriched, reconciliation 21/21 PASS. ml-engineer's read-only
> access was unaffected except during the single view-rebuild transaction.
>   ROOT CAUSE CORRECTED 2026-07-27 (data-engineer-p4 reproduced it twice; I
>   confirmed the mechanism by reading the code and the live catalog). My original
>   attribution above — "someone ran `make warehouse`" — was a plausible hypothesis
>   and it was WRONG. The actual trigger is running the live-PG INTEGRATION SUITE:
>   `make validate-warehouse` / `pytest -m integration`. tests/integration/
>   test_warehouse_postgres.py calls `apply_ddl(engine)`, which drops and recreates
>   every table (twice per run, for its idempotency assertion). That CASCADE-drops
>   all 9 vw_ views and recreates dim_drg with a null drg_desc, and NOTHING in the
>   suite restores either. tests/integration/conftest.py already documents this
>   CASCADE hazard for the sim_ layer and test_end_state.py guards that layer —
>   but no guard covers the views or the REFERENCE enrichment, so the suite reports
>   fully GREEN over a warehouse it just degraded. That is why a degraded DB
>   survived a phase acceptance unnoticed.
>   The standing rule stands, with the trigger list widened: after `make
>   validate-warehouse` OR any warehouse reload, re-run `make reference-codes &&
>   make views` and re-verify 21/21.
>   REPRODUCED twice by data-engineer-p4: after each full `uv run pytest -q`
>   against live PG they had to re-run `make reference-codes && make views` to get
>   back to 21/21. So the rule extends to `make test` as well, not just
>   `make validate-warehouse`.
>   OWNER OF THE FIX: qa-reviewer-p9 (inherited from p8, who died on the cap
>   before starting it; tests/ is qa's file and data-engineer-p4 correctly
>   declined to touch it as out of task scope). Two options they proposed, either
>   acceptable: a view+reference restore step at rank 95 in tests/integration/
>   conftest.py, or extend test_end_state.py to assert 9 views and drg_desc
>   coverage. A green suite over a degraded warehouse is the defect, not the drop
>   itself.
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
> TEAM RULE — STALE-BRANCH WRITE HAZARD (team-lead, 2026-07-27, after
> qa-reviewer-p9 walked into it and independently diagnosed it). A feature branch
> that has not merged `main` can DESTROY MERGED WORK on the shared Postgres, and
> the agent doing it cannot notice, because its own tests pass against its own
> tree. Two live instances the same afternoon:
>   * qa's destructive run applied `apply_ddl` from a branch predating the §3.2
>     crosswalk rename and SILENTLY REVERTED the live crosswalk to bare column
>     names — all 8 — while row counts, FKs, views and reconciliation 21/21 all
>     looked healthy afterwards. Healthy-looking is what made it dangerous.
>   * qa's restore step then ran the pre-rename view SQL against the renamed
>     crosswalk; the build raised, apply_views.py wraps all 9 views in ONE
>     transaction, the whole rebuild rolled back, and the layer landed at ZERO
>     views rather than partially built. From outside, "restore attempted and
>     failed" and "restore never ran" look identical.
> RULE: `git merge main` in your worktree BEFORE any write to the shared database
> — DDL, loader, `make views`, or an integration run. Not after. The trigger is
> branch STALENESS, not the existence of an unmerged branch, so this applies even
> when nothing of yours is outstanding.
> Team-lead merged main into feat/phase4-ml (3cc6577) and feat/phase4-qa (8bbb423)
> on 2026-07-27 in a quiet window with no agents running, to clear the hazard
> before work resumed. Both verified after: ml 211 passed / 12 skipped, qa 205
> passed / 4 skipped / 1 failed (the intentional blocker below), ruff clean on both.
> CRASH + RE-SPAWN #2 2026-07-27 18:37Z (team-lead): ml-engineer-2 and
> qa-reviewer-p9 hit the cap TOGETHER again, ~4.5h after the previous pair. Reset
> 6:50pm America/New_York. Re-spawned as ml-engineer-3 and qa-reviewer-p10.
> PRESERVED before re-spawning: 34a5b1c on feat/phase4-ml = Model C + work-queue
> WIP (appeal.py 315 / model_c.py 456 / work_queue.py 316 + a 47-line
> appeal_economics block in config/model.yaml), verbatim, unreviewed and unrun,
> with NO Model C training run or metrics. qa's worktree was CLEAN — everything
> committed through 47df189, which is the discipline the crash notes ask for.
> Warehouse verified healthy after both crashes: 9 views, 20,867 claims, 0
> orphans, drg_desc 167/168, 0 unprefixed crosswalk columns, reconciliation 21/21.
> CRASH + RE-SPAWN 2026-07-27 13:51Z (team-lead): ml-engineer and qa-reviewer-p8
> hit the ~5h account cap TOGETHER, mid-task, exactly as in Phase 3. Reset 1:50pm
> America/New_York. Re-spawned as ml-engineer-2 and qa-reviewer-p9 — the cap is
> per-SESSION, not account-wide, so a fresh session comes up immediately. That is
> worth knowing for every future crash on this project: preserve, re-spawn, carry on.
> PRESERVED by team-lead before re-spawning (both verbatim, unreviewed and unrun
> by me): c82542f on feat/phase4-ml = Model A scaffolding (baselines/preprocess/
> evaluate + a 16-line extract.py delta), no training run or metrics yet;
> 9355e92 on feat/phase4-qa = qa's in-progress merge of ml's feature store for
> review plus a 44-line WIP hardening of the agreement test.
> DB verified coherent after both crashes: 20,867 claims, 9 views, drg_desc
> 167/168, crosswalk 4,876, 0 orphans, reconciliation 21/21.
- [x] GATE 1 (ml-engineer): populate `config/model.yaml` forbidden_features from
  docs/simulated_forbidden_columns.md. CLOSED 6eeae82 — the exact 27 columns the
  doc names for Model A, parsed STRUCTURALLY from the document (section →
  subsection → table cell) with set equality asserted in BOTH directions, so a
  doc column the config omits and a config column the doc never named both fail
  the build. A deliberate-drift test proves the check can fire. All 5 stale
  patterns gone; all 16 previously-unprotected columns blocked, each named in a
  regression test. Reported: lint clean, 120 passed / 16 skipped.
  THREE ADDITIONAL KEYS, all team-lead APPROVED 2026-07-27, kept SEPARATE from
  `forbidden_features` so the doc-agreement surface stays exactly equal:
  (a) forbidden_derived_features — 9 label-derived columns from sql/views/
      (clean_claim_flag, first_pass_paid_flag, adjudicated, ar_open_flag,
      ar_balance_amt + work_queue's 4). These fell in the gap between two owners:
      the firewall doc covers generated columns, sql/views/ is analytics'. §4.1
      requires blocking derived columns, so they belong in the guard.
  (b) forbidden_source_features — medicare_source_paid_amt / ncvrd_charge_amt /
      bene_deductible_amt. REAL CMS SOURCE columns, so the firewall doc is silent
      on them by construction. RULING: §4's list is introduced "at minimum", so
      the operative test is point-in-time knowability, not provenance class — and
      all three are the payer's adjudication determinations. billed_charge_amt
      stays permitted (it is what the provider bills).
      MEASURED cost of the exclusion (team-lead, n=20,867): corr with the label
      0.0477 / 0.0117 / 0.0008, and clm_pmt_amt's 0.0477 is IDENTICAL to
      billed_charge_amt's — both just track claim size. So the exclusion is NOT
      empirically load-bearing here: these are real Medicare outputs and the label
      is simulated and independent of them. It is load-bearing on the pipeline
      being correct AS IF the data were real, which is the §1 credibility
      property. Model card must say that and must NOT imply a live leak was caught.
  (c) forbidden_crosswalk_tables — both pre- and post-rename spellings, so
      data-engineer-p4's change could not open a window in either direction.
      Synthetic prvdr_num deliberately NOT blocked: it is the mandatory grouping key.
  MODEL C boundary configured under its own `model_c` key (post-denial facts
  legitimately available, §5 of the doc). Two calls team-lead UPHELD:
  sim_denial_driver_mechanism stays forbidden even for Model C (it is the
  generator's statement of WHY, not something on a remittance advice — admitting
  it would invert the §4.5 firewall through a column name), and
  sim_appeal_disputed_amount stays out with sim_denied_amount as the legitimate
  substitute for the same economic quantity.
  OPEN, carried to ml-engineer-2: classify clm_utlztn_day_cnt. It differs from
  length_of_stay_days on 100% of claims so it is not a duplicate, and the RIF
  covered-day count is a benefit determination. Constant offset ⇒ harmless;
  varies ⇒ forbidden_source_features. Must not stay silently permitted.
- [x] GATE 2 (qa-reviewer-p8): tests/leakage/ built — 63062dd + 4ca8e35.
  firewall_doc.py parses the doc section-aware; detectors.py is four probes
  CALIBRATED on the live 20,867-claim layer rather than by eye (name matching,
  uncertainty coefficient U(x|f) for renames/logs/rescalings/re-binnings, a
  single-feature AUC ceiling derived from the oracle for ratios and aggregates,
  and an identifier probe for claim_sk under a new name). test_detectors.py
  proves BOTH directions every run: silent on a permitted-only matrix, rejects
  eleven separate disguises of a forbidden column — "a guard never shown to catch
  anything is worse than no guard".
  Two calibration findings fixed rather than tuned around: clm_id is row-unique
  so U=1.0 against everything (keys excluded from the truth side), and every
  post-submission date is submission + lag so the permitted anchor is legitimately
  0.856-determined by sim_ack_date — loosening past that would also pass renamed
  forbidden columns at 0.965, so date-vs-date pairs are carved out to a probe that
  rejects any date-typed feature the doc does not name.
  The agreement test was RED on the placeholder config, independently reproducing
  5 dead patterns and 26 unblocked forbidden columns — which is the point.
  qa-reviewer-p9 inherits an unrun 44-line WIP hardening of it (9355e92).
- [x] Point-in-time feature store — ml-engineer, e6a1b44, PENDING qa-reviewer-p9.
  40 features over 20,867 claims, built from BASE TABLES with an explicit column
  allowlist rather than from vw_claim_enriched — that view carries the label, the
  money and the latent probability, so selecting it wholesale would leave a
  drop-list as the only thing between them and the model. Every feature declares
  its source columns (src/features/spec.py) and the build refuses a frame that
  does not match the declaration: a name-based guard cannot tell
  payer_prior_denial_rate from payer_denial_rate, and only one is a feature.
  §4.2 historical rates are prior-period with a 60-DAY EMBARGO — "submitted
  before t" is not "known before t", since a claim submitted last week has not
  come back from the payer yet. Embargo comes from the config's posting cycle and
  is deliberately NOT fitted to observed adjudication timing (that would design a
  feature out of a forbidden column). Rates shrink toward the prior-period book
  rate; no history yields NULL, never a silent zero, because zero would tell the
  model the first claims in the warehouse came from flawless providers.
  §4.3 split is the prescribed 2021-12-28 quantile cut: 16,694 train / 4,173 test,
  with the calibration fold carved temporally off the END of the training window
  so isotonic sees neither the estimator's fit rows nor anything from the test
  period. Point-in-time safety is tested BEHAVIOURALLY, not structurally:
  truncate at t and past features must be bit-identical, scramble every post-t
  label and the past must not move, flip one claim's own outcome and its own
  features must not move — with a CONTROL test asserting a naive whole-dataset
  provider rate DOES break all three, so the checks are known to be sensitive.
  Feature names keep the sim_ prefix through engineering (§3.2) so provenance
  survives into the matrix, the SHAP plots and the dashboard.
  DETAIL preserved from ml-engineer's own board entry when team-lead reconciled
  the merge conflict (main's block is authoritative; these facts existed only on
  the branch):
    - A fifth config key beyond the three team-lead ruled on: `forbidden_tables`
      + `forbidden_table_columns` expand §2's WHOLE-TABLE forbids (sim_appeals,
      sim_operating_costs) into real column names, with an integration test
      re-checking the expansion against information_schema. NOTE: this is the key
      qa-reviewer-p9's fixture later misread as an annotated dict and took the
      KEYS of, folding two table names into the blacklist and dropping fourteen
      real column names — the protection held; the test reading it did not.
    - Split numbers: cut 2021-12-28, train 16,694 / test 4,173 (20.0%), test base
      rate 0.1205 vs train 0.1294 — matches firewall doc §8 exactly. Calibration
      fold carved off the END of train (fit 13,356 / calibrate 3,338; latest
      calibration row 2021-12-28 < earliest test row 2021-12-29), so isotonic sees
      neither the fit rows nor the test fold.
    - Historical-rate shrinkage m=20 toward the prior-period book rate, which
      itself moves. No history ⇒ null, never a silent zero.
    - Leak canaries: no single numeric feature exceeds ROC-AUC 0.75 alone, and no
      categorical level with ≥100 claims determines the label.
    - CORRECTION ml-engineer recorded against itself: it first assumed provider
      history would be sparse ("median provider has 2 claims"). Wrong at the CLAIM
      level — volume is concentrated (top 10% of providers hold 53% of claims), so
      72% of all claims and 83% of post-2019 claims DO have provider history.
      Provider-weighted and claim-weighted are different questions; the model card
      must say the latter.
- [x] Model A: baselines -> XGBoost, temporal splits, calibration, SHAP
  — ml-engineer-2, 5097f08, `make train` runs it end to end. PENDING qa-reviewer-p9.
  Forward test fold 4,173 claims / 503 denials / base rate 0.1205:
    base_rate           ROC 0.5000  PR 0.1205  Brier 0.10608
    payer_rule          ROC 0.5921  PR 0.1514  Brier 0.10489
    logistic            ROC 0.6254  PR 0.2210  Brier 0.10280   <- CHAMPION
    xgboost             ROC 0.6257  PR 0.2078  Brier 0.10418
    logistic + isotonic ROC 0.6185  PR 0.1972  Brier 0.10368
    xgboost  + isotonic ROC 0.6256  PR 0.1982  Brier 0.10593
  XGBoost − logistic ROC-AUC = +0.0003 [−0.0173, +0.0183] — interval spans zero by
  an order of magnitude, REPORTED AS NO DIFFERENCE per the standing §7 ruling, with
  the domain explanation. Nothing tuned; no hyperparameter search exists in the
  repo and config records why. 0.625 sits below the 0.68 oracle and lands where the
  Phase 2 self-audit predicted — corroboration, not coincidence. Logistic wins
  PR-AUC outright. Champion selected on the calibration fold, never on test.
  Everything the model card quotes is written by the run into models_artifacts/
  model_a/, nothing typed by hand. 223 passed / 12 skipped (was 178/12), lint clean.
- [x] clm_utlztn_day_cnt CLASSIFIED — FORBIDDEN (ml-engineer-2, cdc93e3). Closes
  the item team-lead opened at GATE 1. Team-lead REPRODUCED every figure
  independently on live PG before upholding it: clm_utlztn_day_cnt − (discharge −
  admission) is BIMODAL, not a constant offset — 0 on 19,295 claims (92.47%) and
  −1 on 1,572 (7.53%) — while length_of_stay_days == span+1 on 20,867 of 20,867.
  The one-day-short cohort carries 7.2x the mean non-covered charge ($2,783.69 vs
  $385.43) and a higher rate of any non-covered charge (47.2% vs 37.9%), so the
  missing day is the payer declining to count a day as covered — the same
  adjudication event nch_ip_ncvrd_chrg_amt already records in dollars. Discharge
  status cannot explain it (single value across the warehouse). Removed from the
  EXTRACT QUERY as well as the spec, so it is never read rather than read-then-
  dropped. Feature store 40 → 39. length_of_stay_days stays permitted.
  tests/leakage/test_covered_days_boundary.py re-measures both facts on live PG so
  a future load cannot silently invalidate the call.
> COST-MATRIX RULING (team-lead, 2026-07-27, raised by ml-engineer-2). The
> configured matrix is DEGENERATE: $25 review against a mean $3,800 at stake at a
> 12% denial rate makes reviewing an average claim worth ~$456, so the cost-optimal
> threshold flags 99% of the queue. The fault is CONCEPTUAL, not a bad constant —
> `prevented_denial_value_multiplier: 1.0` asserts both that a review prevents the
> denial with certainty AND that a denial costs the FULL claim value. The second is
> the worse error: denials are appealed and substantially overturned, so the real
> loss is rework cost + unrecovered fraction + carrying cost of delay, not the claim.
> RULING: decompose the multiplier into named factors — P(review prevents | flagged
> and worked) × (share of claim value permanently lost when a denial occurs) — each
> set from PUBLISHED benchmarks with citations, labelled DESIGN CHOICE, mirroring
> docs/assumptions.md. config/model.yaml is ml-engineer's under §5.
> TWO HARD CONSTRAINTS: (1) do NOT derive the factors from the generator's realized
> overturn/rework rates — that reaches through the §4.5 firewall to set a business
> parameter and makes the operating point a function of what the firewall exists to
> hide; published benchmarks only. (2) Do NOT choose factors to produce a pleasing
> flagged share. Pick each on its own merits and report whatever threshold falls
> out, even if still degenerate — a cost matrix reverse-engineered from a desirable
> operating point is the same failure as tuning a model to beat a baseline, and §1
> forbids it in the same terms. If honest parameters still flag 99%, that is a
> finding about this problem's economics and must be said plainly.
> The CAPACITY-CONSTRAINED point is now the PRIMARY reported operating point (a work
> queue is ranked against finite analyst hours, not thresholded): top 10% of queue
> catches 20.9% of denials at 26.3% precision, 2.2x base rate. The sensitivity sweep
> (flagged share 0.6% → 98% as the multiplier moves 0.005 → 1.0) stays a first-class
> artifact regardless — a business parameter nobody measured is a guess, and the
> sweep is the honest representation of a guess.
> DOLLARS-AT-RISK — TEAM-LEAD RULING BELOW WAS WRONG, CORRECTED 2026-07-27 by
> qa-reviewer-p10's measurement. I compared a POINT (constant scorer 20.4%) against
> a MARGINAL interval ([16.0, 59.3]) and concluded the metric supported no claim —
> which is the very error I was in the same breath telling ml-engineer to fix. The
> question needs a PAIRED interval on the DIFFERENCE. qa measured it with the
> shipped pipeline: champion − base_rate +0.1793 [+0.036, +0.532] (P(diff<=0) ~0.01)
> and champion − payer_rule +0.2962 [+0.071, +0.534], stable across five seeds
> (1337/7/42/20260727/99991) with zero exclusions. It does NOT span zero. The
> unpaired comparison erred in the CONSERVATIVE direction: the metric DOES support
> a claim and my framing understated the model.
> ADOPTED MODEL-CARD LINE: "~18pp more denied dollars than arbitrary ranking,
> magnitude poorly determined." The ban on "38.4% vs 20.4%" STANDS for the original
> reason — ten claims hold 50.9% of denied dollars, so the direction is defensible
> and the point estimate is not. The paired instrument is still required in
> train.py:440, which still reports three unpaired intervals.
> FIREWALL NEAR-MISS, recorded rather than scrubbed (qa-reviewer-p10, 2026-07-27;
> team-lead verified the arithmetic). config/model.yaml:452 anchored
> appeal_processing_cost_usd partly against "the simulation's own realized $29.88
> per DENIED claim". That is exactly avg(sim_denial_rework_cost + sim_appeal_cost)
> over the 2,663 denied claims = 29.8818, and sim_operating_costs is a FORBIDDEN
> table for both models. RULING: the reference comes OUT of config/model.yaml —
> anchoring to a generator-realized value, even as a consistency remark, makes the
> operating point a function of the layer the §4.5 firewall exists to hide.
> ATTRIBUTION CORRECTED by team-lead: qa first recorded this as "a forbidden table
> was queried". Not established, and the likelier route is innocent — the figure is
> PUBLISHED in tasks.md line 164 and docs/assumptions.md line 379, both of which
> ml-engineer is expected to read; §4.5 forbids src/simulation/ and
> config/simulation.yaml, not those docs. A recorded §4.5 breach is the most serious
> accusation available on this project and must not rest on inference when a
> published source explains it — the same failure mode as p9's fixture that reported
> blocked columns as unblocked.
> HOLE THIS EXPOSES (nobody's fault, for Phase 5): docs/assumptions.md and tasks.md
> REPUBLISH generator-realized values to an agent firewalled from the generator. The
> firewall is enforced on source files and leaks through documentation.
> OWNERSHIP RULING (team-lead, following the Phase 1 precedent): tests/leakage/ is
> qa-reviewer's — a guard authored by the party it constrains is not a guard.
> tests/models/ and tests/features/ are ml-engineer's for its own modules, with qa
> owning tests/ overall and free to amend. cd3e30c's tests/leakage/
> test_persisted_matrix.py moves or is adopted by qa, qa's call.
>   RESOLVED: qa ADOPTED it rather than moving it, with a better reason than my
>   ruling gave — its subject is the discovery contract tests/leakage/ PUBLISHES,
>   not src/features/ behaviour, so moving it would put a guard on qa's own
>   contract inside the constrained party's directory: the same inversion the
>   ruling rejects. qa's gate tests in tests/models/ now carry a header stating
>   they are qa-authored, expected red, and not to be edited green.
>   ADOPTING IT FOUND A DEFECT, and it is the restore failure mode in miniature:
>   the staleness check called build_training_matrix(refresh=True), which
>   PERSISTS — so the guard rewrote the committed artifact as a side effect of
>   checking it, and thereby repaired the very condition it existed to detect. A
>   stale file would fail once and pass forever after. "Was never stale" and "was
>   stale and quietly rewritten" were indistinguishable. Fixed: builds through the
>   same path without persisting and asserts sha256 unchanged across itself
>   (digest 479ea5b57d605acc before and after, 11 passed).
> SECOND WRITER — ml's, NON-BLOCKING (flagged by qa, team-lead ruling): src/models/
> train.py:258 persists the matrix on every training run, and test_train_postgres
> trains against live PG, so a full suite run dirties the COMMITTED manifest.
> Reproduced at will; qa hit and reverted it twice, catching the second only by
> inspecting a `git add -A`. Content is byte-stable — same parquet sha256, 20,867
> rows, every rebuild today, which is an independent re-proof that the feature
> store is reproducible — and the only moving field is the embedded wall clock.
> RULING: drop `written_at_utc` from the COMMITTED manifest (or write it only
> under `make features`). A committed artifact that changes on every test run
> trains reviewers to ignore its diff, which is precisely how a real content
> change would slip through unnoticed. Reproducibility wart, not a leak — fix it,
> do not gate on it.
> TEAM-LEAD PROCESS CHANGE (2026-07-27, prompted by qa's staleness guard firing on
> MY commit): I have been committing board updates to main frequently through the
> day, and every such commit makes both agent branches stale, which now correctly
> BLOCKS their destructive integration tests. The guard is right; my cadence was
> wrong. From here I BATCH board commits to main and announce them to both agents
> in one message so they merge once, rather than discovering staleness mid-run.
> NOTE FOR THE RECORD: that guard, written this afternoon, caught a genuine
> staleness event in production conditions hours later — d5927c2 landing on main
> mid-session — and blocked all 14 destructive tests before any reached apply_ddl.
> Third incident today it would have prevented.
> SUPERSEDED RULING FOLLOWS — kept for the record:
> DOLLARS-AT-RISK RULING: as measured, champion captures 38.4% of denied dollars in
> the top decile with CI [16.0%, 59.3%] against a constant scorer at 20.4% — and
> 20.4% lies INSIDE that interval, so the champion is NOT distinguishable from
> arbitrary ranking on this metric. Reporting "38.4% vs 20.4%" would imply a
> superiority the numbers do not support. Required fix is to the INSTRUMENT: a
> PAIRED bootstrap CI on the DIFFERENCE over the same resamples (the discipline
> already applied to XGBoost − logistic). Report whatever it says; if it spans zero,
> state that the metric cannot support a business claim at this fold size, and that
> the ten largest denied claims holding 50.9% of denied dollars is why.
> ml-engineer-2 also fixed the metric's tie-break: it broke ties by row order on a
> date-sorted frame, so a "constant" scorer silently meant oldest-claims-first.
> INHERITED BUG FOUND + FIXED (ml-engineer-2): the preserved WIP baselines declared
> (BaseEstimator, ClassifierMixin) in that order; sklearn 1.9 resolves estimator
> type along the MRO, so is_classifier() returned False and sklearn silently handed
> the full two-column predict_proba to anything asking for a score — reproduced as a
> calibrator fitted against the wrong class, output negatively correlated with its
> own input. Nothing raises. Phase 4 numbers unaffected (train.py indexes [:,1]);
> Phase 5 would have hit it.
> FOR PHASE 5 / app-engineer: metrics.json was emitting bare NaN tokens — valid
> Python, INVALID JSON — and app-engineer parses that file. Fixed with allow_nan=
> False and sanitised to null. Do not reintroduce it with a convenience dump.
> QA STREAM — landed on feat/phase4-qa (07f9f4b, dea1444, 2a9a69d, a1dd591,
> 47df189). Consolidated by team-lead when reconciling the merge conflict; qa's
> full verbose record is in those commit messages.
> DRIFT GUARD — DONE AND PROVEN. Two halves, because either alone is worse than
> useless: test_warehouse_restore.py (rank 80) re-runs the documented repair
> inside the suite, using the SHIPPED sql/views/apply_views.py so a run cannot
> preserve a stale view shape; test_end_state.py (rank 90) then asserts both the
> views and drg_desc actually came back. Restoring without asserting relocates the
> blind spot; asserting without restoring turns `make test` permanently red on any
> populated dev warehouse, and a guard that can never go green gets deleted.
> Compared against a baseline captured BEFORE the first integration test (autouse
> session fixture), so the property is "no worse than we found it", NOT "fully
> materialised" — a fresh clone with no views passes, losing views that were there
> fails. Observing the precondition instead of assuming it is what stops this
> guard excusing itself when its subject goes missing.
> PROVEN BOTH WAYS: with the repair module deselected the run FAILS naming all
> nine destroyed views — the identical run previously reported green; with it
> enabled, 34 passed / 2 skipped and afterwards 9 views, drg_desc 167/168,
> reconciliation 21/21.
> CORRECTION to team-lead's account of the drift: dim_drg.drg_desc self-heals via
> test_reference_codes_postgres, which re-runs the reference load — but ONLY where
> the gitignored raw downloads exist. On a checkout without them that test skips
> and the enrichment stays lost. So the incidental repair was masking the defect
> on exactly the machines least able to notice.
> ORDERING FIX: test_live_leakage resolved forbidden patterns against the full rcm
> catalog, and some name DERIVED columns that exist only in sql/views/. At rank 50
> it read a warehouse whose views had just been dropped and called those patterns
> dead — a leakage guard reporting safety because its subject was missing, the
> same failure class it was built to hunt. Moved to rank 85, between repair and
> end-state.
> §3.2 PREFIX-REGRESSION GUARD (new, third end-state assertion): apply_ddl rewrites
> the crosswalk tables from the DDL of whichever BRANCH the suite runs from, so
> qa's own pre-rename destructive run SILENTLY REVERTED the live crosswalk to bare
> column names — all 8 — while row counts, FKs, views and 21/21 all looked healthy
> afterwards. That is what made it dangerous. The guard now asserts no column that
> carried sim_ before a run may lose it; replayed against the degraded shape it
> names all 8. The restore deliberately does NOT paper over this (re-applying a
> branch's own SQL is correct for that branch) — the guard makes it visible instead.
> FIVE DEFECTS FOUND IN QA'S OWN TESTS by running them against a real matrix rather
> than reading them; underlying protection verified intact before any was touched.
> Worst two: (1) the fixture read `forbidden_table_columns` (a TABLE→columns map)
> as an annotated dict and took its KEYS, folding two table names into the
> blacklist, dropping fourteen real column names, and reporting seven
> sim_operating_costs columns as unblocked when they were blocked all along — a
> reviewer trusting its own output would have filed a false leak report and burned
> a cycle. (4) `_is_datelike()` recognised pd.Timestamp but not datetime.date, and
> PostgreSQL `date` arrives as object holding datetime.date — so the documented
> date-vs-date carve-out NEVER applied to the live truth frame at all, and live and
> CI were not measuring the same thing. A carve-out that silently does not apply is
> worse than none, because it is documented.
> RELAXATIONS RE-PROVEN TO BITE: seven disguised forbidden columns injected into
> the real matrix (rename, log1p, ratio-to-billed, quantile re-bin, rescale,
> null-indicator, rounded latent p) are all rejected live. An eighth (equal-width
> binning) escaped; qa investigated rather than tuning and found it puts 98.8% of
> rows in one bucket and scores AUC 0.5046 against the label — a column predicting
> at chance is not a leak, and the information-preserving version of the same
> disguise is caught at 0.9682.
> EMPIRICAL FIREWALL RESULT (independent instrument, agrees with Phase 2's 0.557):
> strongest single-feature AUC in the feature store is 0.5859 (sim_payer_id), then
> 0.5834 / 0.5529 / 0.5453, against an oracle ceiling of 0.6778. Nothing above the
> ceiling, no leak signature. Measured on the 44-column matrix qa materialised
> itself; MUST BE RE-RUN on the current 39-feature store (qa reviewed a tree three
> commits stale) before the model card cites it.
- [ ] BLOCKER (ml-engineer): wire the training matrix so qa's §4.1 guard stops
  skipping. `tests/leakage/test_training_matrix_guard.py::test_guard_is_wired_
  once_a_feature_store_exists` FAILS: src/features/ holds 6 modules and no matrix
  is discoverable by any route in the contract — `src/features/__init__.py` is
  empty, `build_model_a_frame(engine, config)` needs an engine so the no-arg route
  is unsatisfied, and nothing is persisted. The earlier board claim that leakage
  tests pass was true only in the sense that the probes SKIP. p8 built that
  fail-if-the-skip-outlives-the-feature-store assertion for exactly this case and
  it is doing its job. Fix is cheap: persist to artifacts/features/, set
  RCM_FEATURE_MATRIX, or expose a no-arg build_training_matrix(). TEAM-LEAD
  RULING: this test STAYS RED until wired, and it is a GATE on Phase 4
  acceptance, not a nice-to-have. qa materialised the matrix by hand to run the
  probes (88 passed, 0 failed, store clean) — that is a reviewer's workaround, not
  a substitute for the wiring.
  CLOSED cd3e30c (ml-engineer-3), VERIFIED by qa-reviewer-p10 rather than read:
  test_training_matrix_guard.py is 5 passed, and the four §4.1 VALUE probes now
  RUN instead of skipping. The committed 44-column parquet = the 39 declared
  features + 4 passthrough (claim_sk, prvdr_num, sim_denial_flag, sim_submission_
  date) + split; qa confirmed the 39 feature columns and their values are
  IDENTICAL to a live `prepare_matrix` build off PG, same claim_sk row order, so
  the guard checks the object the model actually saw. The label sitting in that
  file is not a hole: p8's discovery contract names sim_denial_flag as the label
  and split/claim_sk as key, documented before any of this was built, and the
  value probes need y to compute anything at all.
- [x] MODEL A REVIEWED — qa-reviewer-p10, PASS on the model, with two RULING
  DEFECTS held open below. `make train` REPRODUCED end to end on the current
  39-feature store: every figure in the 5097f08 report matches to the digit
  (logistic ROC 0.6254 / PR 0.2210 / Brier 0.10280 champion; xgboost − logistic
  +0.0003 [−0.0173, +0.0183]; folds 13,356 / 3,338 / 4,173; base rate 0.1205).
  Calibration plot and payer / service_line / value_band / facility_provider
  slices all written. metrics.json re-verified strict-JSON clean: 0 bare NaN
  tokens, parses with parse_constant raising. The sklearn MRO fix is in place
  (ClassifierMixin first on both baselines).
> FIREWALL RE-MEASURED ON THE CURRENT 39-FEATURE STORE (qa-reviewer-p10), which
> the model card was blocked on. Strongest single-feature AUC 0.5871 sim_payer_id,
> then 0.5834 sim_payer_prior_denial_rate / 0.5530 sim_auth_required / 0.5453
> sim_provider_prior_denial_rate. NOTHING at or above the 0.6778 oracle ceiling.
> Confirms p9's finding on the current tree — no leak signature.
>   PRECISION NOTE the model card must respect: the suite prints 0.5859 for
>   sim_payer_id and qa measures 0.5871, and both are right. test_live_leakage
>   scores on `shared`, the intersection of the matrix with the live truth frame;
>   0.5871 is the full 20,867. Same feature, same labels, different population —
>   qa proved the data identical (same values, same row order, same codes) before
>   attributing the gap. Quote a number WITH its population, not as a constant.
> RULING 1 — COST-MATRIX DECOMPOSITION: NOT SATISFIED, still deferred.
> config/model.yaml:335 still reads `prevented_denial_value_multiplier: 1.0`. No
> factors, no citations. The comment block describes the degeneracy honestly but
> the ruling asked for the parameter to be REBUILT from named cited factors, and
> the sensitivity sweep is not a substitute for it.
>   AND A CONSTRAINT-1 ANCHORING. The appeal_economics comment anchors $45
>   against "the simulation's own realized denial rework + appeal cost is $29.88
>   per DENIED claim". qa measured it on live PG: avg(sim_denial_rework_cost +
>   sim_appeal_cost) over the 2,663 denied claims is 29.8818, exact. The match is
>   not coincidence.
>   ATTRIBUTION CORRECTED 2026-07-27 (team-lead challenged qa-reviewer-p10's first
>   write-up; qa verified the challenge and it is right). qa wrote that "a
>   forbidden table was queried". THAT DID NOT FOLLOW and is withdrawn. The figure
>   is PUBLISHED in two places ml-engineer is expected to read — tasks.md:164 (the
>   Phase 2 record) and docs/assumptions.md:379 — and §4.5 firewalls
>   `src/simulation/` and config/simulation.yaml, NOT the board. qa confirmed both
>   citations by grep before accepting the correction. The board is the
>   overwhelmingly likely route. ESTABLISHED: a generator-realized figure
>   influenced a business parameter. NOT ESTABLISHED: by what path. Recorded this
>   way on purpose — a §4.5 breach is the most serious accusation available in
>   this project and must not rest on an inference when a published source
>   explains it. This is p9's own lesson (a fixture misreading its input would
>   have produced a false leak report) applied to the reviewer.
>   THE SUBSTANTIVE FINDING SURVIVES and team-lead has RULED: the reference comes
>   OUT of config/model.yaml regardless of route. Even as a consistency remark,
>   anchoring makes the operating point a function of the layer the firewall
>   exists to hide. Published benchmarks only. Recorded rather than scrubbed —
>   an honest record of a near-miss is worth more than a clean-looking config.
>   FIREWALL HOLE EXPOSED, nobody's fault, FOR PHASE 5: docs/assumptions.md and
>   tasks.md republish generator-realized values (denial rate, appeal rate,
>   overturn rate, rework cost) to an agent that is firewalled from the generator.
>   The firewall is enforced on SOURCE FILES and leaks through DOCUMENTATION. Any
>   §4.5 discipline that assumes ml-engineer cannot see realized generator output
>   is currently false by construction.
> RULING 2 — PAIRED CI ON THE DOLLARS-AT-RISK DIFFERENCE: NOT SATISFIED, and the
> conclusion everyone has been carrying is WRONG. train.py:440 still reports three
> SEPARATE unpaired intervals plus a note inviting the reader to compare their
> widths — the invalid comparison the ruling was written to prevent.
>   qa-reviewer-p10 MEASURED the paired difference the ruling asked for, on the
>   current store, reusing the shipped pipeline:
>     champion − base_rate    +0.1793   95% CI [+0.036, +0.532]   P(diff<=0) ~0.01
>     champion − payer_rule   +0.2962   95% CI [+0.071, +0.534]
>   STABLE across five bootstrap seeds (1337/7/42/20260727/99991); zero excluded
>   every time. So the metric DOES support a business claim and the current
>   framing UNDERSTATES the model. The team-lead ruling anticipated "if it spans
>   zero, say so" — it does not span zero. What must NOT be said is "38.4% vs
>   20.4%"; the honest claim is "captures ~18pp more denied dollars than arbitrary
>   ranking, magnitude poorly determined", because the ten largest denied claims
>   hold 50.9% of denied dollars and that is what makes the interval so wide.
>   `paired_bootstrap_difference` already exists in evaluate.py and is used for
>   ROC-AUC; it takes metric_fn(y, score), so dollar capture needs a closure
>   binding the amounts and resampling them with the rows.
> MODEL C — EARLY BOUNDARY PROBE (WIP 34a5b1c; NOT a review, Model C is unrun).
> The §5 boundary is RIGHT where it matters most: sim_denial_driver_mechanism is
> forbidden for BOTH models, appears nowhere in src/ except appeal.py's prose, and
> is not in DENIAL_QUERY; the permitted remittance neighbours are permitted; the
> guard is invoked in both appeal.py and model_c.py. Two findings:
>   (a) APPEAL_TARGET_QUERY SELECTS sim_appeal_disputed_amount — forbidden for
>       Model C — and appeal.py:265 drops it inline before the merge. Nothing in
>       the code uses it. That is a read-then-drop of a forbidden column, the
>       pattern the clm_utlztn_day_cnt ruling rejected ("never read rather than
>       read and then dropped"). Stop selecting it.
>   (b) the equality the ENR recoverable amount rests on — disputed == denied —
>       was checked once by hand and by nothing else. qa VERIFIED it on live PG
>       (967 level-1 appeals, 0 mismatched, so the substitution is SOUND) and
>       pinned it in tests/leakage/test_model_c_boundary.py so a future load
>       cannot silently invalidate it. Same defect class as covered days.
> TEST OWNERSHIP — TEAM-LEAD RULING 2026-07-27, following the Phase 1 precedent
> (data-engineer wrote tests/contracts/). tests/leakage/ is QA'S: it is the guard,
> and a guard authored by the party it constrains is not a guard. tests/models/
> and tests/features/ are ml-engineer's for their own modules. qa owns tests/
> overall and may amend anywhere.
>   DISPOSITION of cd3e30c's tests/leakage/test_persisted_matrix.py (qa's call per
>   the ruling): ADOPTED by qa, kept in tests/leakage/ rather than moved to
>   tests/features/. Its subject is the discovery contract THIS directory
>   publishes, not the behaviour of src/features/store.py; moving it would put a
>   guard on qa's contract inside the constrained party's directory, which is the
>   same inversion the ruling rejects. It is well built and qa would have written
>   substantially the same file.
>   ADOPTING IT MEANT REVIEWING IT, AND THAT FOUND A DEFECT. The staleness check
>   called `store.build_training_matrix(refresh=True)`, and that function
>   PERSISTS what it builds — so the guard rewrote the committed artifact as a
>   side effect of checking it. Two consequences: `uv run pytest` left git status
>   DIRTY on any machine with a warehouse (qa hit exactly that churn earlier in
>   the session and reverted it by hand before it could be committed), and worse,
>   the guard REPAIRED THE CONDITION IT EXISTED TO DETECT — a genuinely stale file
>   fails the first run and passes every run after, because the first run
>   overwrote it. From outside, "was never stale" and "was stale and got quietly
>   rewritten" are indistinguishable, which is the warehouse-restore failure mode
>   in miniature. FIXED on adoption: the check now builds through the same path
>   without persisting, and asserts the artifact's sha256 is unchanged across
>   itself. VERIFIED — digest 479ea5b57d605acc before and after, 11 passed, git
>   status clean on artifacts/.
>   qa's own gate tests in tests/models/ (cost matrix, dollars at risk) carry a
>   header saying they are qa-authored review gates: ml owns that directory, and a
>   red gate the constrained party can edit needs the boundary written down.
>   A SECOND WRITER REMAINS, and it is ml-engineer's to fix (src/ is not qa's).
>   Fixing the staleness guard removed one writer; `src/models/train.py:258` calls
>   `persist_training_matrix` on every training run, and tests/models/
>   test_train_postgres.py trains against live PG as part of the suite. So a full
>   `uv run pytest` on any machine with a warehouse REWRITES the committed
>   artifacts/features/model_a_training_matrix.json and leaves the tree dirty.
>   REPRODUCED at will: clean tree -> full suite -> `M artifacts/features/
>   model_a_training_matrix.json`, every time.
>   The content is byte-stable — parquet sha256 479ea5b57d605acc and rows 20,867
>   identical across every rebuild this session, which is a nice independent proof
>   that the feature store is reproducible. The ONLY changing field is the
>   embedded wall-clock `written_at_utc`. A committed file carrying a wall-clock
>   that the test suite rewrites guarantees spurious diffs and invites exactly the
>   accidental commit qa made and reverted twice today.
>   SUGGESTED FIX (ml's call, not a red gate — this is a reproducibility wart, not
>   a leak): drop `written_at_utc` from the committed manifest, or write it only
>   under `make features` rather than on every train. Keep the sha256 and rows;
>   those are what the staleness digest actually needs.
> ANSWER TO p9'S TWO QUESTIONS (qa-reviewer-p10, asked by team-lead; both are
> tests/ and therefore qa's call — implemented and PROVEN, not just decided):
>   Q1 "should a failed restore be loud and distinguishable?" YES, and the real
>   gap was sharper than stated. The snapshot fallback in test_warehouse_restore.py
>   also silently covers a repository whose own `make views` is BROKEN: the runner
>   raises, apply_views.py wraps all 9 views in ONE transaction so the rebuild
>   rolls back to zero, the fallback restores the pre-run definitions, and the
>   suite goes green. Warehouse fine, repository broken, nobody told. The shipped
>   runner's exit code is now asserted separately, so "restore succeeded",
>   "restore rescued a broken build" and "restore never ran" are three outcomes
>   instead of one green tick. The fallback STAYS — it is what keeps the warehouse
>   usable — it just can no longer report success on someone else's behalf.
>   Q2 "should the merge-main precondition be enforced rather than remembered?"
>   YES. tests/integration/conftest.py now has `branch_is_not_stale`, a session
>   autouse fixture defined AHEAD of warehouse_baseline so it fires before the run
>   writes anything. It fails the suite when `main` is not an ancestor of HEAD.
>   PROVEN BOTH WAYS: replayed from a detached worktree at 47df189 (9 commits
>   behind main) with the new conftest, it BLOCKS all 12 destructive integration
>   tests before any of them reaches apply_ddl, naming the branch, the commit
>   count and the fix; on this merged branch tests/integration/test_end_state.py
>   runs 6 passed. Every unknown degrades to ALLOW — no git, no repo, detached
>   HEAD, no local `main` — because a precondition that cannot form an opinion
>   must not block the suite. It compares against LOCAL `main` and deliberately
>   does not fetch: a suite that reaches the network to decide whether to run is a
>   worse problem than the one it solves.
- [ ] Model C: appeal success + Expected Net Recovery work-queue score
- [ ] Slice metrics, bootstrap CIs, model card
> CRASH + RE-SPAWN #3 2026-07-27 ~23:40Z (team-lead): qa-reviewer-p10 and
> ml-engineer-3 hit the cap together, ~4.5h after the previous pair — the third
> simultaneous double-crash in one day. BOTH WORKTREES WERE CLEAN, everything
> committed (ml through c565ea3, qa through 69adf49). Nothing to preserve, which
> is the first time that has been true and is exactly the discipline the crash
> notes ask for. Re-spawned 2026-07-28 as ml-engineer-4 and qa-reviewer-p11.
> Warehouse verified healthy at re-spawn: 9 views, 20,867 claims, drg_desc 167,
> 0 orphans, 0 unprefixed crosswalk columns, reconciliation 21/21.
> ml-engineer-3 declared PHASE 4 ML WORK COMPLETE before crashing (290 passed / 17
> skipped, ruff clean, DB read-only throughout). Not merged; awaiting acceptance.
> TEAM-LEAD RULINGS ON ml-engineer-3's TWO OPEN QUESTIONS:
> 1. NO SHAP FOR MODEL C — UPHELD, with a condition. Their argument is right: a
>    model whose paired interval cannot separate it from a category rule
>    (xgboost − category_rule −0.0356 [−0.1325, +0.0597]) has nothing stable to
>    attribute, and a waterfall over it would read as an explanation of a decision
>    the data does not support. §7's ML bar is baseline-vs-advanced REPORTED,
>    calibration, leakage, slices — SHAP is a §2 stack decision and it IS delivered
>    for Model A. CONDITION: the model card must state explicitly WHY Model C has
>    no SHAP, in terms of the measured non-separation, so a reader cannot mistake a
>    deliberate omission for an oversight. That converts an absence into a finding.
> 2. COMMITTED TRAINING MATRIX — APPROVED, reaffirmed. Keep it in git; do not
>    switch to RCM_FEATURE_MATRIX + a CI build step. A guard that only runs where a
>    warehouse is loaded has the shape of the defect this repo already shipped.
> STILL OPEN AND NOT SATISFIED — §3.3, verified by team-lead on the ml branch
> 2026-07-28: docs/provenance_register.md and docs/data_dictionary.md still have
> ZERO mentions of artifacts/features/model_a_training_matrix.parquet. It has been
> committed since cd3e30c. This is the ONLY data file a reader can open from a
> clean clone with no database, so it is the most likely artifact an outside reader
> inspects and the worst one to leave unclassified. Register it — what it is,
> `make features` as the regeneration path, grain (one row per claim, 20,867),
> per-column provenance, and an explicit statement that sim_-prefixed columns are
> SIMULATED. BLOCKS Phase 4 acceptance.
> DETERMINISM (ml-engineer-3 flagged, unexplained): one Model A run in six diverged
> (ROC diff +0.0026 vs +0.0003, ECE 0.02056 vs 0.01964), not reproducible; two
> consecutive full runs are byte-identical and estimator scores hash identically
> across processes. tests/models/test_determinism.py added. TEAM-LEAD NOTE: their
> own first suspect is the right one — `estimators.xgboost.n_jobs: 4`. Multi-thread
> XGBoost sums gradient histograms in a thread-scheduling-dependent ORDER, and
> floating-point addition is not associative, so bitwise-identical inputs can give
> slightly different splits run to run. That is a known property, not a bug, and it
> would produce exactly this signature: rare, tiny, unreproducible on demand.
> Test at n_jobs=1 to confirm; if it holds, document it in the model card rather
> than chasing it, and note that the CHAMPION is logistic so no headline figure
> depends on it.
- [x] BLOCKER — training-matrix guard wired (ml-engineer-3, cd3e30c). PENDING qa.
  qa's test_guard_is_wired_once_a_feature_store_exists was RED and the red test was
  the smaller half of the problem: src/features/ satisfied NO discovery route, so
  the §4.1 VALUE probes — the only kind that catch a renamed / logged / binned /
  ratioed forbidden column — SKIPPED, and a skip reads like a pass.
  All three routes now served: artifacts/features/model_a_training_matrix.parquet
  (COMMITTED, .gitignore exception, 1.4 MB / 20,867 x 44), a no-required-argument
  src.features.build_training_matrix() re-exported on the package, and
  RCM_FEATURE_MATRIX already honoured. `make train` persists the matrix it is about
  to fit on, so the guard checks the object the model saw, not a copy.
  COMMITTED ON PURPOSE: regenerating on demand makes the guard live only on a
  machine with a loaded warehouse — the same shape as the defect this project
  already hit, a green suite over a degraded DB. Team-lead notified it is a
  judgement call and offered the RCM_FEATURE_MATRIX alternative.
  ONLY MODEL A GOES THERE and a test enforces it: qa's forbidden_columns fixture is
  MODEL A's set, so a Model C matrix beside it would fail the guard correctly and
  for entirely the wrong reason (C is permitted to see the denial) and the fix
  someone would reach for is loosening the guard. Model C writes to
  models_artifacts/model_c/, which the guard does not scan.
  Staleness by measurement: sidecar manifest with rows, feature list, split
  boundary, parquet sha256 and a digest of every forbidden_* block in
  config/model.yaml, so a widened blacklist with no rebuild fails a unit test
  instead of leaving the guard checking an older column set.
  VERIFIED by checking qa's tests/leakage out over this tree, running it, then
  restoring: guard 5 passed (was 1 failed + 4 skipped); test_live_leakage.py 8
  passed on live PG across BOTH routes, strongest single-feature AUC 0.5859
  (sim_payer_id) vs the 0.6778 oracle — reproduces qa-reviewer-p9's hand-
  materialised figures exactly. Also adds `make features` and `make train-appeal`.
- [x] Model C: appeal success + Expected Net Recovery work queue (ml-engineer-3,
  1f4375c). PENDING qa. The preserved WIP HAD run — models_artifacts/model_c/ held
  14:37 artifacts team-lead could not see because that directory is gitignored.
  Reproduced, then fixed three defects:
  (1) THE QUEUE COMPARISON MEASURED TWO DIFFERENT OBJECTS — the table ranked by the
      TIERED queue while the paired bootstrap beside it ranked by the raw ENR score,
      so a -2.2pt gap sat next to an interval centred on 2.2e-16. Both correct about
      different rankings; nothing said so. Every rule is now a score vector over the
      same claims (work_queue.priority_score() re-expresses the tiered queue as
      -queue_position), so table and interval are one computation by construction.
  (2) THE DEADLINE OVERRIDE FIRED IN NO REPORTED ARTIFACT. Property of the two
      conventions, not of the rule: at-arrival triage gives every claim the full
      window (backtest 0 DEADLINE_CRITICAL) and the live snapshot is one instant on
      a thin 2023-24 tail (1 open claim, 467 out of window). Added rolling
      month-start queues: 22 snapshots, 237 distinct claims reach the urgent tier,
      guarantee re-asserted on every snapshot. Degenerate snapshot REPORTED with its
      caveat, not dropped.
  (3) THE APPEAL-SIDE EMBARGO WAS MODEL A's 60 days off the DENIAL posting, but an
      appeal outcome is not known then. appeal_embargo_days: 180 from the published
      Medicare Part A/B timetable (120 to file + 60 for the decision), and
      deliberately NOT checked against sim_appeal_decision_date.
  RESULTS. 2,663 denials / 967 appealed / test 193 with 86 overturned. Champion
  xgboost ROC 0.5611; xgboost - category_rule -0.0356 [-0.1325, +0.0597] — no
  difference from a rule needing no model. Queue at 10% capacity: largest-denial-
  first 65.7%, enr_score 61.0%, tiered queue 59.8%, random 0.7%; enr_score minus
  largest-first -4.7% [-16.7%, +0.9%]. THE PROBABILITY DOES NOT EARN ITS PLACE —
  P is nearly flat so P x amount is dominated by amount. ENR ships for the CUTOFF
  and the tiering, not the ordering, and the card says exactly that.
  No SHAP published for Model C, deliberately: a model a paired interval cannot
  separate from a category rule has nothing stable to attribute, and the analyst-
  facing explanation is the tier + recommended_action, which are rule-based.
- [x] COST MATRIX DECOMPOSED per the ruling (ml-engineer-3, 1f4375c).
  p_prevented_given_flagged_and_worked 0.50 (Change Healthcare Denials Index: ~86%
  potentially avoidable as a ceiling, ~half front-end in origin — the optimistic
  end, it equates "front-end cause" with "caught and fixed in time") x
  share_of_claim_value_permanently_lost 0.25 (same index: ~24% of avoidable denials
  not recoverable; a FLOOR, since MGMA/CH put 50-65% of denials as never reworked
  at all) = 0.125, multiplied in exactly one place. Neither derived from the
  generator; both fixed BEFORE the threshold was computed.
  WHAT FELL OUT, as instructed: break-even multiplier MEASURED at 0.0632 on the
  calibration fold (0.0543 test, 0.0785 whole book). 0.125 is above it by ~2x where
  1.0 was above by 16x. Flagged share 98.4% -> 60.1% at 73.2% recall. Still not an
  operating point, and the sweep is why: 13.6% -> 59.9% -> 86.3% flagged as the
  multiplier moves 0.075 -> 0.125 -> 0.25. Capacity-constrained stays PRIMARY.
  I corrected my own pre-written prose mid-run — I had written "the decomposition
  did not fix it" and the measured numbers contradicted it.
  VERIFIED the predecessor's appeal_economics block rather than trusting it: $45
  Premier-anchored, and the $29.88 note is a consistency remark, not load-bearing.
- [x] DOLLARS-AT-RISK INSTRUMENT FIXED, and it CHANGES THE CONCLUSION
  (ml-engineer-3, 1f4375c). Paired bootstrap on the DIFFERENCE over the same
  resamples: champion - constant +17.9% [+4.2%, +51.7%]; champion - payer rule
  +29.6% [+7.1%, +53.4%]. Both EXCLUDE zero, so the champion IS distinguishable
  from arbitrary ranking — which the unpaired reading (38.4% [16.0, 59.3] vs 20.4%)
  could not support and this can. Width still enormous; magnitude not pinned down;
  ten largest denied claims hold 50.9% of denied dollars. Card forbids quoting a
  single number from that row.
  Also worth the reviewer's attention: the payer rule captures 8.7%, BELOW the
  20.4% a constant score gets — it concentrates on a payer whose denials are small.
  Ranking by count and ranking by dollars are different problems.
- [x] Slice metrics + bootstrap CIs (ml-engineer-3, 1f4375c). Every scorable slice
  AUC carries a CI and an explicit beats_chance column. Model A: MCR_FFS 0.5507
  [0.4968, 0.6031] COVERS 0.5 and is the largest slice; service line only
  AFTERCARE_REHAB and MSK_SKIN_ENDO clear chance, reproducing the Phase 2 warning
  independently; value bands all clear; 1,801 providers / 1 scorable. Model C:
  EVERY slice interval covers 0.5, 157 providers / 0 scorable.
- [x] docs/model_card.md written (ml-engineer-3). Carries all six required items:
  the SOURCE-exclusion honesty statement (corr 0.0477 / 0.0117 / 0.0008, clm_pmt_amt
  identical to billed_charge_amt, so NOT empirically load-bearing — load-bearing on
  the pipeline being right AS IF the data were real, and it does NOT imply a live
  leak was caught), the covered-days classification, the Model C boundary with both
  upheld calls, the cost-matrix decomposition, the dollars-at-risk caveat, and the
  base-tables architecture decision. Every figure copied from metrics.json.
  I re-verified the inherited claims rather than repeating them: top 10% of
  providers hold 52.94% of claims, 72.5% of claims (83.0% post-2019) have provider
  history, recovery ratio 0.811 sd 0.116, by category 0.740-0.834, corr with log
  denied 0.024, disputed == denied on 967/967 with max abs diff 0.00.
- [x] DETERMINISM GUARD (ml-engineer-3). One Model A run diverged from five others
  this session (ROC diff +0.0026 vs +0.0003, ECE 0.02056 vs 0.01964). NOT
  reproducible: two consecutive full 1,000-resample runs are byte-identical and
  estimator scores hash identically across processes. Cause unknown, so
  tests/models/test_determinism.py stands guard rather than leaving it as a note.
  First place to look if it recurs is estimators.xgboost.n_jobs: 4.
- [x] §3.3 BLOCKER CLEARED — committed training matrix REGISTERED (ml-engineer-4).
  docs/provenance_register.md gains "The committed Model A training matrix" and
  docs/data_dictionary.md gains the matching section: what it is, `make features`
  as the regeneration path, grain (one row per claim, 20,867 x 44), the split, and
  per-column provenance for all 44 columns with an explicit statement that every
  sim_-prefixed column is SIMULATED.
  COUNTS VERIFIED AGAINST THE FILE, NOT BY HAND — and my hand count was wrong
  twice: SIMULATED 34 (32 features + sim_denial_flag + sim_submission_date, not
  33), SOURCE 4, DERIVED 6, sum 44, zero unaccounted. The register now states the
  arithmetic so a reader can check it.
- [x] §3.2 VIOLATION FOUND WHILE REGISTERING, AND FIXED (ml-engineer-4).
  `overall_prior_denial_rate` shipped in the committed matrix UNPREFIXED since
  cd3e30c. It is computed entirely from sim_denial_flag — an aggregate of a
  fabricated denial — so §3.2 makes it a simulated column. In the ONE data file a
  reader can open from a clean clone with no database, a column called
  "overall_prior_denial_rate" reads as a real Medicare book rate. Nothing failed,
  because nothing was checking. Renamed to `sim_overall_prior_denial_rate`, and
  its Model C counterpart to `sim_overall_prior_overturn_rate`.
  NO NUMBER MOVED: every Model A and Model C figure in the card reproduces exactly
  (logistic ROC 0.6254 / PR 0.2210, xgboost - logistic +0.0003 [-0.0173, +0.0183],
  ECE 0.01964 -> 0.01753, dollars +17.9% [+4.2%, +51.7%]; C xgboost 0.5611/0.4914,
  category_rule 0.5571/0.4793, enr - largest -4.7% [-16.7%, +0.9%]). Metrics JSONs
  diff to nothing but the rename. SHAP top drivers unchanged, no feature UNMAPPED.
  GUARDED so the next one fails instead of shipping: tests/features/
  test_matrix_provenance.py checks the DECLARATION, not the column name — a
  feature whose declared lineage touches a sim_ source must carry the prefix.
  NEGATIVE CONTROL RUN: restoring the old name makes it fail with the offending
  lineage named, then passes again on restore. A guard that cannot fail is not one.
- [x] WALL CLOCK DROPPED FROM THE COMMITTED MANIFEST (ml-engineer-4, per ruling).
  `written_at_utc` removed outright rather than gated behind `make features`:
  gating it would mean train.py REMOVING the field on every run, which is also a
  diff. Every remaining field is a function of content or config, so all writers
  emit identical bytes. `make features` prints the build time to stdout instead;
  the build time of record is the git commit date.
  MEASURED: two consecutive `make features` and two `make train` runs all leave
  manifest sha256 d5c1ca88aa0786f7d39c7ac055b13c264654ea1a84ef9e75412c5d2fed454e8c
  and parquet d11bd0df5a918d0debef9858e1dcc6c05de392f7dddb322741576a2ae73d42d8.
  Guarded without a database by writing the artifact twice to tmp_path and
  comparing bytes — the property stated directly, rather than grepping keys for
  words that look like timestamps (my first attempt did that and false-positived
  on `time_column`, which is a column NAME).
- [x] DETERMINISM: THE n_jobs HYPOTHESIS IS REJECTED (ml-engineer-4). Tested as
  instructed; it does not hold. Fitting the same xgboost pipeline on the same fit
  fold and hashing raw test-fold scores gives ONE distinct hash (607f1d7abd126139)
  across 38 fits — 20 at n_jobs=4 and 20 at n_jobs=1 in-process, plus 3 fits in
  each of six SEPARATE processes at OMP_NUM_THREADS 1/2/3/4/8/16. xgboost `hist`
  is bitwise deterministic on this data at every thread count, so thread
  scheduling cannot be the mechanism.
  TWO INDEPENDENT ARGUMENTS AGAINST IT: the figure that moved was ece_uncalibrated,
  which belongs to the CHAMPION — and the champion is LOGISTIC, so a defect
  confined to the tree model could not move it. A champion flip would explain both
  numbers at once, but the calibration-fold margin is PR-AUC 0.2769 vs 0.2576, far
  too wide for a floating-point perturbation to cross.
  Cause therefore remains UNKNOWN and the card says so, in §5.1, rather than
  attributing it to a plausible mechanism the measurement rejects. Consequences
  bounded: champion is logistic, so no headline figure depends on the xgboost path.
  ONE REAL ORDER-INSTABILITY WAS FOUND AND FIXED en route: the SHAP global
  importance table sorted with pandas' default NON-stable quicksort, so features
  xgboost never split on (all tied at exactly 0.0) could reorder for reasons
  unrelated to the model — observed live, two zero rows swapping after an
  unrelated rename. Both sorts now pass kind="stable". Never the divergence above
  (importances feed no metric), but it was making the artifact's diffs noisy.
- [x] MODEL C SHAP ABSENCE now states the MEASURED non-separation (ml-engineer-4,
  team-lead condition). The card previously gave the reason in words ten lines
  below the number; it now restates -0.0356 [-0.1325, +0.0597] inline, says the
  interval spans zero, says SHAP IS delivered for Model A so the contrast is
  visible, and names the condition under which Model C SHAP becomes appropriate.
  A forward pointer was added to §1's Explanations section so a reader who goes
  looking for Model C's SHAP finds the reason rather than a gap.
> WARNING FOR qa-reviewer-p11 AND team-lead — CONCURRENT WAREHOUSE RELOAD SEEN
> (ml-engineer-4, 2026-07-28 ~14:20Z). I queried the warehouse mid-run and found
> sim_workflow_events at 0 rows (should be 131,077); it was back to 131,077 three
> minutes later. Almost certainly qa's integration suite calling apply_ddl, which
> drops and recreates. Two consequences worth knowing:
> (1) MY MISTAKE, RECORDED: I ran `pytest tests/models/test_train_postgres.py
>     tests/features/test_feature_store_postgres.py` by FILE PATH, which bypasses
>     the `-m "not integration"` filter I had been using correctly everywhere
>     else. Naming an integration file directly runs it. I will not do that again.
> (2) THE REAL FINDING: that run PERSISTED A DEGRADED MATRIX OVER THE COMMITTED
>     ARTIFACT — manifest sha moved to 1e3a00f7... with diagnosis_count all-null,
>     because the warehouse was mid-reload. It failed loudly afterwards
>     (cumsum on object dtype), but the WRITE had already happened. So the
>     committed artifact is rewritable by any training run against a transiently
>     degraded warehouse, and nothing in the write path checks that what it is
>     about to persist is sane. I caught it only because I hashed before and after.
>     Restored from git, rebuilt on a healthy warehouse, and both shas came back
>     bit-identical to the clean build — but a content sanity check before persist
>     (or a refusal to overwrite when row counts/null rates move) belongs on the
>     Phase 5 list. Flagging, not fixing: the write path is mine but the guard's
>     shape is qa's call.
> WAREHOUSE WRITE WINDOW (qa-reviewer-p11, 2026-07-28): OPENED then CLOSED for the
> Phase 4 acceptance suite run. main merged into feat/phase4-qa FIRST (113b0dd) per
> the stale-branch rule, so p10's `branch_is_not_stale` guard permitted the
> destructive integration tests. Pre-window: 20,867 claims, 20,867 adjudications,
> 9 vw_ views, drg_desc 167, 998 appeals. `make reference-codes && make views` run
> afterwards per the standing rule. POST-WINDOW, IDENTICAL TO PRE: 20,867 / 20,867
> / 998 / 9 views / drg_desc 167 / 0 claim_sk orphans / 0 unprefixed crosswalk
> columns / reconciliation 21/21 PASS. `make train` and `make train-appeal` were
> READ-ONLY against PG.
> FINDING 4 RECONCILED — BOTH SIDES WERE MEASURING, AND OF DIFFERENT COMMITS
> (qa-reviewer-p11, 2026-07-28, after team-lead challenged it and asked for
> re-verification or withdrawal). NOT withdrawn, and not upheld as written either.
> qa reported 4 unprefixed sim-derived features against **c565ea3**, the tree under
> review. team-lead could not reproduce it and measured `sim_overall_prior_denial_
> rate` already present. Both are correct. **ml-engineer-4's fix landed in 0a7960a
> BETWEEN the report and the challenge.** The proof is in the line numbers
> team-lead cited: historical.py:210 and appeal.py:155 WITH the prefix are
> 0a7960a's lines; at c565ea3 the same file reads `historical.py:202
> out["overall_prior_denial_rate"] = global_rate`, unprefixed. Verified with
> `git show c565ea3:src/features/historical.py | grep -n overall_prior` against the
> same command on 0a7960a. ml-engineer-4's own board entry — "§3.2 VIOLATION FOUND
> WHILE REGISTERING, AND FIXED ... shipped in the committed matrix UNPREFIXED since
> cd3e30c ... Nothing failed, because nothing was checking" — is a third
> independent confirmation that the finding was real on the reviewed tree.
> DISPOSITION: 3 of the 4 names were real and are FIXED. The 4th,
> `log_sim_denied_amount`, is sim_ INFIXED rather than prefixed; team-lead ruled it
> a naming preference at most and **qa agrees on the merits** — the marker is
> visible, the base column is legible, and no reader takes it for a real Medicare
> quantity. The gate now fires on ABSENCE of the marker rather than its position,
> which aims it at the property §3.2 protects instead of at a spelling. GREEN.
> ON THE FALSE-POSITIVE PATTERN team-lead named (p9's fixture, p10's §4.5 route,
> qa-p11's dollars-at-risk gate): the lesson is accepted and it is the right one.
> This instance is NOT a fourth member of that class — it was a true positive on
> the reviewed commit that a concurrent fix overtook. But the reviewing failure it
> DOES expose is real and worth the same weight: **qa reported against a moving
> branch tip without pinning the commit in the report**, which cost team-lead a
> verification cycle and nearly cost a genuine finding its credibility. Every
> future qa finding names the exact SHA it was measured on.
- [ ] ACCEPTANCE (qa-reviewer-p11): **BLOCKED — 4 findings open, 5 red tests.**
  Reviewed feat/phase4-ml @ c565ea3 (fa8ff87), re-verified @ 0a7960a.
  **Findings 3 and 4 CLEARED by ml-engineer-4 (0a7960a) and re-run by qa, green.**
  Registration gates pass including the content checks (SIMULATED classification
  present, `make features` named). Committed artifact verified sane after ml's
  degraded-persist warning: parquet d11bd0df5a918d0d, manifest d5c1ca88aa0786f7,
  20,867 x 44, zero all-null columns, diagnosis_count 0 nulls, `written_at_utc`
  gone, exactly 7 unprefixed features and every one genuine SOURCE/DERIVED.
  STILL OPEN: findings 1, 2, 5, 6.
> QA RULINGS 2026-07-28, all measured at feat/phase4-ml @ **0a7960a** (SHA pinned
> per the discipline qa-reviewer-p11 committed to after the finding-4 confusion):
> A. `log_sim_denied_amount` — **NON-BLOCKING, and do NOT rename it.** qa's call
>    under §3.2 and it goes further than team-lead's lean. Measured exposure is
>    ZERO: `git ls-files artifacts/` shows only the MODEL A matrix is committed,
>    there is no Model C matrix in git, and Model C has no SHAP plot by ruling, so
>    the name reaches no artifact a reader can open (`grep -rl` over
>    models_artifacts/ finds nothing). It also mirrors `log_billed_charge_amt`, the
>    SOURCE analogue, so `log_<base>` is a consistent internal construction with the
>    marker intact in the base. Renaming would churn code during an acceptance cycle
>    for a property no reader can observe. REVISIT IN PHASE 5 if a Model C matrix is
>    ever committed or a dashboard surfaces the name — that is when exposure starts.
>    The gate already guards the blocking property (absence of a marker), not this.
> B. DEGRADED-PERSIST GUARD — **PHASE 5, not a blocker.** Agreed with team-lead.
>    The committed artifact is verified sane NOW (parquet d11bd0df5a918d0d, zero
>    all-null columns), the failure needs a concurrent warehouse reload, and that
>    hazard is already documented with a standing rule. Adding a new guard at the
>    gate is scope creep. SHAPE, since it is qa's to specify: refuse the write when
>    row count, column set, or per-column null rates deviate from the COMMITTED
>    manifest — compare against git, NOT against the previous run. Comparing to the
>    previous run reproduces the exact defect qa fixed in the staleness guard, where
>    the check repaired the condition it existed to detect and "was never stale" and
>    "was stale and got quietly rewritten" became indistinguishable. It must fail
>    loudly; a skip is what let this through.
> C. `split` COLUMN — **outcome UPHELD, reasoning REJECTED.** ml-engineer-4 justify
>    leaving it unprefixed because qa's guard discovers it by name from
>    {is_train, split, fold} and prefixing would blind the temporal check. That is
>    backwards: a guard must never be the reason a correctness-improving rename
>    cannot happen. The CORRECT reason is that `split` is not an attribute of the
>    claim at all — it is a fold assignment, metadata about OUR experiment, not a
>    statement about the simulated world. §3.2 governs simulated values; a partition
>    label is not one. It stays unprefixed on its own merits.
>    THE UNDERLYING ISSUE IS QA'S AND IS REAL: p8's discovery contract is
>    name-based, and name-based discovery is what made a correct rename look
>    dangerous. Declaration-based discovery is the better shape. PHASE 5 —
>    tests/leakage/ is qa's and this is qa's debt, not ml's.
  Full suite on the merged tree: **355 passed / 9 failed / 3 skipped, ruff clean.**
  Every failure is a qa review gate; nothing unexpected is red.
  THE ML WORK IS STRONG. `make train` and `make train-appeal` both REPRODUCED end
  to end and every figure in the 564-line model card matches the run to the digit
  (Model A logistic ROC 0.6254 / PR 0.2210 / Brier 0.10280; xgboost − logistic
  +0.0003 [−0.0173, +0.0183]; Model C xgboost 0.5611, − category_rule −0.0356
  [−0.1325, +0.0597]; queue 65.7 / 61.0 / 59.8 / 0.7; 22 monthly queues, 237
  distinct deadline-critical claims). The parquet is byte-identical to p10's
  digest 479ea5b57d605acc ACROSS A SESSION BOUNDARY. §4.5 firewall intact — no
  ml module imports src/simulation. metrics.json strict-JSON clean on both models.
  THREE RULINGS RE-RUN — two SATISFIED, one NOT:
    1. COST MATRIX — decomposition SATISFIED. Factors are literal cited constants;
       `prevented_value_multiplier()` computes the product FROM them, so the
       flagged share cannot feed back into the factors. Both hard constraints hold
       structurally. Constraint 1 STILL VIOLATED in the config text — see finding 2.
    2. DOLLARS AT RISK — SATISFIED. `paired_difference` is emitted and reproduces:
       champion − constant +17.9% [+4.2%, +51.7%], − payer rule +29.6% [+7.1%,
       +53.4%]. Card carries the "do not quote a single number" instruction and
       reports the payer rule's 8.7% < 20.4% finding prominently, not buried.
    3. READ-THEN-DROP — **NOT FIXED.** See finding 1.
  TEAM-LEAD CONDITIONS BOTH MET: the card states plainly that Model C's
  probability "does not earn its place" and ships ENR for the cutoff and tiering
  rather than the ordering, without dressing it up and without dismissing the
  cutoff; and it states WHY there is no SHAP for Model C in terms of the measured
  non-separation.
  TWO DEFECTS FOUND IN MY OWN PREDECESSOR GATES by running them rather than
  re-reading them; both fixed in 5b2d158, and fixing them did NOT clear ml:
    - the cost-matrix constraint-1 check was line-based and reported a fragment of
      a DISCLAIMER as an offence. Now sentence-level, requires a NUMBER spoken in
      the generator's voice — and it then found a SECOND real instance the old
      version missed.
    - the dollars-at-risk check asserted interval fields against a dict of
      COMPARISONS and failed on a metrics.json that satisfies the ruling
      completely. That is a false leak report — p9's fixture defect class.
  ONE NON-FINDING, recorded so nobody reconstructs it as one:
  tests/leakage/test_model_c_boundary.py arrived as an add/add conflict. p10 and
  ml-engineer-3 independently wrote files at that path on branches that had not
  met; the file was never on main, so neither could see the other's. NAMING
  COLLISION, NOT an ownership breach. Resolved as a union — ml's parametrized
  boundary sweep is good work and is kept alongside qa's two gate assertions.
  FINDINGS, all with repro commands, in the handoff report to team-lead:
    1. sim_appeal_disputed_amount read-then-drop — STILL PRESENT at ml's tip
    2. $29.88 generator anchor still in config/model.yaml, + a 2nd instance
    3. §3.3 committed training matrix registered nowhere (team-lead's blocker)
    4. §3.2 prefix lost by 4 features derived from sim_ columns
    5. SHAP PNG ships with no synthetic-data banner
    6. 12 artifact CSVs ship with no provenance beside them
  NON-BLOCKING, measured: team-lead's determinism hypothesis is NOT SUPPORTED.
  8 xgboost fits at n_jobs=4 and 8 at n_jobs=1 on the committed matrix give ONE
  score digest, 97391a34056d0c3a, identical across thread counts. Isolates the
  estimator fit only (not calibration or the bootstrap path), but the first
  suspect is cleared and the divergence should be looked for elsewhere.
  ALSO STILL OPEN, non-blocking, previously ruled: `written_at_utc` still churns
  artifacts/features/model_a_training_matrix.json on every `make train`
  (reproduced; only that field changes, the parquet is byte-stable).
> TEAM RULE — PIN THE SHA (team-lead, 2026-07-28, after a §3.2 finding was nearly
> lost to a disagreement neither party could settle). qa-reviewer-p11 filed a
> correct finding measured on c565ea3; ml-engineer-4 fixed it independently while
> registering the matrix; team-lead then "disproved" it by reading the ml
> worktree's WORKING TREE — which already contained the uncommitted fix — and
> reported that as though it described c565ea3. Three parties, all measuring
> correctly, two of different trees.
> RULE: every finding names the commit it was measured on, AND every challenge to
> a finding names the commit it was checked against. Team-lead violated the second
> half, so this binds the coordinator too. On a project where several agents work
> concurrently in separate worktrees against one shared database, an unpinned
> observation is not reproducible even when it is correct.
> WHAT THE FINDING ACTUALLY WAS, since it is the substantive lesson:
> `overall_prior_denial_rate` shipped UNPREFIXED in the committed training matrix
> from cd3e30c to 0a7960a. It is an aggregate of a fabricated denial, and in the
> one data file a reader can open from a clean clone with no database it reads as
> a real Medicare book rate. Nothing failed because nothing was checking. Renamed
> to sim_overall_prior_denial_rate (Model C counterpart sim_overall_prior_overturn_
> rate); NO metric moved. Now guarded on DECLARED LINEAGE rather than column name —
> a name check cannot catch a name defect — with a negative control that restores
> the old name, watches the test fail naming the offending lineage, and passes on
> restore. `log_sim_denied_amount` infixes rather than prefixes and STAYS: qa
> re-aimed that gate to fire on ABSENCE of the sim_ marker rather than its
> position, which targets the property §3.2 protects instead of a spelling.
> TEAM RULE — LABEL, DON'T NUMBER: the team-lead's spawn brief and the reviewer's
> findings both used 1-4 for different work, so ml's "items 1-4 DONE" did not
> answer qa's findings 1-2 and both parties briefly believed the other was
> stalling. Fix lists now use LABELS ([READ-THEN-DROP], [GENERATOR-ANCHOR],
> [SHAP-BANNER], [CSV-PROVENANCE]) and replies quote them back.
> FOR PHASE 5 — [ARTIFACT-REWRITE], found by ml-engineer-4 by accident and worth
> more than the accident: a training run against a TRANSIENTLY DEGRADED warehouse
> silently PERSISTED a bad matrix (diagnosis_count all-null) over the committed
> artifact, and the write landed BEFORE the run failed loudly. The artifact that
> exists specifically so the §4.1 probes run without a warehouse is therefore
> rewritable by the very condition it guards against — the same shape as p10's
> staleness check that repaired what it was detecting, and the restore that rolled
> back to zero views while looking like it never ran. Caught only because they
> hashed before and after. FIX (Phase 5, not blocking Phase 4): a content sanity
> check before persist — refuse to overwrite when row counts or null rates move
> materially. Write path is ml's, guard shape is qa's, contract is shared.
> ALSO RECORDED: running an integration test BY FILE PATH bypasses the
> `-m "not integration"` filter. Not obvious, and it hit the shared warehouse.
- [x] qa-reviewer-p11 FIX LIST 1/2/5/6 CLEARED (ml-engineer-4). 3 and both
  non-blocking items were already fixed in 0a7960a; the list was compiled against
  c565ea3, so 3 (§3.3 registration), the written_at_utc churn and the determinism
  write-up read as open on the board but were not on my branch.
  (1) sim_appeal_disputed_amount NO LONGER READ. It was selected by
      APPEAL_TARGET_QUERY and dropped again after the join, which is not the same
      as never reading it: the drop was one refactor from vanishing while the read
      stayed, leaving a forbidden column on the frame with nothing objecting. Now
      excluded at the QUERY (extract.py), per the clm_utlztn_day_cnt ruling. The
      card's "equal on all 967 level-1 appeals" claim is relabelled a ONE-TIME
      MANUAL CHECK, since the pipeline no longer recomputes it — the same
      correction applied to appeal.py's module docstring, which said "I checked
      before relying on it" and would otherwise have described code that is gone.
      Model C figures unchanged (0.5611/0.4914, category_rule 0.5571/0.4793,
      enr - largest -4.7% [-16.7%, +0.9%]): the column was inert, which is why
      nothing caught it.
  (2) BOTH generator cross-checks out of config/model.yaml. The $29.88 anchor and
      the "965 of 967 filed within 120 days" check are deleted, Premier/MGMA
      citations kept. I did NOT argue the "not load-bearing" line again — the
      ruling pre-rejected it and the second instance shows why: it validated a
      POLICY parameter against the generated layer. Each comment now records what
      was removed and why, so the next reader does not helpfully re-add it.
      Verified the one surviving "generator's realized" mention at :360 is the
      opposite of an anchor — it states that neither cost factor is derived from
      the simulation, and names no generator value.
  (5) SHAP PNG BANNER — fixed at the shape, not the file. `_save_figure` is now
      the ONLY place in the project that calls savefig; both plots route through
      it, so a new plot carries the banner by construction and one that bypasses
      it fails qa's AST gate. x-axis label was being clipped mid-word and is
      restated in the units the axis is in.
  (6) One README.md per artifact directory, WRITTEN BY THE RUN (models_artifacts/
      is gitignored, so a committed note would describe files that may not exist).
      Names slice_payer.csv (§3.5 payer-level analysis) and work_queue*.csv
      (reads as an operational worklist for denials that never happened).
  VERIFIED AGAINST qa's ACTUAL GATES, not against my reading of them: checked
  tests/leakage/test_plot_provenance_banner.py and
  test_artifact_directory_provenance.py out of 2afb693 over this tree, ran them
  (5 passed), then REMOVED them — they are qa's files and are not in my commit.
  NEGATIVE CONTROLS RUN on both: adding a savefig function that bypasses
  _save_figure fails the banner gate; removing model_a/README.md fails the
  directory gate. Both pass again on restore.
  Full suite 264 passed / 12 skipped, ruff clean, warehouse read-only.
> ON HOLD as instructed: qa's finding 4 (§3.2 prefix through feature engineering).
> Not acted on. For the reconciliation — the four names qa flagged exist WITH the
> prefix because I renamed them in 0a7960a after finding the violation myself
> while registering the matrix; team-lead's count of 7 unprefixed features, all
> genuine SOURCE or SOURCE-derived, is measured on that commit and matches mine.
> qa was reading c565ea3, where `overall_prior_denial_rate` really was unprefixed.
> I believe we found the same defect independently, one commit apart.
> `log_sim_denied_amount` (appeal.py:183) is a separate question and I have left
> it alone pending the ruling.
> CRASH + RE-SPAWN #4 2026-07-28 14:45Z (team-lead): qa-reviewer-p11 and
> ml-engineer-4 hit the cap together, the FOURTH simultaneous double-crash in two
> days. Re-spawned as ml-engineer-5 and qa-reviewer-p12.
> PRESERVED: 4ecc4c5 on feat/phase4-ml. ml-engineer-4 was cut off MINUTES after I
> asked them to commit and send qa a SHA, so all four labelled fixes existed only
> in the working tree. Preserved verbatim, ruff clean, but NOT verified — no test
> run, no `make train`, no qa pass. Working-tree state as found:
>   [READ-THEN-DROP]   extract.py:115 states the column is deliberately NOT
>                      selected; appeal.py:277 confirms the inline drop is gone.
>   [GENERATOR-ANCHOR] zero matches for $29.88 or "965 of 967" in config/model.yaml.
>   [SHAP-BANNER]      train.py:180-183 adds the fig.text banner qa specified.
>   [CSV-PROVENANCE]   train.py:232 writes README.md into the artifact directory.
> qa-reviewer-p11 was left holding 5 reds at 1907286, waiting on that SHA.
> LESSON, now explicit: pin-the-SHA only works if the work is COMMITTED. "Report
> done" must mean "committed and named", never "done on my disk". This is the
> third time in two days that uncommitted work caused a coordination problem —
> twice as crash losses, once as a fix qa could not see and reasonably flagged.
> Warehouse verified healthy after the crash: 9 views, 20,867 claims, 131,077
> workflow events, drg_desc 167, 0 orphans, reconciliation 21/21.
> QA RULINGS A/B/C (qa-reviewer-p11 @ 1907286, all accepted by team-lead):
>   A. `log_sim_denied_amount` — NON-BLOCKING, do not rename. Exposure MEASURED at
>      zero: only the Model A matrix is committed, there is no committed Model C
>      matrix, and Model C has no SHAP by team-lead ruling, so the name reaches no
>      artifact a reader can open. It also mirrors `log_billed_charge_amt`, its
>      SOURCE analogue. Revisit in Phase 5 IF a Model C matrix is committed or a
>      dashboard surfaces it — that is when exposure begins.
>   B. Degraded-persist guard — PHASE 5. SHAPE (qa's, and the reasoning is the
>      load-bearing part): refuse the write when row count, column set or
>      per-column null rates deviate from the COMMITTED manifest read from git —
>      NOT from the previous run. Comparing to the previous run reproduces the
>      defect qa already fixed once, where the check repairs the condition it
>      exists to detect and "was never bad" becomes indistinguishable from "was bad
>      and got quietly rewritten". Must fail loudly; a silent skip is what let the
>      original through.
>   C. `split` stays unprefixed — OUTCOME upheld, REASONING replaced, and the
>      correction matters. ml-engineer-4 justified it as "qa's guard discovers it
>      by name, so prefixing would blind the temporal check", and team-lead
>      initially upheld THAT. qa rejected it: a guard must NEVER be the reason a
>      correctness-improving rename cannot happen — that turns the safety net into
>      a constraint on the code it protects. The correct reason does not depend on
>      the tests at all: `split` is a fold assignment, metadata about OUR
>      EXPERIMENT, not an attribute of the claim or a statement about the simulated
>      world. §3.2 governs simulated values; a partition label is not one.
>      Recorded rather than silently swapped, because the wrong reasoning would
>      have licensed the general form "we cannot rename X because a guard looks for
>      it by name", which must always lose. The name-based discovery contract is
>      qa's debt and Phase 5 work.
- [x] ACCEPTANCE (qa-reviewer-p12): **PHASE 4 PASSES §7**, measured on
  feat/phase4-ml @ **4ecc4c5** merged into feat/phase4-qa as **aab1c3a**.
  Two NON-BLOCKING findings below; nothing gates the merge.
  ALL FIVE INHERITED REDS CLEARED, re-verified in one pass:
    [READ-THEN-DROP]   BY MEASUREMENT, not by reading the comment ml-engineer-4
                       wrote. Only mentions surviving in the ML path are comments;
                       APPEAL_TARGET_QUERY selects 3 columns; a live
                       build_model_c_frame is 2,663 x 60 with ZERO columns matching
                       "disputed". The simulation-side mentions are the generator's
                       own and are not ml's to remove.
    [GENERATOR-ANCHOR] $29.88 and "965 of 967" both gone from config/model.yaml.
    [SHAP-BANNER]      Verified IN THE PNG, not merely in the AST gate. Banner
                       renders; the clipped x-label now reads in full.
    [CSV-PROVENANCE]   README.md written into both artifact dirs BY THE RUN; gates
                       green for model_a and model_c.
  A DEFECT IN MY OWN GATE, found by running it rather than trusting it (20a1ca3),
  and it is the same class my three predecessors each hit once. The constraint-1
  check fired on ml-engineer-4's REMOVAL NOTE — "A previous version of this comment
  reconciled $45 against the simulation's own realized cost per denied claim."
  That sentence DELETES the anchor and discloses no generator value; $29.88 is gone
  from the file, and $45 is the config's own appeal_processing_cost_usd. THIRD time
  this check has confused an anchor with a statement ABOUT an anchor (v1 line-based
  caught a disclaimer fragment; v2 sentence-based + any-digit caught this). Every
  version's misfire pushes the reader to delete the honest sentence, against the
  standing "recorded rather than scrubbed" ruling. Now states the property the
  ruling protects — no generator-realized VALUE may be READABLE from this file — via
  a foreign-number discriminator: a number the config itself SETS is not such a
  value, any other number in generator voice is. THREE CONTROLS, because a check
  relaxed to clear a false positive is the one most likely to have gone silent on
  the true ones: both historical anchors replayed VERBATIM must still fire, and the
  removal note must stay quiet. 5 passed.
  §7 ML BAR WALKED ITEM BY ITEM, every figure REPRODUCED from a fresh run:
    - baseline vs advanced REPORTED (not won): logistic ROC 0.6254 / PR 0.2210 /
      Brier 0.10280 champion; xgboost - logistic +0.0003 [-0.0173, +0.0183];
      folds 13,356 / 3,338 / 4,173; base rate 0.1205; ECE 0.01964 -> 0.01753.
    - calibration plot PRODUCED and inspected; carries the banner.
    - leakage tests PASS: 325 passed / 0 failed non-integration, plus 178 passed /
      1 skipped on the read-only live-PG probes. Empirical firewall reproduces —
      strongest single-feature AUC 0.5859 (sim_payer_id) against the 0.6778 oracle
      ceiling, NOTHING at or above it.
    - slice metrics by payer / facility / service line REPORTED for BOTH models
      (A also value_band; C also denial_category), each with CI + beats_chance.
    - model card CURRENT at 658 lines; every headline figure checked against
      metrics.json, not eyeballed.
    - Model C reproduces: xgboost 0.5611/0.4914, category_rule 0.5571/0.4793,
      queue 65.7/61.0/59.8/0.7, 22 monthly queues, 237 deadline-critical claims.
      The [READ-THEN-DROP] fix moved NO figure, confirming the column was inert —
      which is exactly why nothing caught it for so long.
  REPRODUCIBILITY, and the written_at_utc churn is CLOSED: committed artifacts are
  BYTE-IDENTICAL before and after `make train` + `make train-appeal` (parquet
  d11bd0df5a918d0d, manifest d5c1ca88aa0786f7) and `git status` stayed CLEAN.
  Those digests also match p11's and p10's across TWO session boundaries.
  p11's board line calling the churn "still open" contradicted their own line above
  it; measured now, it is fixed.
  §3.2/§3.3 RE-MEASURED ON THE FILE: 20,867 x 44, zero all-null columns,
  diagnosis_count 0 nulls, 34 sim_-prefixed / 10 unprefixed = claim_sk + prvdr_num +
  split + 7 features. All 7 confirmed genuine SOURCE/DERIVED — I traced
  `provider_state_cd` specifically because a crosswalked state WOULD be SIMULATED
  under §3.4, and it is not: it comes from dim_provider via provider_key, is the
  SYNTHETIC provider's own state, and the register already says "never the
  crosswalked real facility's". Registered arithmetic 34+4+6=44 checks out.
  HONESTY PASS CLEAN. §4.5 firewall intact (no ml module imports src/simulation).
  Every "fraud" occurrence in the repo is an explicit negation. Card's opening
  honesty block is prominent and unambiguous. metrics.json carries a `provenance`
  key on both models. models_artifacts/ is GITIGNORED, so artifact exposure begins
  only after a run — and that same run writes the README beside the CSVs.
  Warehouse left HEALTHY: 9 views, 20,867 claims, 20,867 adjudications, 131,077
  workflow events, 998 appeals, drg_desc 167, 0 orphans, reconciliation 21/21 PASS.
  NO DESTRUCTIVE WINDOW WAS OPENED — I confirmed by inspection that every apply_ddl
  caller lives under tests/integration/, so the live-PG leakage probes and the
  reconciliation gate ran read-only. Nothing needed dropping to prove Phase 4.
> TWO NON-BLOCKING FINDINGS (qa-reviewer-p12 @ 4ecc4c5), neither gating:
> [README-STALE] §7 names README in the Docs bar, and README.md has not been
>   touched since the 2026-07-22 scaffold (2b5f90d). It is 11 lines and mentions
>   Model A, Model C, `make train`, and docs/model_card.md exactly zero times — a
>   658-line model card that nothing in the repo's front door points to. NOT
>   blocking, on two grounds: the honesty statement it does carry is correct and
>   prominent, so §1 is not at risk; and Phase 5 already OWNS "README final,
>   screenshots, demo script". Minimal fix if it is wanted before then is two
>   lines — link the model card, name the two make targets.
> [FIREWALL-POPULATION] model_card.md:479 quotes "strongest single-feature AUC
>   0.5859" without naming its population, which is the one thing p10's precision
>   note asked for: the suite's 0.5859 is scored on `shared` (matrix ∩ live truth
>   frame) and p10's 0.5871 on the full 20,867, and BOTH are right. Conclusion is
>   untouched — both sit far below the 0.6778 ceiling — so this is a precision nit,
>   not a correctness one. Add "on the probe population" and it is closed.
- [x] PRESERVED WIP VERIFIED AND EXTENDED (ml-engineer-5, measured on be25ee7 =
  4ecc4c5 merged with main 1df3904). ml-engineer-4's four labelled fixes were
  committed but never run. Ran them against qa's ACTUAL gates (checked
  feat/phase4-qa's tests/ out over this tree, ran, then removed — they are qa's
  files and are not in my commit). THREE of the four held; ONE DID NOT.
  [READ-THEN-DROP]  HELD. sim_appeal_disputed_amount is excluded at
      APPEAL_TARGET_QUERY, the post-join drop is gone, and `make train-appeal`
      reproduces every Model C figure to the digit (xgboost 0.5611/0.4914,
      category_rule 0.5571/0.4793, enr − largest −4.7% [−16.7%, +0.9%], 237
      deadline-critical claims). The column was inert, which is why nothing caught
      it — the fix is about the boundary, not the number.
  [GENERATOR-ANCHOR] FAILED, and the gate is right. The $29.88 anchor was gone,
      but the NOTE RECORDING ITS REMOVAL re-established the reconciliation:
      "reconciled $45 against the simulation's own realized cost per denied
      claim" pairs a number with the generator's voice, which is the consistency
      remark the ruling ordered out — a retraction that repeats the fact it
      retracts is not a retraction. Rewritten to name no figure on either side.
      The failing run IS the negative control: the gate fired on the real defect
      before I touched it.
  [SHAP-BANNER]     PARTIAL. The banner was there and _save_figure is a better
      answer than qa asked for (single savefig writer ⇒ a new plot carries the
      banner by construction). But qa's spec named THREE things and two were
      missing: no title naming the model, and tight_layout instead of
      bbox_inches="tight". Both added. The AST gate only checks the banner, so it
      passed on an incomplete fix — a gate is not a spec. PNG inspected, not just
      asserted: title, full x-axis label, banner all render.
  [CSV-PROVENANCE]  HELD, with a defect I found by reading the output. The note
      was written BEFORE the CSVs, and its call-out list was a fixed block, so
      model_a/README.md warned about `work_queue*.csv` — files that directory does
      not contain. A provenance note that describes files it does not have is the
      first thing a reader stops trusting. Both writers now run LAST and build the
      list from what actually landed. Also removed a stale gitignored
      work_queue.csv (Jul 27, no remaining writer) and corrected model_c's
      description, which promised rolling-monthly queues as CSVs when they exist
      only as a metrics.json summary.
  MODEL CARD: the +44 lines team-lead flagged as unexamined are accurate against
  the code, with ONE EXCEPTION I removed. The card credited qa-reviewer with an
  independent 16-fit determinism probe ("8 at n_jobs=4 and 8 at n_jobs=1, one
  digest"). No committed qa artifact contains it — qa's tests/models/
  test_determinism.py is a different, two-fit check — so under PIN-THE-SHA it is
  an unpinned secondhand measurement in the project's honesty document. Replaced
  with the standing guard that does exist. If qa DID run that probe, restore it
  with a commit to point at; I removed the claim, not the finding.
  EVIDENCE. `make train` and `make train-appeal` end to end on live PG (read-only;
  9 views / 20,867 claims / 131,077 workflow events / drg_desc 167 verified before
  and unchanged after). Every Model A headline reproduces: logistic ROC 0.6254 /
  PR 0.2210 / Brier 0.10280, xgboost − logistic +0.0003 [−0.0173, +0.0183], ECE
  0.01964 → 0.01753, dollars +17.9% [+4.2%, +51.7%]. 40 of 40 metric-table figures
  and every narrative figure I could machine-check reproduce to the digit against
  the freshly written metrics.json. Committed artifacts UNCHANGED across four
  consecutive rebuilds: parquet d11bd0df5a918d0d, manifest d5c1ca88aa0786f7.
  qa's gates 49 passed; full non-integration suite 310 passed / 12 skipped with
  qa's tree overlaid, 264 / 12 on mine alone; ruff clean.
  NEGATIVE CONTROLS, because a gate never shown to fail proves nothing: a savefig
  function bypassing _save_figure fails the banner gate; removing model_a/README.md
  fails the directory gate; both pass on restore.

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
