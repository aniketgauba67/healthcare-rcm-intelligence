# PROMPTS.md — Copy-Paste Prompt Pack for the Agent Team

This file contains every prompt you need, in order. Copy each block verbatim
into Claude Code at the indicated moment. Prompts are designed for maximum
autonomy: the team lead spawns teammates, they coordinate through the shared
task list and direct messages, and QA gates each phase before the team moves on.

---

## STEP 0 — One-time setup (terminal, before anything)

```bash
# 1. Agent teams flag is already in .claude/settings.json (project-level).
#    If you prefer it globally, add to ~/.claude/settings.json:
#    { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }

# 2. Install tmux for the split-pane view (macOS: brew install tmux;
#    Ubuntu: sudo apt install tmux). Use a standalone terminal —
#    split panes do NOT work inside VS Code's integrated terminal.

# 3. Start a tmux session, then launch Claude Code inside it:
tmux new -s rcm
cd /path/to/healthcare-rcm-intelligence
cp .env.example .env   # edit the password
claude
```

Notes on autonomy: `.claude/settings.json` pre-approves the common commands
(python, uv, pytest, git commit, CMS downloads) so teammates don't stall on
permission prompts. Anything outside that allowlist will still ask you — that
is intentional guardrailing. Agent teams use roughly 7x the tokens of a single
session, so run phases one at a time, not all five at once.

---

## STEP 1 — Kickoff prompt (paste as your FIRST message in Claude Code)

```
Read CLAUDE.md, tasks.md, and every file in .claude/agents/ completely before
doing anything. You are the TEAM LEAD for this project. You never write feature
code yourself — you create the team, assign tasks, route messages, resolve
conflicts, enforce phase gates, and keep tasks.md current.

Create an agent team for this project. Operating rules for the whole team:

1. PHASE DISCIPLINE. Work strictly in the phase order defined in tasks.md.
   A phase is complete ONLY when the qa-reviewer teammate checks its
   ACCEPTANCE box in tasks.md. Do not start the next phase's tasks early,
   except for read-only planning notes.
2. FILE OWNERSHIP. Each teammate touches only the paths listed for its role
   in CLAUDE.md §5. If a task needs a file owned by someone else, message
   that teammate instead of editing it.
3. COMMUNICATION. Teammates claim tasks from tasks.md before starting, post
   a one-line status when done, and message each other directly when their
   work interfaces (e.g., simulation-engineer tells data-engineer the sim_
   table schemas before DDL is written). Broadcast blockers immediately.
4. QA GATES. Every completed task goes to qa-reviewer before it counts.
   qa-reviewer replies with PASS or a numbered fix list with file paths and
   reproduction commands. Authors fix and resubmit. Maximum 3 review cycles
   per task; if still failing, escalate to me under "Blocked / Questions
   for human" in tasks.md and move on to other work.
5. NO GUESSING ON POLICY. Anything touching data provenance rules (CLAUDE.md
   §3) or leakage rules (§4) that is ambiguous goes in the Blocked section —
   never resolved by assumption.
6. COMMITS. Small commits on feature branches, message format
   "[agent-name] description". Merge to main only after qa-reviewer PASS
   and `make test` green.
7. AUTONOMY. Do not stop to ask me for approval on routine work. Only
   surface: (a) items in the Blocked section, (b) phase completion
   summaries, (c) anything requiring credentials or paid services.

Now begin Phase 1. Spawn these teammates with the personas from
.claude/agents/: data-engineer and qa-reviewer. Assign all Phase 1 tasks from
tasks.md to data-engineer, with qa-reviewer reviewing each as it lands.
data-engineer's first task is the source download scripts — start with a
small state-filtered NPPES extract and the CMS synthetic claims ZIP, record
checksums and vintages in config/sources.yaml, and post actual file sizes and
row counts to the task list so we have real numbers.

When every Phase 1 acceptance box is checked, give me a phase summary:
what was built, row counts, reconciliation results, open risks — then STOP
and wait for my go-ahead for Phase 2.
```

---

## STEP 2 — Phase 2 prompt (paste after you approve Phase 1)

```
Phase 1 is approved. Begin Phase 2 (Simulation Layer).

Spawn simulation-engineer (persona in .claude/agents/simulation-engineer.md).
Keep qa-reviewer active. data-engineer stays available for warehouse loading
of sim_ tables but takes no new scope.

Assign all Phase 2 tasks from tasks.md to simulation-engineer. Sequence:
(1) design sim_ table schemas and message data-engineer to review DDL fit,
(2) build the seeded generator per CLAUDE.md §3 and the agent persona —
including the submission→adjudication→payment timeline,
(3) calibrate marginals to cited benchmark ranges and write
docs/assumptions.md with real citations (use web search if available; if
not, leave clearly marked TODO_CITATION placeholders and log it in Blocked),
(4) run the validation suite: directional validity, distributional validity,
temporal ordering, class balance, and byte-identical reproducibility at
fixed seed,
(5) load sim_ tables to the warehouse via data-engineer and update the
provenance register.

qa-reviewer must additionally verify: every simulated column has the sim_
prefix, sim_latent_p exists but is documented as validation-only, and no
text anywhere claims simulated values reflect real CMS behavior.

Same autonomy rules as before. When all Phase 2 acceptance boxes are
checked, post a phase summary with the achieved denial rate, category mix,
and validation results, then STOP for my review.
```

