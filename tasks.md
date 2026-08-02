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
- [x] ACCEPTANCE (qa-reviewer-p11): **BLOCKED at the time — SUPERSEDED 2026-07-29
  by qa-reviewer-p13's §7 PASS at 71cee4b (entry below). Kept as the record of
  what was found and fixed; all 4 findings and 5 reds were cleared.**
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
  [GENERATOR-ANCHOR] HELD. My first call on this was WRONG and is retracted.
      qa's gate went red on the REMOVAL NOTE ("reconciled $45 against the
      simulation's own realized cost per denied claim") and I read that as a
      retraction that repeats what it retracts, so I rewrote the comment to name
      no figure on either side. qa-reviewer-p12 identified it as a defect in
      THEIR OWN gate — third formulation, fixed in 20a1ca3 with a foreign-number
      discriminator — and they are right on the substance: the offence the ruling
      names is a generator-realized VALUE being readable from this file, $29.88
      is gone entirely, and $45 is the config's own appeal_processing_cost_usd,
      so nothing about the generated layer is disclosed. My rewrite ALSO passed
      their new gate, so this was never about passing: it was about which text is
      better, and scrubbing an honest record to satisfy a red test is exactly the
      move team-lead's "recorded rather than scrubbed" preference forbids.
      REVERTED to ml-engineer-4's wording verbatim. Recorded, not quietly undone,
      because the lesson is mine: I treated a red gate as proof of a defect in the
      code it points at. A gate is evidence about the gate as well as the code.
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
  the code. I briefly removed one of them and was WRONG to. The card credits
  qa-reviewer with an independent 16-fit determinism probe; I searched qa's
  tests/ and their test_determinism.py, found a different two-fit check, and cut
  the paragraph as an unpinned secondhand claim. THE PROBE IS REAL AND WAS
  RECORDED ALL ALONG — qa-reviewer-p11's entry on feat/phase4-qa's tasks.md, 8
  fits at n_jobs=4 and 8 at n_jobs=1, one score digest 97391a34056d0c3a. I
  searched the test suite and not the board, which is where this team writes its
  measurements down. RESTORED, now carrying the digest and the source so the next
  reader does not have to re-run the search I got wrong.
  Dropped on restore, deliberately: ml-engineer-4's closing speculation that the
  diverging run was "likeliest" a different tree mid-development. Cause is ruled
  UNKNOWN with both hypotheses rejected; naming a likeliest explanation reopens a
  closed question and contradicts the next paragraph of the same section.
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
> CRASH #5 2026-07-28 23:00Z: qa-reviewer-p12 and ml-engineer-5 hit the cap
> together. BOTH WORKTREES CLEAN — nothing lost. Only qa-reviewer-p13 re-spawned;
> the remaining ml work was two edits to SHARED files, which team-lead made
> directly (71cee4b) rather than spending a sixth spawn cycle on two lines.
> Warehouse healthy: 9 views, 20,867 claims, 131,077 workflow events, 0 orphans,
> reconciliation 21/21.
> RULE EARNED THIS CYCLE — A GATE'S OUTPUT IS EVIDENCE ABOUT THE GATE TOO.
> Both halves were learned expensively:
>   GREEN ≠ DONE. qa's AST banner gate asserts only the banner string, so it went
>   green on a [SHAP-BANNER] fix delivering one of the three items qa specified
>   (no title, wrong bbox). ml-engineer-5's phrasing: "a gate is not a spec."
>   RED ≠ BROKEN. qa's constraint-1 check fired on ml-engineer-4's removal note —
>   the THIRD time that check confused an anchor with a statement ABOUT an anchor.
>   ml-engineer-5 treated the red as proof of a defect and scrubbed an honest
>   record to clear it, then retracted: "I scrubbed an honest record to satisfy a
>   red test." Their own rewrite ALSO passed the fixed gate, which proves it was
>   never about getting green. config/model.yaml is back to ml-engineer-4's
>   wording verbatim; qa's fixed gate (foreign-number discriminator + 3 controls)
>   passes on it. Team-lead had said "keep your rewrite" and was overruled by the
>   better argument.
> PIN-THE-SHA, THIRD AND FOURTH VARIANTS. The rule keeps finding new ways to be
> violated, and none of them look like carelessness:
>   ON-DISK ARTIFACT vs COMMIT — qa-reviewer-p12 opened the SHAP PNG, saw the
>   un-clipped axis label, and credited it to 4ecc4c5, where that function has
>   neither the title nor the tight bbox. models_artifacts/ is GITIGNORED, so it
>   holds the last RUN, not any commit; they were looking at ml-engineer-5's
>   output from fbd4503. An artifact with no SHA in it cannot be pinned by
>   inspection. OPEN QUESTION for Phase 5: stamp the generating git SHA into
>   metrics.json and the artifact READMEs at write time. Write path is ml's.
>   ACCEPTANCE PINNED TO THE WRONG TREE — qa issued a §7 PASS measured on 4ecc4c5,
>   team-lead's PRESERVATION commit, not ml's tip. Caught independently by both
>   team-lead and ml-engineer-5. Had it stood, acceptance would have certified a
>   tree still carrying three artifact defects.
>   SEARCHED THE TESTS, NOT THE BOARD — ml-engineer-5 removed a qa determinism
>   measurement from the model card as unpinned after searching qa's tests/. It was
>   recorded all along in qa-reviewer-p11's tasks.md entry on feat/phase4-qa
>   (digest 97391a34056d0c3a). Restored with digest and source. "This team writes
>   its measurements down on the board" is now explicit.
- [x] Slice metrics, bootstrap CIs, model card — ml-engineer-3/4/5; model card at
  658 lines, every headline figure machine-checked against a freshly written
  metrics.json; slice metrics by payer/facility/service line for BOTH models, each
  with CI and beats_chance. FIREWALL-POPULATION and README-STALE closed 71cee4b.