---

## STEP 3 — Phase 3 prompt

```
Phase 2 is approved. Begin Phase 3 (Analytics + KPI Views).

Spawn analytics-engineer (persona in .claude/agents/analytics-engineer.md).
Keep qa-reviewer active. Others wind down unless messaged.

Assign all Phase 3 tasks from tasks.md. Requirements beyond the persona:
- Every SQL view ships with its control query in sql/quality/ and a test in
  tests/integration/ asserting reconciliation.
- Notebooks must run clean top-to-bottom via `uv run jupyter nbconvert
  --execute` (add the dev dependency if needed) and each stated insight
  must name its statistical evidence (test, effect size, CI).
- Payer-level findings must state in the notebook text that the payer
  dimension is simulated.
- Deliver the ">= 12 insights" list as docs/insights.md, each insight one
  paragraph: finding, evidence, business action.

qa-reviewer adds a reconciliation pass: pick 5 random dashboard-bound
numbers and re-derive them independently with fresh SQL.

When all acceptance boxes are checked, post the insights list and
reconciliation results, then STOP for my review.
```

---

## STEP 4 — Phase 4 prompt

```
Phase 3 is approved. Begin Phase 4 (ML).

Spawn ml-engineer (persona in .claude/agents/ml-engineer.md). Keep
qa-reviewer active.

ENFORCE THE FIREWALL: ml-engineer must not read src/simulation/ or
config/simulation.yaml. qa-reviewer verifies this by checking that no ml
code imports from src.simulation and, at review time, asking ml-engineer to
state its feature list so it can be diffed against config/model.yaml's
forbidden_features (including wildcard patterns).

Assign all Phase 4 tasks. Additional requirements:
- Baselines first, committed and evaluated BEFORE any XGBoost work.
- Temporal split boundaries documented in docs/model_card.md.
- If any advanced model beats baseline by an implausible margin or scores
  PR-AUC > 0.95, treat it as suspected leakage: halt, investigate, and log
  findings in Blocked rather than proceeding.
- Expected Net Recovery scoring must show the deadline-urgency and
  compliance overrides operating in a worked example in the model card.
- Deliverables: model artifacts with versioned metadata, experiment report
  (docs/experiments.md), completed model card, SHAP global plot + 3 example
  claim-level waterfalls saved to docs/figures/.

When all acceptance boxes are checked, post headline metrics (baseline vs
advanced, calibration, top-decile dollars captured) and STOP for my review.
```

---

## STEP 5 — Phase 5 prompt

```
Phase 4 is approved. Begin Phase 5 (App + Packaging) — final phase.

Spawn app-engineer (persona in .claude/agents/app-engineer.md). Keep
qa-reviewer active; other teammates respond to interface questions only.

Assign all Phase 5 tasks. Additional requirements:
- Dashboard pages read from the DuckDB/Parquet demo extract by default so
  the hosted demo needs no database; a flag switches to live Postgres.
- The synthetic-data banner is a single shared component imported by every
  page; qa-reviewer fails any page without it.
- API responses containing sim_ fields include "contains_simulated": true.
- Write docs/demo_script.md: a 3–5 minute walkthrough (page order, what to
  say, which numbers to point at).
- Finalize README.md per CLAUDE.md structure with screenshots saved to
  docs/figures/ (use Streamlit screenshots via the browser if available;
  otherwise placeholder markers and a Blocked note).
- Full pipeline test from clean clone: document the exact command sequence
  in README and verify it in CI-adjacent form (make test + smoke scripts).

Final gate — qa-reviewer runs the complete release checklist from CLAUDE.md
§7 across ALL phases plus the honesty pass, and writes the results to
docs/release_review.md.

When done, post: the demo script, the release review, remaining known
limitations, and suggested resume bullets USING ONLY MEASURED NUMBERS from
this build. Then STOP. Project complete pending my final review.
```

---

## UTILITY PROMPTS (use as needed)

**Status check (any time):**
```
Give me a status report: per-teammate current task, tasks.md deltas since my
last message, blockers, and estimated remaining work for this phase. Do not
interrupt teammates mid-task to compile this.
```

**Unstick a stalled team:**
```
Progress has stalled. Have each teammate post its current state and last
completed action to the task list, identify the bottleneck, reassign or
split the stuck task, and resume. If the blocker needs me, state exactly
what decision or input you need in one sentence.
```

**Resume after closing the terminal:**
```
Re-read CLAUDE.md and tasks.md. Reconstruct project state from tasks.md, git
log, and the file tree. Report which phase we are in and what remains, then
re-spawn only the teammates needed for the current phase and continue.
```

**Independent second-opinion review (optional, single session — no team):**
```
You are an external reviewer who has never seen this project. Read README.md,
CLAUDE.md, docs/, and skim the code. Produce a hiring-manager-style critique:
what impresses, what looks fake or hand-wavy, what would fail under
questioning in an interview, and the 5 highest-impact fixes. Be harsh.
```

---

## HONEST LIMITS OF "WITHOUT MY INTERVENTION"

The team will run long stretches unattended, but plan on touching it at
5 points minimum — the phase gates. That's deliberate: phase gates are where
compounding errors get caught cheaply. Also expect: occasional permission
prompts for commands outside the allowlist, agent-teams rough edges around
session resumption (use the resume prompt above), and high token usage
(~7x a single session). Budget accordingly and run one phase per sitting.