- [x] ACCEPTANCE (qa-reviewer-p13): **PHASE 4 PASSES §7.** Measured on
  feat/phase4-ml @ **71cee4b** (ml's tip, not a preservation commit), merged into
  feat/phase4-qa as **328b1d5**. The merged tree is byte-identical to 71cee4b for
  every path except `tests/` and `tasks.md` — verified with
  `git diff --stat 71cee4b HEAD -- . ':(exclude)tests' ':(exclude)tasks.md'`, which
  is EMPTY. So these measurements are measurements of 71cee4b.
  ARTIFACTS REGENERATED BEFORE JUDGING, per the trap that caught p12: I moved the
  whole of `models_artifacts/` aside and re-ran `make train` + `make train-appeal`
  from this tree. Nothing in this entry describes a file I inherited on disk.
  THE THREE fbd4503 DEFECTS ARE GENUINELY GONE, each verified in the regenerated
  output rather than in the diff that claims to fix it:
    - SHAP PNG: I OPENED it. Title "Model A denial-risk drivers — logistic,
      forward test fold" renders, the x-axis label reads in full, and the banner
      renders. `_save_figure` uses `bbox_inches="tight"` (train.py:192).
    - `model_a/README.md` no longer warns about `work_queue*.csv`. It cannot: the
      call-out list is built by globbing what actually landed (train.py:248-260)
      and both writers now run LAST. model_a gets the slice_payer call-out only;
      model_c gets both. Every file each README names exists in its directory.
    - model_c's description now says the rolling month-start summary lives in
      metrics.json "for which no CSV is written", which is true.
  §7 ML BAR, item by item, every figure REPRODUCED from the fresh run:
    - baseline vs advanced REPORTED (not won): logistic ROC 0.6254 / PR 0.2210 /
      Brier 0.10280 champion; xgboost − logistic +0.0003 [−0.0173, +0.0183];
      folds 13,356 / 3,338 / 4,173; base rate 0.1205; ECE 0.01964 → 0.01753.
    - calibration plot PRODUCED and inspected; title + banner render.
    - leakage tests PASS: 325 passed / 0 failed / 0 skipped non-integration, plus
      35 passed on the read-only live-PG probes. Firewall reproduces: strongest
      single-feature AUC 0.5859 (`sim_payer_id`) on `shared` against the 0.6778
      oracle ceiling, nothing at or above it.
    - slice metrics by payer / facility / service line REPORTED for BOTH models
      (A also value_band; C also denial_category), each with CI + beats_chance.
    - model card CURRENT at 670 lines; every headline figure present and matching.
  TEAM-LEAD's 71cee4b REVIEWED AS ANYONE'S WORK, and both fixes are sound:
    [FIREWALL-POPULATION] I re-measured BOTH numbers with the project's OWN
      detector rather than trusting either. `single_feature_auc` over the full
      committed matrix (n=20,867) gives **0.5871 sim_payer_id**; the suite gives
      **0.5859** on `shared`. Both reproduce exactly, both sit below 0.6778, and
      the card now quotes neither without its population. NOTE: my first pass used
      naive ordinal encoding and got 0.5602 — the detector cross-fit
      target-encodes categoricals, and the instrument is part of the measurement.
    [README-STALE] Every claim in the new README is true against the run:
      logistic champion, gradient boosting does not beat it, Model C's probability
      does not earn its place. Pointer-only; Phase 5 still owns "README final".
  [GENERATOR-ANCHOR] RETRACTION CONFIRMED. `git diff 4ecc4c5 HEAD -- config/model.yaml`
  is EMPTY, so the file is ml-engineer-4's wording verbatim, and p12's fixed gate
  (foreign-number discriminator + 3 controls) is 5 passed on it. $29.88 and
  "965 of 967" are absent; the surviving mention at :360 names no value and is a
  prohibition, not an anchor. ml-engineer-5 was right to retract.
  DETERMINISM: I RAN THE SKIP. `tests/models/test_determinism.py` skips its
  1,000-resample comparison unless `RCM_SLOW_TESTS=1` — and a skip reads like a
  pass, which is this project's own lesson. With it set: **2 passed**, two full
  runs agree on every reported number. Cause of the historical divergence stays
  UNKNOWN with both hypotheses rejected; the card says so.
  REPRODUCIBILITY MEASURED, not assumed. Committed artifacts byte-identical across
  four `make train` runs (parquet d11bd0df5a918d0d, manifest d5c1ca88aa0786f7,
  matching p10/p11/p12 across four session boundaries) and `git status` stayed
  CLEAN throughout, including after the live-PG probes — the second-writer churn
  is closed. Every artifact CSV and PNG is byte-identical across three consecutive
  runs; the ONLY moving byte in the entire artifact surface is
  `model_a/metrics.json`'s `generated_at_utc`. That is NOT the `written_at_utc`
  defect and must not be "fixed" as one: that ruling turned on a COMMITTED file
  whose churn trains reviewers to ignore its diff. `models_artifacts/` is
  gitignored, there is no diff to ignore, and a run stamp there is useful.
  HONESTY PASS CLEAN. §4.5 firewall intact — no ml module imports src/simulation
  (only two comments name the path). Every "fraud" occurrence in the repo is an
  explicit negation. Both metrics.json carry a `provenance` key. Payer archetypes
  are codes, not insurer names. Warehouse left HEALTHY: 9 views, 20,867 claims,
  20,867 adjudications, 131,077 workflow events, 998 appeals, drg_desc 167, 0
  orphans, 0 unprefixed crosswalk columns, reconciliation 21/21 PASS.
  NO DESTRUCTIVE WINDOW OPENED. I re-verified by grep that every `apply_ddl`
  caller lives under `tests/integration/`, and ran the live-PG probes with
  `-m integration tests/leakage tests/features tests/models` — never by naming
  `tests/integration/` files, which is the filter bypass ml-engineer-4 recorded.
> ONE NON-BLOCKING FINDING (qa-reviewer-p13 @ 71cee4b), and it is the artifacts-
> alone read team-lead asked for:
> [QUEUE-PREFIX] `models_artifacts/model_c/work_queue_backtest.csv` and
>   `work_queue_live_snapshot.csv` ship `recoverable_amt`, which is
>   `sim_denied_amount` VERBATIM with the marker stripped — `work_queue.py:148`
>   defaults `recoverable_column="sim_denied_amount"` and :175 copies it under the
>   new name. These files are one row per CLAIM keyed on claim_sk, which is exactly
>   the shape §3.2 governs, and the inconsistency is INSIDE one file: the simulated
>   CATEGORICAL keeps its marker (`sim_denial_category`) while the simulated DOLLAR
>   AMOUNT loses it, beside a `recommended_action` column reading "Appeal — expected
>   recovery exceeds the cost of working it." Structurally the same defect as
>   `overall_prior_denial_rate`, and stronger: that was an aggregate, this is a
>   rename. `p_overturn` / `expected_recovery_amt` / `expected_net_recovery` /
>   `days_to_deadline` are the same family.
>   WHY IT DOES NOT BLOCK, under the team's OWN settled tests rather than my
>   preference: standing ruling A fixes the exposure test at "committed, or
>   surfaced by a dashboard". `git ls-files` shows no Model C artifact is committed;
>   models_artifacts/ is gitignored; Model C has no SHAP by ruling. And the accepted
>   mitigation is present and specific — the README written beside these CSVs names
>   `work_queue*.csv` by glob and says "The denials being worked never happened."
>   WHY IT IS WORTH RECORDING ANYWAY: ruling A pre-authorised this revisit, and
>   **Phase 5 is the trigger it named.** app-engineer's work-queue dashboard page
>   surfaces exactly these columns, and dashboard headers come from these names. So
>   this is a Phase 5 blocker in waiting, not a Phase 4 defect. Two notes for
>   whoever takes it: ml-engineer-4's declaration-based guard
>   (tests/features/test_matrix_provenance.py) is scoped to the Model A matrix and
>   does not reach the Model C artifact path, so nothing is checking here — the same
>   "nothing failed because nothing was checking" that let the last one run four
>   commits; and the fix is ml's (src/models/work_queue.py), the guard's shape qa's.
> ANSWER TO TEAM-LEAD'S OPEN QUESTION — stamp the generating git SHA into
> metrics.json and the artifact READMEs? **YES on substance, PHASE 5 not Phase 4,
> and the naive version is worse than nothing.**
>   Not Phase 4, on the same reasoning that made 71cee4b legitimate and this not:
>   README.md and model_card.md are §5 SHARED files and those fixes were pointers.
>   The write path is `src/models/`, which is ml's alone, and ml-engineer is not
>   spawned. Landing an unguarded feature during an acceptance pass to close a gap
>   that acceptance has already closed is how this project acquires defects — a
>   gate written under time pressure is the recurring shape here.
>   It is also not load-bearing. The gap it addresses is a REVIEWER-PROCESS gap and
>   the process fix already works: regenerate from the pinned tree before judging
>   the output. I did that this cycle at zero code cost and it caught nothing,
>   because there was nothing left to catch. A stamp makes that cheaper, not
>   possible-versus-impossible.
>   THE DESIGN POINT THAT MATTERS, and why the obvious version must not ship:
>   a BARE SHA IS A LIE ON A DIRTY TREE. p12's actual error was reading artifacts
>   built from ml-engineer-5's uncommitted work and attributing them to 4ecc4c5. A
>   stamp that emitted `4ecc4c5` there would have converted a reviewer's mistake
>   into a machine-attested falsehood — worse, because it would have survived
>   challenge. So the requirement is `git describe --always --dirty` plus an
>   explicit UNCOMMITTED-CHANGES line when the tree is dirty, loud enough that no
>   reader treats a dirty-tree artifact as pinned. Refusing to write is too strong;
>   an unlabelled SHA is too weak.
>   CHEAPER THAN IT LOOKS, in one direction only: `model_a/metrics.json` already
>   carries `generated_at_utc`, so the slot exists — but `model_c/metrics.json`
>   carries no run-stamp field at all, so it is a two-place change plus the two
>   READMEs plus the dirty-tree semantics. Small, not trivial. Phase 5.

## Phase 5 — App + Packaging (lead: app-engineer)
> FIRST BLOCKER GROUP CLOSED by ml-engineer on `feat/phase5-matrix-guard`,
> pending independent QA. The matrix writer now distinguishes a genuine first
> write from an existing artifact whose repository, HEAD, committed manifest, or
> expected path cannot be verified; only the first case is quiet. All comparisons
> remain against `git show HEAD:<manifest>`, never the working-tree sidecar.
> Missing/malformed comparison fields, row/column drift, and material null-rate
> drift refuse before either artifact file is touched. `priority_tier` remains
> forbidden as a feature and exempt as process metadata; its reason now correctly
> states that it is an ntile over `heuristic_priority_score`, not a direct
> computation from `sim_denial_flag`. Focused guard, manifest, matcher, blacklist,
> and QA tier tests: 56 passed / 1 skipped (the live-Postgres staleness rebuild;
> no database configured). Repository-wide ruff check and format check clean.
> CRASH #9 2026-07-30 02:23-03:20Z: app-engineer-3 and qa-reviewer-p18 hit the cap.
> ml-engineer-9 survived (idle). PRESERVED: 0fb7eb2 on feat/phase5-app — 210 lines
> of §3.3 registration for the 8 MB demo bundle across provenance_register and
> data_dictionary, unverified. Re-spawned app-engineer-4 and qa-reviewer-p19.
> QA ROUND 3 (qa-reviewer-p18, 521f93b) — the dashboard RAN for the first time.
> **MEASURED ON A STALE APP TREE: 5a88d6b, my preservation commit, three commits
> behind app's tip 17dd780.** Team-lead verified `banner_extra` is now absent from
> all five pages, so [DASHBOARD-BLANK] is very likely already fixed. Everything
> below must be RE-MEASURED against the current tip before being treated as open.
> This is the SEVENTH time the stale-tree trap has bitten on this project and it is
> now the dominant failure mode of the review loop, not a series of slips.
>   [DASHBOARD-BLANK] all 5 pages called render_page_header(..., banner_extra=...)
>     while components.py:83 takes (title, subtitle); ar_recovery and work_queue
>     raised TypeError on their FIRST rendering statement and produced ZERO blocks.
>     An interrupted refactor — components.py's docstring documents removing the
>     banner and agrees with the qa gate about why; the five call sites were never
>     updated. Zero of five pages rendered the §6 banner. LIKELY STALE.
>   [RENDER-GATE] the answer to team-lead's question about why the banner gate
>     stayed quiet: **a static gate cannot see a page that crashes ABOVE its banner
>     call.** Each page is now RUN and checked on its OUTPUT, with controls on the
>     harness, one subprocess per page (five pages through AppTest in one
>     interpreter segfaults). "The page imports" and "the page renders" are
>     different assertions and only the second is what §6 is about.
>   [DISCLOSURE-FALSE-RED] qa's own gate falsely reported dashboard/ missing the
>     "forbidden as a feature" disclosure — it is present but split across an
>     implicit string concatenation that read_text() could not see. Fixed at the
>     root: Python surfaces now read through `ast`, which merges adjacent literals
>     at parse time; docstrings and comments excluded, three controls.
>   [GUARD-DISARM-3] p17's preserved gate re-measured END TO END and the guard
>     STILL does not fire: parquet 1,469,982 → 1,456,629 bytes, diagnosis_count
>     null 0.0 → 1.0, NO EXCEPTION. The fix belongs in `_repo_root_for`
>     (store.py:226-234), NOT the FileNotFoundError handler at :328 — control flow
>     never reaches it, and the reason string it emits is FALSE. Third pass at the
>     same defect. ml's to close.
>   [TIER-DISPUTED] qa removed their own hardcoded {"priority_tier"} carve-out: the
>     view builds it as an ntile over heuristic_priority_score, so the EXEMPTION is
>     right and config/model.yaml:186's REASON is the inaccurate half. Red until ml
>     rewords. (A reason that is wrong is worse than none — it launders the entry.)
>   [RECONCILE-SILENT-SKIP] the [SKIP-BLIND] shape on a USER-FACING surface:
>     run() drops checks whose datasets are absent and the page prints "All N" over
>     the EVALUATED count — 17 declared, 14 evaluated, 3 vanished, and the user is
>     told all 17 passed. Pinned red.
>   ALSO: `docker compose config` exits 1 on a clean clone (missing .env) and
>     starts postgres only — §7 clean-clone UNMET. The 8 MB bundle was absent from
>     both provenance docs (app-engineer-3 was fixing exactly this when the cap hit;
>     preserved at 0fb7eb2). The bundle ships three genuinely unmarked simulated
>     columns.
>   VERIFIED PASSING: API on the bundle path in all three queue modes, OpenAPI
>     3.1.0 valid, synthetic-id keying held, Model C's negative result presented as
>     one, no fraud framing, no firewall overclaim, ruff clean.
> CRASH #8 2026-07-29 19:07Z: ml-engineer-8, ml-engineer-7, app-engineer-2 and
> qa-reviewer-p17 all hit the cap together. PRESERVED by team-lead:
> **5a88d6b** on feat/phase5-app — the ENTIRE 5-page dashboard plus the demo
> bundle, 3,045 lines and an 8.0 MB rcm_demo.duckdb, ruff clean, verified by NOBODY:
> no `streamlit run`, no test pass, no qa gate. Specifically UNCONFIRMED: banner on
> every page (§6), totals reconciling to view_reconciliation.py, synthetic-id
> keying, and whether the bundle is registered per §3.3. That registration is
> REQUIRED — the bundle is the most exposed artifact in the repo because it ships
> to a hosted demo. Two scratch copies of qa's gates (tests/contracts/zz_tmp_*.py)
> were deliberately NOT committed; tests/ is qa's.
> **5a59f42** on feat/phase5-qa — an 80-line matrix-write-guard gate update.
> Re-spawned as app-engineer-3, ml-engineer-9, qa-reviewer-p18. Warehouse healthy,
> reconciliation 21/21.
> ML WORK LANDED SINCE (feat/phase5-blockers, f18dfc7 → 21fe077):
>   9bcc14e [GUARD-DISARM] FIXED — the write guard no longer stands down when its
>     own baseline is damaged. qa's RED gate (65de5f9) is what forced it.
>   6b17885 [MATCHER-EXPRESSIVENESS] SWEEP — the rule generalised and a SECOND
>     instance found in provenance.py. Instance 1 is WIDER than recorded: f18dfc7
>     refused globs in forbidden_derived_features only, but the hole is in the
>     MATCHER, not one block — tests/leakage/ resolves EVERY configured name with
>     fnmatch (its own vocabulary calls them "patterns") while `_offenders` matches
>     exact-then-substring and expands nothing. A planted `*denied_amount` in
>     forbidden_features is green and empty: it reports coverage for names it never
>     blocks, and every check agrees the list is fine.
>   7c110c0 [FIREWALL-CLAIM] — an honesty defect, and the worse half SHIPS.
>     model_card.md 139-141 justified the cost-matrix factors by asserting the
>     generator's realized overturn and rework rates "sit behind the §4.5
>     firewall". They do not: assumptions.md §8 states the overturn target and §9
>     the realized rework cost, and §4.5 firewalls src/simulation/, not docs/. The
>     SECOND surface is worse — train.py wrote the same false claim into
>     model_a/metrics.json, a MACHINE-READABLE artifact that ships. Found by
>     grepping `firewall` across every ml-owned surface. THE ARGUMENT SURVIVES:
>     both factors are anchored to published benchmarks named in the card and both
>     were fixed BEFORE the threshold was computed — so this is a rewording, not a
>     retraction. qa's ruling stands recorded in assumptions.md §12: **the firewall
>     is a DISCIPLINE, NOT AN INFORMATION BARRIER, and no surface may describe it
>     as one.**
>   21fe077 [DEMO-BUNDLE] registered the .duckdb surface.
> QA ROUND 2 (qa-reviewer-p17): API RUNS — /metrics/executive returns
> claims_submitted=20867 and denied_claims=2663, the control-query figures, no
> 500s. [PASSTHROUGH-BLIND] is the phase's best structural finding: the emitter
> probe perturbs an input and reports columns that MOVE, so it can only see columns
> a surface COMPUTES — and the API read side is entirely pass-through. Registering
> it would have turned the gate GREEN while proving nothing, and the same will hold
> for every dashboard page that renders a view. Replacement gate keys on ml's own
> `forbidden_derived_features`, and found [WIRE-UNMARKED]: 8 fields reach the wire
> unmarked, including `ar_balance_amt` — an unmarked simulated DOLLAR figure on a
> user-facing surface, i.e. [QUEUE-PREFIX] for the THIRD time, one layer further
> out each time.
> TEAM-LEAD NOTE ON THE PATTERN, for the human: three separate renames of the same
> family is a symptom, not three bugs. CLAUDE.md §3.1 defines DERIVED as "computed
> from SOURCE" and has no category for "computed from SIMULATED". That taxonomy gap
> is why these keep surfacing one layer at a time. Amending §3.1 requires human
> approval, so it is flagged rather than done.
> CRASH #7 2026-07-29 13:20-13:48Z (team-lead): ml-engineer-7, qa-reviewer-p16,
> ml-engineer-6 and app-engineer ALL hit the cap within 28 minutes — the largest
> simultaneous loss on this project. ml and qa worktrees were CLEAN. app-engineer's
> was not: 1,816 lines of unreviewed FastAPI + demo read-side preserved as 08d88cc
> (main.py 529, scoring.py 404, schemas.py 350, tables.py 251, provenance.py 106,
> demo/source.py 176), ruff clean, NOT verified. Re-spawned as ml-engineer-8,
> app-engineer-2, qa-reviewer-p17. Warehouse healthy: 9 views, 20,867 claims,
> 0 orphans, 21/21. main == origin/main == aac6cb5.
> TEAM-LEAD CORRECTION — I OVERSTATED THE BLACKLIST COUPLING. I told app-engineer,
> ml-engineer-7 and qa that renaming the vw_work_queue_priority columns without a
> lockstep `forbidden_derived_features` update would make the §4 blacklist
> "silently stop matching". ml-engineer-7 measured it and I verified against
> `src/features/leakage.py::_offenders`: the runtime guard matches EXACT then
> SUBSTRING (`if forbidden in lowered`), so the existing entry `dollars_at_stake`
> ALREADY catches `sim_dollars_at_stake`. A `sim_` PREFIX rename opens no hole in
> either commit order. I inferred the failure mode from the config text instead of
> reading the matcher. The real rule is narrower: **add the marker, never reword** —
> a WORD change (`sim_amount_at_stake`) is blocked by nothing, and THAT is when
> lockstep matters.
> TEAM RULE — MATCHER-EXPRESSIVENESS MISMATCH (from ml-engineer-7's near-miss):
> the test suites resolve blacklist names with `fnmatch`; the RUNTIME guard has no
> glob support. So `*dollars_at_stake` would pass every test and block nothing —
> the Phase 2 placeholder defect returning through the one door the existing checks
> cannot see. **Any protection whose test-time and runtime matchers differ in
> expressiveness is a latent placeholder defect.** A glob is now rejected in code.
> ML FINDINGS THIS ROUND (f18dfc7): `action_type` was guarded by NOTHING while
> vw_work_queue_priority:78-82 builds it as a CASE on `sim_denial_flag` — it
> encodes the label directly, a stronger leak than two columns already listed, and
> invisible because no existing entry was a substring of it. Substring matching
> protects against decoration of KNOWN names, not against unknown ones. Now
> forbidden. `age_days` stays permitted WITH its reasoning recorded in the config
> (a permitted column with no recorded reason is indistinguishable from an
> oversight — how `overall_prior_denial_rate` survived four commits).
> THE SHARPEST OBSERVATION OF THE PHASE, and it belongs in user-facing docs not
> just a config comment: the label-bearing property of vw_work_queue_priority is
> **MEMBERSHIP**, not any column — the where clause selects denied-or-open-AR
> claims. No column-name blacklist can express that. A queue presented as a neutral
> list of claims is presenting a selection that already knows the outcome.
> QA REVIEW ROUND 1 (qa-reviewer-p16, on feat/phase5-qa; team-lead never received
> the report — recovered from the board after the crash):
>   [BLOCKERS-3] PASS at 4a87270.
>   [GUARD-DISARM] NEW, ml's to fix, gate RED and committed. `committed_manifest()`
>     collapses FOUR conditions to None — no git, path outside repo, `git show`
>     failed, and MANIFEST DID NOT PARSE — and `_refuse_or_report` treats None as
>     "nothing to protect". Three of those are; the fourth is not. MEASURED: with a
>     corrupt committed manifest, and separately with the manifest untracked while
>     the parquet is still committed, a matrix with `diagnosis_count` entirely null
>     was written straight over the committed parquet with NO exception — the exact
>     failure bfea020 exists to prevent. The module's own principle turned on
>     itself: `manifest_deviations` already refuses to pass over a missing
>     null_rates block because a check that did not run must not read like one that
>     passed. MUST close before Phase 5 acceptance.
>   [EMITTER-HOLE] found in qa's OWN inherited gate, fixed 723a95c. The
>     [QUEUE-PREFIX] "harder half" was not covering half of Phase 5: probe modules
>     showed a Streamlit page and a `to_dict()` helper caught, but a FastAPI ROUTE
>     NOT — it calls nothing on the emitter list and declares no response_model, so
>     the only evidence it is user-facing is the decorator. The gate was reporting a
>     clean API boundary while blind to it. Added route-decorator detection plus
>     positive AND negative controls ON THE DETECTOR, because green on a tree with
>     no dashboard and no API said nothing about whether the detector could see.
>   [FIREWALL-DOC-HOLE] RULED (d5b8402): NOT fixable by redaction. Recorded as a
>     known limitation in docs/assumptions.md §12, pinned by a test.
>   [APP-R1] app-engineer @ 6e51e61 reviewed, not yet gateable.
> CROSSWALK DISCLOSURE UNDERSTATES THE COLLISION (qa-reviewer-p16, measured):
> grouping by facility NAME collides WORSE than by CCN — worst 15:1 against the
> 8:1 everyone has been quoting, with 2,816 distinct names for 2,857 CCNs. And
> NAME is the key a dashboard is more likely to group on. Every user-facing
> disclosure must carry the NAME figure, not only the CCN figure.
> OPENED 2026-07-29 by human go-ahead after Phase 4 acceptance. main pushed to
> origin at 94ce0d3.
> REPO DIVERGENCE FOUND AT PUSH (team-lead): origin/main carried TWO commits local
> main did not — 8f9dd13 "[docs] expand README with architecture, status, setup,
> and validation" and 1e7898c, both authored by the human on 2026-07-26. The real
> README is 382 lines. Local main still had the 10-line scaffold, which is why
> qa-reviewer-p12's [README-STALE] finding read as true and why team-lead's
> 71cee4b "fix" appended a Models section that DUPLICATED content the human had
> already written. Resolved by keeping the human's README verbatim and dropping
> the appended block; README work returns to Phase 5 where it belongs.
> This is pin-the-SHA at repo scale — the fifth variant: LOCAL main vs ORIGIN
> main. Nobody had fetched since Phase 3. STANDING RULE: `git fetch origin` before
> treating local main as authoritative, and before filing any finding about a
> shared file.
> HUMAN INSTRUCTION 2026-07-29, binding on this phase:
>   1. Resolve [QUEUE-PREFIX] BEFORE any dashboard/API work starts, and re-run the
>      provenance/exposure check that would have caught it so it cannot recur in
>      Phase 5's outputs.
>   2. Resolve every other item already flagged as a Phase 5 blocker before
>      building on top of it.
>   3. Standard scope: FastAPI endpoints, 5-page Streamlit dashboard (banner on
>      EVERY page), DuckDB/Parquet demo extract, clean-clone `docker compose up`,
>      final docs.
>   4. Honesty pass must EXPLICITLY re-verify two things, and they must be visible
>      where a USER sees the data (README, dashboard, model card) — not only in
>      internal docs: the VINTAGE SKEW and the CROSSWALK COLLISION / synthetic-ID
>      keying rule.
> BLOCKER INVENTORY, swept from the whole board by team-lead 2026-07-29. Resolve
> before dashboard/API work:
>   [QUEUE-PREFIX] (ml-engineer) work_queue.py:148 defaults
>     recoverable_column="sim_denied_amount" and :175 copies it out as
>     `recoverable_amt` — the sim_ marker stripped by a rename. Same family:
>     p_overturn, expected_recovery_amt, expected_net_recovery, days_to_deadline.
>     One row per claim keyed on claim_sk, which is exactly the shape §3.2 governs,
>     and the inconsistency is INSIDE one file — sim_denial_category keeps its
>     marker while the dollar amount loses it. Phase 5 IS the trigger ruling A
>     named, because the dashboard work-queue page surfaces these column names as
>     headers. HUMAN DIRECTED: rename to carry the marker (e.g. sim_recoverable_amt).
>   [ARTIFACT-REWRITE] (ml write path, qa guard shape) a training run against a
>     transiently degraded warehouse silently persisted a bad matrix over the
>     committed artifact, write landing BEFORE the loud failure. Shape is settled:
>     compare against the COMMITTED manifest read from git, NEVER the previous run
>     (that reproduces the self-repairing-check defect), and FAIL LOUDLY.
>   [SHA-STAMP] (ml write path) artifacts in gitignored dirs carry no SHA, so they
>     cannot be pinned by inspection — this caused a §7 acceptance to be measured
>     against the wrong tree. Stamp the generating git SHA into metrics.json and
>     the artifact READMEs at write time, including dirty-tree semantics.
>   [SPLIT-DISCOVERY] (qa) p8's discovery contract finds the split column by name
>     from {is_train, split, fold}. Name-based discovery is what made a correct
>     rename look dangerous. Declaration-based is the better shape.
>   [FIREWALL-DOC-HOLE] docs/assumptions.md and tasks.md republish generator-
>     realized values to an agent firewalled from the generator. Any §4.5
>     discipline assuming ml cannot see realized output is false by construction.
>   [LOG-SIM-DENIED] revisit trigger has arrived if a Model C matrix is committed
>     or a dashboard surfaces the column.
>   [README-FINAL] the human's 382-line README still marks Phase 4 as 🚧 and has
>     ZERO mentions of docs/model_card.md. Update status and add the pointer —
>     carefully, on top of the human's text, not over it.
> ML BLOCKERS — STATUS (ml-engineer-7, branch feat/phase5-blockers):
>   [QUEUE-PREFIX] f0a1e12, [ARTIFACT-REWRITE] bfea020, [SHA-STAMP] 4a87270 —
>   committed by ml-engineer-6, with qa-reviewer-p16 gating.
>   [LOG-SIM-DENIED] **RULED: RENAME. `log_sim_denied_amount` ->
>   `sim_log_denied_amount`.** The trigger ruling A named has NOT strictly fired,
>   and that is measured, not assumed: no Model C matrix is committed
>   (`git ls-files` shows artifacts/features/model_a_* only), models_artifacts/ is
>   gitignored, Model C publishes no SHAP, and no dashboard can surface the name
>   because WORK_QUEUE_SCHEMA is closed and checked inside build_work_queue — the
>   column appears in NO artifact, metrics.json field, slice CSV, model-card line
>   or doc. Exposure today is still zero, exactly as p11 measured it.
>   It was renamed anyway, and the reason is not that the name is wrong. qa's
>   re-aim is right: the marker was present, the value reads as generated, and the
>   gate should fire on ABSENCE not position. What decided it is arithmetic on the
>   exception rather than taste: Model A has 39 specs with ZERO infixed names and
>   Model C had 52 with ONE, so this was the last one. Renaming it costs a single
>   feature and converts "a marker somewhere, plus an exception someone must
>   remember" into a rule with no exception list, stated literally at the feature
>   layer by tests/features/test_feature_marker_position.py (STRICTER companion to
>   qa's tests/leakage/test_feature_prefix_survival.py; it does not replace or
>   weaken it, and carries a negative control so it cannot degrade into `"sim_" in
>   name`). Phase 5 adds a dashboard, an API and a demo extract — three new ways a
>   feature name becomes a column header — and an exception is free only while
>   every future author remembers it. Consistency with f0a1e12 is real and points
>   the same way: within one pipeline five queue columns now lead with the marker.
>   NO NUMBER MOVED, verified rather than asserted: `make train-appeal` before and
>   after, and model_c/metrics.json is byte-identical apart from the run-stamp
>   block (dirty-tree warning — SHA-STAMP behaving as designed). xgboost
>   0.5611/0.4914, category_rule 0.5571/0.4793, queue 65.7/61.0/59.8/0.7, 237
>   deadline-critical over 22 monthly queues, folds 619/155/193. Model A is not
>   touched: the column is Model C's alone and the committed matrix is unchanged.
>   FOR app-engineer: `sim_log_denied_amount` is a MODEL INPUT and belongs on no
>   page. If the work-queue page needs a dollars-in-dispute figure, the declared
>   column is `sim_recoverable_amt` (WORK_QUEUE_SCHEMA). Nothing else is blocked.
>   FOR qa: tests/leakage/test_feature_prefix_survival.py is yours and I did not
>   touch it. It still PASSES (the prefixed name contains the marker), but its
>   docstring cites `log_sim_denied_amount` as the live example of the infix case
>   it deliberately permits; that example is now historical.
> [ARTIFACT-REGEN] (ml-engineer-7) models_artifacts/ regenerated on
>   feat/phase5-blockers @ e19836c, clean tree, both metrics.json stamping
>   {"describe": "e19836c", "dirty": false}. Queue CSV headers now carry the
>   markers. Every headline reproduces: Model A logistic ROC 0.6254 / PR 0.2210,
>   xgboost − logistic +0.0003 [−0.0173, +0.0183]; Model C xgboost 0.5611, queue
>   65.7 / 61.0 / 59.8 / 0.7. The feat+phase4-ml worktree's pre-rename copies are
>   correct FOR THAT TREE and were left alone; nothing should be bundled from any
>   worktree — regenerate after the merge so the stamp matches what ships.
> [BLACKLIST-LOCKSTEP] (ml-engineer-7) PARTIALLY LANDED; the rename half waits on
>   app-engineer's final column names, requested and not guessed.
>   MEASURED FIRST, and it changes the risk story: the runtime guard
>   (src/features/leakage.py `_offenders`) matches exact-then-SUBSTRING, so
>   `dollars_at_stake` STILL BLOCKS `sim_dollars_at_stake`. A `sim_` prefix rename
>   does NOT open a hole in either commit order — Model A stays protected through
>   the window. What breaks is a rename that changes a WORD: `sim_amount_at_stake`
>   is blocked by nothing. That is the case lockstep actually exists for.
>   TWO REAL GAPS FOUND WHILE MEASURING, neither caused by the rename:
>     - `action_type` was on NO list and nothing else was a substring of it, so it
>       was blocked by nothing — vw_work_queue_priority:78-82 builds it as a CASE
>       on sim_denial_flag, encoding the LABEL. Now forbidden.
>     - `age_days` deliberately NOT forbidden, with the reason written into the
>       config: it is a monotone function of the permitted point-in-time boundary
>       plus one global constant, and sim_submission_date-derived features are
>       already live and legitimate. What is label-bearing in that view is
>       MEMBERSHIP, which is not a column and no blacklist entry can express.
>   ANSWER TO THE QUESTION TEAM-LEAD PUT TO qa-reviewer-p16 — would the existing
>   config-vs-doc test have CAUGHT this drift? **In CI, NO. On a loaded warehouse,
>   YES.** test_forbidden_config_agreement.py's dead-pattern check is explicitly
>   scoped to `forbidden_features` and its own docstring excludes the DERIVED view
>   block, because the generated schema is the wrong universe for it.
>   test_live_leakage.py DOES cover it against the whole rcm catalog — but that
>   module is pytest.mark.integration, excluded from CI unit runs, and skips
>   outright when no views are present. So on a clean clone, in CI, or before
>   `make views`, the drift is invisible. Closed by
>   tests/features/test_derived_blacklist_tracks_views.py, which resolves every
>   `forbidden_derived_features` key against the sql/views/ TEXT with no database.
>   A staleness tripwire, not proof of existence — the live check stays the
>   authority — and it moves discovery to the commit that renames the column.
>   NEGATIVE CONTROL RUN: replaying the rename in the view text strands exactly
>   `dollars_at_stake` and `heuristic_priority_score` and nothing else.
>   A TRAP RECORDED IN THE CONFIG: the tests resolve these names with fnmatch, the
>   RUNTIME guard has no glob support. `*dollars_at_stake` would pass every test
>   and block nothing. Third test in the new file refuses a glob outright.
>   The blacklist widening tripped the manifest's leakage_config_digest guard, as
>   designed, so the committed matrix was rebuilt in the same commit: parquet
>   BYTE-IDENTICAL (d11bd0df5a918d0d, p10/p11/p12's digest across four sessions),
>   manifest diff exactly one line. That was [ARTIFACT-REWRITE]'s second live use
>   and it correctly did NOT refuse a legitimate digest-only update.
> [BLACKLIST-LOCKSTEP] part 2 — **RESOLVED: NO CONFIG CHANGE REQUIRED.**
>   app-engineer-2 confirmed the final spellings and NOT ONE WORD CHANGED. The
>   VIEW is unrenamed (analytics-engineer's to fix); they re-mark
>   dollars_at_stake / heuristic_priority_score / action_type at the API boundary
>   in src/api/tables.py. Every re-marked name is a SUPERSTRING of its blacklist
>   entry, so the substring guard covers all three — measured, not assumed. The
>   entries stay accurate because the view they name is unchanged, and
>   tests/features/test_derived_blacklist_tracks_views.py stays green for the
>   same reason. It fires the day analytics-engineer renames the view, which is
>   the outcome it was built for.
>   FLAG FOR TEAM-LEAD, not mine to decide: this is the presentation-layer
>   re-marking the [QUEUE-PREFIX] delegation explicitly wanted to AVOID ("a
>   warehouse and a screen disagreeing on a column name is the worse outcome").
>   The view and the API now disagree by design, and the disagreement has a
>   consequence — see below.
> [DEMO-BUNDLE-PROVENANCE] (ml-engineer-7) registered
>   `dashboard/demo_data/*.duckdb` in PUBLISHED_SURFACES at app-engineer's
>   request, and found a defect while verifying their proposed text rather than
>   transcribing it. TWO CORRECTIONS to the justification they drafted:
>     - "14 datasets" is 16 declared (9 warehouse `select *` views, 5 model
>       output, 2 self-describing meta tables). Measured off src/demo/spec.py.
>     - "per-column classification" is per-DATASET: a provenance class, grain,
>       contains_simulated flag and note. Simulated COLUMNS are found by marker,
>       not declared one by one. Writing the stronger claim into a declaration a
>       reviewer relies on would be the overclaim this module exists to prevent.
>   THE DEFECT, and it is on the most exposed surface we have: the bundle copies
>   `vw_work_queue_priority` UNMODIFIED (src/demo/build.py: read_warehouse_datasets
>   — deliberately, so a dashboard figure cannot diverge from its SQL control
>   query). So `dollars_at_stake`, `heuristic_priority_score`, `priority_tier` and
>   `action_type` ship UNMARKED in a committed .duckdb that opens on a clean clone
>   with no database. The API's re-marking does not reach it. `action_type` is a
>   CASE on sim_denial_flag — the LABEL under a process name.
>   Registering the path alone would have made rule 1 green and rule 3 SILENT
>   (read_columns returns None for .duckdb), i.e. my registration would have
>   BLESSED it. So the registration is paired with
>   tests/features/test_demo_bundle_provenance.py, which opens the bundle and
>   applies the marker rule to the columns actually in it. Skips where no bundle
>   exists; the bundle is committed, so it runs everywhere once it lands.
>   FIXABLE BY EITHER OWNER: analytics-engineer marks the view (better — warehouse
>   and screen then agree) or app-engineer re-marks into the bundle as the API
>   already does. Not ml's to choose.
> [SKIP-BLIND] (ml-engineer-9) the [PASSTHROUGH-BLIND] shape swept across every
>   ml-owned guard, as directed. **ONE real instance, and it is in the file written
>   to close the previous one** — tests/features/test_demo_bundle_provenance.py.
>   Both column checks looked their table up by name and `pytest.skip`ped when it
>   was absent, so a bundle that is PRESENT, openable and holding sixteen tables
>   reported nothing to check the moment one was RENAMED. MEASURED on a scratchpad
>   copy of the committed bundle with `vw_work_queue_priority` renamed and nothing
>   else touched: the unmarked-column check goes from a correct RED naming all four
>   columns to SKIPPED, and in a suite summary a skip is indistinguishable from a
>   pass. The docstring's defence ("SKIPS when the bundle is absent... not a silent
>   pass") was true and covered the wrong condition: a missing TABLE is not a
>   missing bundle.
>   This is why it mattered now rather than later — a view rename is in flight for
>   [BLACKLIST-LOCKSTEP], so the exact condition that disarms the check is the one
>   being planned. Closed by `_require_table`, which FAILS on absence, names the
>   tables the bundle really holds, and tells the author which of the two cases
>   they are in (rename: re-point the constant in the same commit; drop: write a
>   sentence). Discovery moves to the commit that causes the drift. Negative
>   control included so the check cannot regress to a skip unnoticed.
>   SECOND, SMALLER FINDING in the same file, same family: rule 2 in
>   src/features/provenance.py is two-directional ("an undeclared column is one
>   that skipped rule 3") but the bundle check compared one direction only —
>   declared-minus-present. One column was slipping through it, measured not
>   supposed: `queue_mode` (1 `live_snapshot` row / 468 `backtest`), which the demo
>   build adds when it unions the two queue builds into one table and which the
>   per-run CSVs do not carry. Both directions are now checked, with a written
>   BUNDLE_ONLY_COLUMNS allowance that costs a sentence the way `marker_exempt`
>   does. FLAGGED FOR app-engineer AND qa, NOT RULED ON BY ME: the column is
>   app-engineer's, the allowance records that the check now SEES it rather than
>   that it is fine. My read is that it is the `as_of`/`split` class (a parameter
>   of our build, no dollar/rate/date reading) but that call is not mine.
>   CLEAN elsewhere, which is the useful half of the result. Every other skip in
>   tests/features/ and tests/models/ is absence of a gitignored artifact or of a
>   Postgres URL — the surface genuinely is not there — and each already says so.
>   SWEEP SCOPE, written down so the next ml session RE-RUNS it instead of
>   re-deriving what it covered (team-lead directed; a clean sweep is only durable
>   if its scope is recorded). The command, verbatim:
>     `grep -rn "pytest.skip\|importorskip\|skipif" tests/features tests/models
>      src/features src/models`
>   plus `grep -rn "exists()\|return None\|except \|is None" src/features/*.py
>   src/models/*.py` for the non-test guards. Directories: tests/features/,
>   tests/models/, src/features/, src/models/ — i.e. ml-owned only. NOT swept:
>   tests/leakage/ and tests/integration/ (qa's files), src/api/, dashboard/,
>   src/demo/ (app-engineer's), sql/ (analytics').
>   At 7866630 that command returns 12 live skip sites, and the TEST APPLIED to each
>   is: *is the skip's precondition the thing that is actually absent, or a plausible
>   neighbour of it?* Classification — 5 no-Postgres (test_feature_store_postgres x2,
>   test_determinism, test_train_postgres x2: the database itself is absent);
>   4 gitignored-artifact-absent (test_matrix_provenance x3, test_dollars_at_risk_
>   ruling, test_published_provenance work-queue CSVs, test_matrix_write_guard
>   matrix); 2 manifest-not-tracked-at-HEAD (test_matrix_write_guard — the ABSENT
>   leg of store.py's three-valued baseline, the one legitimately quiet case, since
>   UNREADABLE refuses); 1 bundle-absent + 1 duckdb-not-installed
>   (test_demo_bundle_provenance). All 12 pass the test. The two that FAILED it are
>   the two now fixed: table-absent-while-bundle-present (555b77e) and
>   published_files-empty-while-the-repo-holds-an-undeclared-tree (7866630).
>   Non-test guards: the one instrument that returns None rather than a verdict is
>   `provenance.read_columns` on a `.duckdb`, which is honest about the limit and is
>   covered by tests/features/test_demo_bundle_provenance.py opening the file itself.
>   The three-valued ABSENT/READABLE/UNREADABLE baseline in src/features/store.py
>   is the pattern the rest of them follow. ONE STRUCTURAL LIMIT recorded rather
>   than fixed: provenance rule 1's coverage scans a hardcoded PUBLISHED_ROOTS, so
>   its docstring claim that a new Phase 5 output "fails the build until it is
>   declared here" holds for a new FILE, not a new DIRECTORY. Evidence it does not
>   self-extend: `dashboard/demo_data` had to be hand-added at app-engineer's
>   request. Not currently exploited — measured: every tabular file Phase 5 writes
>   lands under an existing root — so this is a note for whoever adds the next
>   output root, not a defect.
>   CLOSED by [ROOTS-INVERTED] below on team-lead authorisation, so the docstring
>   no longer claims a guarantee the code does not provide.
>   `queue_mode` RULED PERMITTED UNMARKED by team-lead 2026-07-29 — a build label is
>   metadata about which of OUR runs produced the row (the `split`/`as_of` class,
>   QA ruling C) and §3.2 governs simulated VALUES, which a build label is not. Two
>   conditions attached: the reason is recorded where the column is declared (it is,
>   in BUNDLE_ONLY_COLUMNS — a bare exemption list is how `action_type` got
>   through), and app-engineer-3 keeps the call as owner. Also flagged by team-lead
>   FOR app-engineer-3, not an ml item: the 1-row `live_snapshot` against 468
>   `backtest` rows is the Phase 4 degenerate-queue finding resurfacing in the
>   bundle, and the dashboard should carry the model card's caveat rather than let
>   one row read as a queue. Relayed.
> [ROOTS-INVERTED] (ml-engineer-9, team-lead AUTHORISED) provenance rule 1's
>   coverage scan inverted rather than widened. It asked "is every file under these
>   three hardcoded roots declared?", which catches a new FILE and misses a new
>   DIRECTORY — a whole output tree could appear and rule 1 stayed green having
>   never looked. Now: walk the repo, and every tabular file that is OURS must live
>   under a DECLARED root. Default-deny, so the next output directory fails the
>   build the day it appears, which is what the docstring already promised.
>   `NON_OUTPUT_TREES` holds the exclusions and each carries a sentence, enforced by
>   a test — which caught SEVEN of my own entries as too thin to be reasons on the
>   first run, so the bar is real and not decorative.
>   THE `data/` DECISION, because it is the one that could have given the purchase
>   away: excluded as the FOUR TIERS (raw / validated / curated / simulated) and NOT
>   as the parent. Excluding `data/` would let a published extract escape under a
>   new `data/<something>/`; naming the tiers means such a directory fails. A test
>   asserts `data/demo/hosted_extract.parquet` is NOT excluded. `mlruns/` is
>   deliberately NOT pre-excluded: if anyone turns MLflow on, rule 1 fires and
>   someone decides whether that tree is published, which is the behaviour being
>   bought.
>   `.claude/` exclusion is load-bearing, not hygiene: `.claude/worktrees/` holds
>   full checkouts, so without it a scan from the primary checkout reports six other
>   worktrees' artifacts as uncovered — a guard that cries wolf gets switched off.
>   MEASURED both ways: primary checkout 1 tabular file ours, this worktree 16, rule
>   1 GREEN from both, scan under 0.01s (excluded trees are pruned during the walk,
>   so `.venv` is never descended into).
>   AND IT IMMEDIATELY EXPOSED ONE MORE [SKIP-BLIND], in the commit that made it
>   possible: `test_the_files_actually_on_disk_are_all_covered` was guarded by
>   `if not published_files(): skip("no artifacts generated")`. Defensible while the
>   scan only looked inside the roots — empty roots did mean nothing to check — but
>   after the inversion an undeclared output tree is exactly what it can find when
>   the roots are empty, so the guard would have skipped the only case needing it.
>   Now unconditional. Same lesson as 555b77e: a skip's precondition must be the
>   thing actually absent, not a plausible neighbour of it.
> [BLACKLIST-LIMIT] (team-lead directed) the MEMBERSHIP caveat is now in
>   docs/model_card.md beside the leakage guards, not only on this board: a
>   column-name blacklist cannot express that a table's POPULATION is conditioned
>   on the outcome. vw_work_queue_priority's where clause selects denied-or-open-AR
>   claims, so which ROWS are present is itself the label whatever the columns are
>   called, and every guard stays green while the label enters through the row
>   filter. The rule it implies is about JOINS, not names: Model A features come
>   from base tables, never from a view whose filter reads a post-submission fact.
>   VERIFIED rather than asserted — src/features/ contains zero `vw_` references;
>   extract.py reads sim_*/fact_*/dim_* only.
> VINTAGE SKEW — MEASURED by team-lead from config/sources.yaml, for the honesty
> pass. The code sets are CORRECT and unskewed (ICD-10-CM/PCS FY2023, HCPCS 2023,
> MS-DRG v40 FY2023 — all match the 2023-04 claims). The SKEW is in the crosswalk
> reference files: claims are 2023-04, but Hospital General Information is vintage
> 2026-04 (dataset xubh-q36u, ~3 years later) and Medicare Physician by Provider is
> data year 2024 released 2026-05. Hospitals open, close and change type over three
> years; providers change specialty and state. Currently documented NOWHERE as a
> skew — provenance_register records the vintages and requires them for
> reproducibility, but never states the mismatch. Gap in all user-facing surfaces.
> CROSSWALK COLLISION — currently in docs/model_card.md:526-530 with the numbers
> (4,876 synthetic providers onto 2,857 real CCNs, worst 8:1, display-only,
> forbidden as a feature). README:145 says "display-only enrichment" WITHOUT the
> numbers. Dashboard does not exist yet. Needs to be visible in all three.
>
> ===== qa-reviewer-p16 REVIEW ROUND 1, 2026-07-29 =====
>
> [BLOCKERS-3] ml-engineer-6's three blockers: **PASS**, measured on
> feat/phase5-blockers @ **4a87270** merged into feat/phase5-qa @ **8ea639c**.
> Commands: `make features` / `make train` / `make train-appeal`, `uv run pytest
> -m "not integration" -q`, `uv run ruff check . && ruff format --check .`.
>   NO NUMBER MOVED — re-measured, not taken on trust. Model A logistic ROC
>   **0.6254** / PR **0.2210**; xgboost − logistic **+0.0003 [−0.0173, +0.0183]**;
>   Model C xgboost **0.5611**; queue **65.7 / 61.0 / 59.8 / 0.7**. All identical.
>   The committed parquet is byte-identical (d11bd0df…, unchanged by bfea020 —
>   only the .json manifest gained `null_rates`), so Model A's numbers could not
>   have moved; Model C's were re-run and matched anyway.
>   QUEUE-PREFIX: all five columns renamed; shipped CSV headers verified on disk
>   (`sim_p_overturn, sim_recoverable_amt, sim_expected_recovery_amt,
>   sim_expected_net_recovery, sim_days_to_deadline`). `assert_publishable` runs
>   INSIDE the builder, so the guarantee is structural. The QA exposure gate
>   (test_output_surface_provenance) went RED→GREEN on this commit and nothing
>   else changed, which is what makes it evidence.
>   SHA-STAMP: dirty-tree semantics probed in three states. Clean → plain commit.
>   Tracked file modified → `describe: <sha>-dirty`, `dirty: true`, warning, and
>   the prose says "an uncommitted working tree". UNTRACKED FILE ONLY → `git
>   describe` alone reads clean, and the stamp still reports `dirty: true` because
>   it cross-checks `git status --porcelain`. That second path is the one that
>   would have produced a machine-attested falsehood; it is handled.
>
> [GUARD-DISARM] **NEW, ml's to fix, gate is RED and committed.**
> `committed_manifest()` collapses four conditions to None — no git, path outside
> the repo, `git show` failed, and MANIFEST DID NOT PARSE — and
> `_refuse_or_report` treats None as "nothing to protect". Three of those are.
> The fourth is not. MEASURED: with a corrupt committed manifest, and separately
> with the manifest untracked while the PARQUET is still committed, a matrix with
> `diagnosis_count` entirely null was written straight over the committed parquet
> with **no exception raised** — the exact failure bfea020 exists to prevent.
> This is the module's own principle turned on itself: `manifest_deviations`
> already refuses to pass over a missing `null_rates` block ("NULL RATES NOT
> COMPARED") because a check that did not run must not read like one that passed.
> Repro: `uv run pytest tests/features/test_matrix_write_guard.py -q` (2 RED).
> Not a blocker on merging the three — the settled shape is met — but must close
> before Phase 5 acceptance.
>
> [EMITTER-HOLE] **FOUND IN MY OWN INHERITED GATE, fixed, 723a95c.** The
> [QUEUE-PREFIX] "harder half" — the check extended so this cannot recur in Phase
> 5's outputs — was NOT covering half of Phase 5. Measured by planting three
> probe modules: a Streamlit page and a `df.to_dict()` helper were caught; a
> **FastAPI route** (`@router.get` returning `[{"recoverable_amt": …}]`) was NOT.
> It calls nothing on the emitter list and declares no `response_model`, so the
> only evidence it is a user-facing surface is the decorator. The gate was
> reporting a clean Phase 5 API boundary while blind to it. Added
> route-decorator detection, `model_dump`, and `st.write`/`st.json` when the
> first argument is not a string literal (prose stays exempt, measured). Added
> positive AND negative controls on the DETECTOR — the registration test is green
> on a tree with no dashboard and no API, and that green said nothing about
> whether the detector could see anything at all.
>
> [FIREWALL-DOC-HOLE] **RULED (d5b8402): NOT fixable by redaction. Recorded as a
> known limitation in docs/assumptions.md §12 and pinned by
> tests/leakage/test_firewall_doc_hole.py.** Three classes, all measured:
>   A. Realized label statistics. `sim_denial_flag` IS the Model A label; its mean
>      on the committed matrix is **0.1276**, which is the doc's 12.8%. Model A
>      prints `test base rate 0.1205` itself. Deleting the figure removes an
>      auditable record and restores nothing.
>   B. Generator internals + a prototype of ml's own deliverable: oracle AUC 0.68
>      (from `sim_latent_p`, banned as a feature), the latent 8.9% / 16.7% solve,
>      and §4.1's "+0.005–0.009 AUC" GBM-over-logistic prediction — published
>      BEFORE ml produced +0.0003 [−0.0173, +0.0183]. Required by §1/§7; deleting
>      them trades honesty for a wall with no other sides.
>   C. **FOUND WHILE RULING, and worse than A and B together.**
>      `config/simulation.yaml` (NOT firewalled from ml) publishes the latent
>      FORMULA and every coefficient, §3 republishes the odds ratios, §2 gives the
>      solved intercept — and all **fifteen** mechanism indicators the formula
>      consumes are features in the committed matrix. `sim_latent_p` is therefore
>      analytically reconstructible without opening `src/simulation/` once. Also
>      not fixable: those are legitimate pre-submission features.
>   THE EVIDENCE THE DISCIPLINE HELD IS IN THE NUMBER: Model A shipped **0.6254**
>   against a reconstructible **0.68** ceiling. A pipeline exploiting A, B or C
>   sits at the ceiling. §4.5's value is that gap, not an access control, and no
>   surface may describe it as an information barrier.
>   docs/simulated_forbidden_columns.md deliberately untouched.
>   CONSEQUENT FINDING FOR ml: **docs/model_card.md:139-141 is false as written** —
>   "The generator's realized overturn and rework rates sit behind the §4.5
>   firewall". They do not; assumptions.md §9 publishes the realized rework figure
>   ($29.88/denied claim) and §8 the overturn target. The threshold argument
>   survives (both factors were fixed from published benchmarks, before the
>   threshold) but the sentence justifying it is not true. ml to reword.
>
> [APP-R1] app-engineer @ **6e51e61** (demo extract): reviewed, NOT yet gateable —
> no dashboard page, no API endpoint, no bundle built, no tests. What is there is
> good and two things are verified rather than accepted:
>   * `dashboard/disclosures.py` numbers CHECKED against the runs I made, all
>     correct: 0.6254/0.2210, +0.0003 [−0.0173,+0.0183], logistic−payer_rule
>     +0.0333 [+0.0066,+0.0571] ROC and +0.0695 [+0.0444,+0.0998] PR, Model C
>     0.5611 and −0.0356 [−0.1325,+0.0597], 65.7/61.0/59.8, capacity point 9.6% /
>     20.9% / 26.3% (= 2.18× the 0.1205 base rate), 50.9% concentration, 2,663
>     denials / 967 appealed / 193 test rows. MODEL_C_HONESTY states the negative
>     result correctly and explicitly ("the probability does not earn its place in
>     the ordering"). NOT_A_FRAUD_SIGNAL present.
>   * KEYING RULE verified in the WAREHOUSE, not in the prose:
>     `vw_clean_claim_performance` is `group by e.prvdr_num`, and live it returns
>     4,877 rows = 4,877 distinct prvdr_num over 2,857 distinct CCNs. Correct.
>   MEASURED, AND THE DISCLOSURE UNDERSTATES IT — **grouping by facility NAME is
>   worse than by CCN. Worst case 15:1, not 8:1**, and 1,302 distinct names carry
>   more than one synthetic provider (vs 1,311 of 2,857 CCNs = the 45.9% already
>   quoted). Distinct display names = **2,816** < 2,857 CCNs, because real CMS
>   facilities share names across sites. The disclosure correctly forbids grouping
>   on either, but quotes only the 8:1 CCN figure — and NAME is the key a
>   dashboard is far more likely to group on, being the human-readable one. Add
>   the 15:1 name figure wherever 8:1 appears.
>   §5/§6 PROCESS: `src/demo/` is a new package nobody owns (app-engineer owns
>   `src/api/`, `dashboard/`, `docker-compose.yml`); the commit does not update
>   tasks.md (§6); and 1,417 lines of new module ship with zero tests (§6, "every
>   new module gets tests in the matching tests/ subfolder in the same PR").
>   tests/ is mine — coordinate, do not skip.
>   Minor: spec.py says vw_clean_claim_performance is 4,877 rows while the
>   disclosure says 4,876 crosswalked providers. Both are right (one provider has
>   no crosswalk row); say so once so it does not read as a typo.
>
> STILL RED AND CORRECTLY SO on feat/phase5-qa @ **58574e9** (= a8dab94 + 4a87270
> + 6e51e61 + my three commits): 4 disclosure gaps the human named — vintage skew
> absent from README.md and docs/model_card.md, collision numbers and keying rule
> absent from README.md ([README-FINAL]) — plus
> `test_the_dashboard_exists_before_phase_5_is_accepted`. Repro:
> `uv run pytest tests/contracts/test_user_facing_disclosures.py
> tests/contracts/test_dashboard_banner.py -q`.
> QA REVIEW ROUND 2 (qa-reviewer-p17), measured on feat/phase5-qa @ 6e7b288 =
> main a04d38c + feat/phase5-blockers f18dfc7 + feat/phase5-app 08d88cc. Baseline
> before the merges: 7 failed / 437 passed (the six inherited RED gates + the
> dashboard-exists gate). After: 8 failed / 443 passed — the one new red is
> [EMITTER-HOLE]'s route detector firing on src/api/main.py, which is the gate
> working. Repro for everything below:
>   uv run pytest -q -p no:randomly --ignore=tests/integration
>   RCM_DATA_SOURCE=postgres uv run python -c "<TestClient over src.api.main:app>"
>
> [APP-R2] THE API RUNS. Nobody had run these 1,816 lines. Exercised against the
> live warehouse (read-only, RCM_DATA_SOURCE=postgres): /health 200 (degraded, as
> designed — no model_c_work_queue outside a bundle), /metrics/executive 200 with
> claims_submitted=20867 and denied_claims=2663, which are the control-query
> figures, /work-queue?queue_mode=heuristic 200, /claims/1 200,
> /work-queue (model modes) 501 with the correct hint. No 500s. The schemas are
> strict enough that a malformed data_source block is rejected by pydantic — found
> that by hitting it with a stub.
>
> [PASSTHROUGH-BLIND] — THE FINDING OF THIS ROUND, and it is about MY OWN GATE.
> The [EMITTER-HOLE] fix made the detector SEE src/api/main.py. Registering it as
> a surface would make the gate GREEN AND PROVE NOTHING. Measured: I built the
> surface (route function, stub source, frame shaped like vw_work_queue_priority)
> and ran the exposure probe. It reported ZERO unmarked simulated columns —
> including on `sim_dollars_at_stake`, the very column the app re-marks. The
> reason is structural, not a bug: the probe perturbs a simulated INPUT and sees
> which emitted columns MOVE, so it can only see columns the surface COMPUTES. The
> entire API read side is PASS-THROUGH — every column arrives already computed by
> a view — so nothing moves and everything reads clean. The probe was built for
> src/models/work_queue.py, which computes its columns; at the wire it is the
> wrong instrument. **A perturbation probe cannot measure provenance across a
> pass-through boundary; only a DECLARATION can.** Same family as
> MATCHER-EXPRESSIVENESS: the instrument that runs is weaker than the instrument
> the gate's green implies.
>
> [WIRE-UNMARKED] What the right instrument finds. Cross-referencing the columns
> vw_work_queue_priority actually emits (verified against the live PG catalog, and
> a static parse of the view SQL agrees for 8 of 9 views) against
> config/model.yaml `forbidden_derived_features` — ml's own list, with ml's own
> reasons, so this is not a QA opinion:
>   sim_ marked on the wire: dollars_at_stake -> sim_dollars_at_stake,
>     heuristic_priority_score -> sim_heuristic_priority_score (app re-marks both).
>   UNMARKED AND UNDECLARED: `ar_open_flag` ("derived from sim_payment_date") and
>     `appeal_levels` ("count over sim_appeals; non-zero implies a denial"). They
>     are in neither RE_MARKED_COLUMNS nor PROCESS_METADATA_COLUMNS. They ship.
>     Confirmed in a live response body.
>   WRONGLY EXEMPTED: `action_type` is declared PROCESS METADATA by
>     src/api/tables.py:39-51, while config/model.yaml:192 forbids it as "a CASE on
>     sim_denial_flag; encodes the label directly". A rank or a recommendation is
>     process metadata under RULING C; a restatement of the label under a workflow
>     name is not. Every row of the live heuristic response carries
>     action_type=DENIAL_REWORK beside sim_denial_flag=true.
>   `priority_tier` is a rank and stays exempt under RULING C — recorded so the
>     omission does not read as an oversight (the age_days precedent).
>
> [EXEMPT-NO-REASON] src/api/tables.py::PROCESS_METADATA_COLUMNS is a BARE
> frozenset of 9 names. The qa exemption list it mirrors requires a REASON per
> entry and has `test_no_exemption_is_speculative` to refuse any exemption the
> probe would not otherwise have reported. Two lists, same job, and the one that
> actually runs at the wire has neither property. This is how `action_type` above
> got exempted with nothing to argue with.
>
> [REMARK-IS-API-ONLY] `re_mark_simulated_columns` lives in src/api/tables.py.
> dashboard/ is a separate package that will read the same views through the same
> src/demo/source.py. If the dashboard renders a queue frame without calling it,
> the identical unmarked columns ship on the SCREEN while the API is clean —
> and the screen is the surface the human's honesty instruction names.
> app-engineer-2: the re-marking belongs below both, not inside src/api/.
>
> [BUNDLE-ABSENT] `git ls-files` finds no .duckdb and no dashboard/demo_data/.
> src/demo/source.py:157 tells the reader "A clean clone ships one" and
> src/demo/spec.py:5 calls it "a COMMITTED data file". Neither is true yet, so the
> §7 clean-clone criterion is unmet by construction and the default data source
> raises on a fresh checkout. Not a defect in the code — the build step is
> pending — but the docstrings state it in the present tense TODAY.
> src/demo/spec.py declares provenance and contains_simulated PER DATASET, not per
> column; team-lead's acceptance condition already requires per-column
> classification for the bundle, and [PASSTHROUGH-BLIND] is the argument for why
> that per-column declaration is the only instrument that can check the wire.
>
> [MEMBERSHIP-UNDISCLOSED] ml-engineer-7's observation, checked at the API and not
> just reserved for the page: WorkQueueResponse carries `ranking`,
> `ordering_caveat` and `limitations`, and all three describe the ORDER. None
> states the MEMBERSHIP — that the where clause selected denied-or-open-AR claims,
> so the list already knows the outcome. Applies to /work-queue in both modes.
>
> [ROC-MISMATCH] low severity, honesty surface. src/api/main.py:79 tells every
> caller "the champion is a regularized logistic regression at ROC-AUC 0.6254"
> while /health on the same process reports roc_auc_test_fold 0.6185 for
> "logistic + isotonic". Both numbers are in docs/model_card.md:69-71 (uncalibrated
> vs calibrated) so neither is wrong, but the two statements the service makes
> about itself do not agree and a reader cannot tell which is the shipped number.
>
> OWNERSHIP NOTE, not a blocker: ml-engineer added tests/features/
> test_derived_blacklist_tracks_views.py and test_feature_marker_position.py.
> tests/ is qa's under §5. Both are good tests and both stay; recorded so the
> boundary does not erode by precedent.
>
> ===== qa-reviewer-p18 REVIEW ROUND 3, 2026-07-29 =====
> Measured on feat/phase5-qa @ **51cac7d** = main **0b6bd2d** + feat/phase5-blockers
> **21fe077** + feat/phase5-app **5a88d6b** + the preserved gate **5a59f42**.
> Repro for everything: `uv run pytest -q -p no:randomly --ignore=tests/integration`
> Baseline at 51cac7d before my commits: **12 failed / 471 passed**.
> After: **22 failed / 487 passed** — one red REMOVED as a false positive of my own
> gate, eleven added, every one measured below.
>
> [DASHBOARD-BLANK] **THE BLOCKER OF THIS ROUND. Two of the five pages are blank
> pages, and the whole dashboard had never been run by anyone.** All five pages call
> `render_page_header(title, subtitle, banner_extra=...)`; components.py:83 defines
> `render_page_header(title, subtitle)`. ar_recovery.py:43 and work_queue.py:61
> raise `TypeError: unexpected keyword argument 'banner_extra'` on the first
> statement that renders anything, so both produce **ZERO** blocks. The other three
> render 16 / 16 / 41 with no exceptions. Measured with streamlit's own AppTest,
> one interpreter per page.
>   This is an INTERRUPTED REFACTOR, not carelessness: components.py:83-94 documents
>   deliberately REMOVING the banner from render_page_header, agreeing with the qa
>   banner gate that a §6 obligation discharged two frames away is one a future
>   author can silently drop. The docstring landed; the five call sites did not.
>   Fix (app-engineer): add `banner_extra: str | None = None` to render_page_header
>   and have it call render_synthetic_data_banner(extra=banner_extra) — OR keep the
>   split and put `render_synthetic_data_banner(MEMBERSHIP_WARNING)` in each page.
>   The second matches the docstring's own argument.
> [BANNER-ABSENT] §6, "no page ships without it": **zero of five pages render the
>   banner**, confirmed twice by independent instruments — statically by
>   test_dashboard_banner.py (AST call names; `banner_extra=` is a kwarg, not a
>   call, so it correctly did not count it) and in the RENDERED OUTPUT by my new
>   test_dashboard_renders.py. app.py:116 renders it inside `landing()`, which
>   `st.navigation` executes for the HOME page only; the sidebar carries
>   BANNER_SHORT as a caption, which is not the §6 block.
> [RENDER-GATE] NEW, mine, tests/contracts/test_dashboard_renders.py. The static
>   banner gate cannot see a page that crashes ABOVE its banner call, which is the
>   exact failure above — a correct call on line 50 with a TypeError on line 43 is
>   green forever. So each page is now RUN and the check is made on the output, with
>   positive and negative controls ON THE HARNESS (a page that raises, a page that
>   renders nothing). Subprocess per page is required, not stylistic: five pages
>   through AppTest in one interpreter SEGFAULTS (exit 139) after the first, because
>   the pages pull duckdb/xgboost/shap into a process AppTest is driving.
>
> [DISCLOSURE-FALSE-RED] **A red of MY OWN, and it was pointing at correct work.**
> test_user_facing_disclosures.py reported that `dashboard/` never says the
> crosswalk is forbidden as a model feature. It says exactly that, at
> dashboard/disclosures.py:119-120, and a user reads it — but the gate ran
> `read_text()` and the sentence is built by implicit concatenation, so the FILE
> holds `**forbidden as a "\n    "feature**` and the regex cannot cross the quote,
> the newline and the indent. PROVED both ways: runtime string matches, file text
> does not. Same family as MATCHER-EXPRESSIVENESS — the instrument that ran was
> weaker than its result implied — and this one pushes an author towards rewording
> an honest sentence to satisfy a grep. FIXED at the root: Python surfaces are now
> read through `ast`, which merges adjacent literals at parse time, so the gate sees
> the value a user sees. Docstrings and comments are now EXCLUDED (a user reads
> neither), with three controls pinning concatenation-yes / comment-no /
> docstring-no. The dashboard surface still passes the vintage and collision checks
> under the stricter rule, so those disclosures are in real rendered strings.
> app-engineer's disclosure work is good: the 15:1 NAME figure, the 2,816 distinct
> names, the 4,877-vs-4,876 note, and the keying rule are all present and correct.
>
> STILL RED AND CORRECT — the four the human named, all in docs (README.md ×3,
> docs/model_card.md ×1): vintage skew absent from README and never stated as a
> MISMATCH in the model card; collision numbers and keying rule absent from README.
> [README-FINAL] is also still open: README:3 says "Phases 1–3 of 5 are complete",
> Phase 4 and 5 are 🚧, and there are ZERO mentions of docs/model_card.md.
>
> [GUARD-DISARM-3] p17's preserved gate is RIGHT and I re-measured it end to end
> rather than trusting the docstring: with git unrunnable, the committed parquet went
> **1,469,982 -> 1,456,629 bytes** and `diagnosis_count` null rate **0.0 -> 1.0**
> with NO exception. The control beside it (a first write with no artifact present)
> correctly stays quiet, so the fix must distinguish "nothing to protect" from
> "cannot tell".
>   **THE FIX IS NOT WHERE IT LOOKS.** The door taken is NOT the FileNotFoundError
>   handler at store.py:328 — control flow never reaches it. `_repo_root_for`
>   (store.py:226-234) catches `CalledProcessError, FileNotFoundError, OSError,
>   NotADirectoryError` together and returns None, so committed_baseline:300 answers
>   `absent` with `reason="<path> is not inside a git repository"` — a statement that
>   is FALSE in this state, in a diagnostic a future reader will act on. Fix
>   `_repo_root_for` to distinguish "git answered: not a repo" from "git could not be
>   run", and propagate the second as `unreadable`. Patching only line 328 leaves the
>   parquet just as overwritable and the test just as red.
>
> [BUNDLE-UNMARKED] ml's own gate (21fe077) is RED against app's bundle, and it is
> the [REMARK-IS-API-ONLY] prediction coming true in the most exposed artifact in
> the repo: the committed 8,400,896-byte rcm_demo.duckdb ships
> vw_work_queue_priority with `dollars_at_stake`, `heuristic_priority_score`,
> `action_type` unmarked. src/api/tables.py re-marks all three on the API path
> (`sim_action_type` now included — [WIRE-UNMARKED]'s action_type finding is CLOSED
> at the API), so the two published surfaces disagree and the bundle is the one a
> reader opens with none of our code in front of it. Fixable by analytics-engineer
> in the view (better — warehouse and screen then agree) or app-engineer on the way
> into the bundle.
> [TIER-DISPUTED] the fourth column ml's gate flags, `priority_tier`, is where **two
>   live instruments contradict each other**, and I removed my own hardcoded
>   carve-out rather than keep the disagreement invisible. MEASURED at the source,
>   sql/views/vw_work_queue_priority.sql:99:
>       ntile(4) over (order by heuristic_priority_score desc) as priority_tier
>   So the EXEMPTION is factually right and **config/model.yaml:186's REASON is the
>   inaccurate one** — it says "built on sim_denial_flag". Forbidding it as a FEATURE
>   stays correct (transitively simulated money). ONE-LINE FIX, ml-engineer: reword
>   that reason to name the ntile over heuristic_priority_score, and the
>   `"ntile" not in reason` filter exempts it in ml's own words with no name
>   subtraction anywhere. test_wire_provenance.py no longer subtracts
>   `{"priority_tier"}` by name, so it is red until that wording lands — red is the
>   honest state for two gates that disagree about a published column.
>
> [BUNDLE-UNREGISTERED-§3.3] the 8 MB bundle is registered in
> src/features/provenance.py PUBLISHED_SURFACES and in docs/model_card.md, and NOT
> in docs/provenance_register.md or docs/data_dictionary.md — grep finds no
> `rcm_demo`, no `demo_build_info`, no `demo_manifest` in either. §3.3 requires both
> in the same PR that adds a table, and the bundle adds 16 datasets including two
> NEW tables that exist nowhere else (`demo_manifest`, `demo_build_info`). This is
> the Phase 4 unregistered-artifact hole recurring on a bigger surface.
>   ON TEAM-LEAD'S "per-column classification" CONDITION: ml is right that
>   src/demo/spec.py declares per-DATASET, and right to have refused the overclaim.
>   My ruling as reviewer: per-dataset class + the `sim_` marker rule is sufficient
>   **only while the marker rule actually holds on the bundle's columns**, and
>   [BUNDLE-UNMARKED] is that premise failing today. Fix the three columns and the
>   per-dataset declaration becomes honest; leave them and no amount of declaration
>   makes it so.
>
> [RECONCILE-17/17] **§7 PASS on the path that ships, run rather than asserted.**
> dashboard/reconcile.py declares 17 checks, each reaching the figure from a second
> dataset and carrying runnable control SQL. Against the committed bundle: 17
> evaluated, **17/17 pass**. Against Postgres: 14/14 pass.
> [RECONCILE-SILENT-SKIP] but `run()` does `continue` on any check whose datasets
>   are absent, and model_data_quality.py:76 prints "All {len(results)} reconciled
>   figures match" over the EVALUATED count. MEASURED on Postgres: **17 declared, 14
>   evaluated, 3 vanished** (the model ones) behind a green tick with nothing saying
>   three checks never ran. Same shape as [GUARD-DISARM] but in a reporter, on the
>   one page whose job is telling a reader the numbers can be trusted — and the repo
>   already refuses it one layer down, where `manifest_deviations` emits "NULL RATES
>   NOT COMPARED" rather than passing over a missing baseline. Pinned RED by
>   tests/contracts/test_dashboard_reconciliation.py. Small fix, app-engineer's:
>   carry the unevaluated figures through as NOT_CHECKED rows or print
>   declared-vs-evaluated.
>
> [COMPOSE-CLEAN-CLONE] **§7 "docker compose up works from a clean clone" FAILS, and
> it fails before it starts.** Cloned 51cac7d to a fresh directory and ran
> `docker compose config` (non-destructive; the running warehouse was not touched):
> **exit 1**, `env file .../.env not found`. `.env` is gitignored by design, and
> docker-compose.yml:5 has `env_file: .env` with no `required: false` and no
> default. Fix is one of: `env_file: [{path: .env, required: false}]` with
> POSTGRES_* defaults inline, or a documented `cp .env.example .env` as step 0 of
> the clean-clone path — but §7 says `docker compose up` works, so the first is
> closer to the criterion.
> [COMPOSE-NO-APP] and even fixed, `docker compose up` starts **postgres only**.
>   There is no api service and no dashboard service in docker-compose.yml for a
>   phase whose two deliverables are a FastAPI service and a Streamlit dashboard.
>   app-engineer owns the file.
>
> WHAT I VERIFIED AS PASSING, so it is not re-litigated:
>   * API on the BUNDLE path (nobody had run this; p17 ran Postgres only):
>     /health 200 **"ok"**, kind=bundle — not degraded, because the bundle DOES carry
>     model_c_work_queue. /metrics/executive, /claims/1 and /work-queue in all three
>     declared modes (backtest, live_snapshot, heuristic) all 200. No 500s.
>   * OpenAPI schema valid and generatable: 3.1.0, 6 paths, 15 component schemas,
>     JSON-serialisable. §7 "API schema validated" met.
>   * [BUNDLE-ABSENT] CLOSED: rcm_demo.duckdb is tracked, committed, not gitignored,
>     8,400,896 bytes, and present in a fresh clone. The docstrings' present tense is
>     now true.
>   * SYNTHETIC-ID KEYING: no groupby on facility name or CCN anywhere in dashboard/.
>     The only two groupbys are on sim_denial_category and (reason_code,
>     analyst_action). The provider table is keyed on prvdr_num and says so on
>     screen, with the 15:1 name figure in the column help.
>   * MODEL C AS A NEGATIVE RESULT: work_queue.py:85 renders MODEL_C_HONESTY as
>     st.error, and the capacity chart plots "largest denial first (no model)" at
>     0.657 against the score at 0.610. Nothing implies otherwise.
>   * MEMBERSHIP: work_queue.py:51-59 carries a correct, strongly-worded
>     MEMBERSHIP_WARNING — which is passed as `banner_extra` and therefore currently
>     renders NOTHING. It lands the moment [DASHBOARD-BLANK] is fixed.
>     [MEMBERSHIP-UNDISCLOSED] stays open at the API: still no membership statement
>     in the /work-queue payload, both modes.
>   * [FIREWALL-CLAIM]: no surface describes §4.5 as an information barrier. Zero
>     hits for firewall/information-barrier/"cannot see" in dashboard/ or src/api/.
>     ml fixed the model-card sentence at 7c110c0.
>   * No anomaly is called fraud anywhere; NOT_A_FRAUD_SIGNAL renders on both risky
>     pages, and src/api/main.py:85 says "never a fraud signal".
>   * ruff clean across the whole merged tree (161 files) after my commits.
>
> A CHECK I WROTE, RAN AND DELETED, recorded so it is not re-added: "every control
> total must come from a DIFFERENT dataset". It reported two of the 17 — full +
> partial = total denials, and denied + non-denied = open per bucket. Both are
> additivity identities WITHIN one view, which is a real check. My rule was wrong,
> so it is gone rather than weakened; the reasoning is in the file.
- [x] FastAPI endpoints with schemas + version metadata
- [x] Streamlit dashboard (5 pages, synthetic banner on all)
- [x] DuckDB demo extract regenerated from clean source SHA
  `ab2aa41541909a991877a8264a64e5856896599b`; independent final-artifact QA
  accepted with SHA-256
  `66456ebf4e52e4c5f5565cf6085efb89d80bc264710b3783bd1eb2e491a03e95`
- [x] Docker Compose clean-clone start with exact 24-base-table / 9-view
  PostgreSQL contract and dependency-aware readiness; independently QA accepted
- [x] README, local and hosted screenshots, and demo walkthrough
- [x] ACCEPTANCE (qa-reviewer): full honesty, reconciliation, clean-clone,
  final-artifact, and public hosted passes completed 2026-08-01

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
- [x] Phase 5 simulated-derived naming and work-queue membership disclosure —
  app-engineer, `feat/phase5-provenance-disclosure`: renamed derived claim and
  heuristic-queue values to `sim_*` at the SQL-view boundary; propagated the
  names through the API, dashboard, committed demo bundle, provenance documents,
  and leakage config. `PROCESS_METADATA_COLUMNS` is now a reasoned mapping, and
  `/work-queue` states that membership is selected from simulated denial or open
  A/R outcomes. Bundle regenerated from the dirty feature tree using fresh local
  Postgres views and preserved committed model datasets. QA follow-up corrected
  the stale public field names in the model-quality page, model card, and SQL
  headers; the bundle remains explicitly non-final until clean-SHA regeneration.
- [x] Test gate green on clean clone (qa-reviewer, merged to main bc2a7ab, pushed):
  smoke tests + pytest config; scope-expanded dependency fix (numpy<2.1 cap,
  [tool.uv] environments bounded to CPython 3.11–3.12, uv.lock committed,
  .python-version=3.11) to unbreak `uv sync` — RATIFIED by team-lead 2026-07-22;
  requires-python ">=3.11" unchanged, locked decisions intact.
