# Simulation Assumptions & Calibration Anchors

**Read this first.** Everything the simulation layer produces is invented. The
numbers in `config/simulation.yaml` are **design choices**, chosen so the
generated data sits inside ranges that published industry reporting describes as
plausible. They are **not** measurements of the CMS synthetic claims, and they
are **not** a description of how Medicare, Medicare Advantage, commercial, or
Medicaid payers actually adjudicate claims. No conclusion about real payer
behaviour may be drawn from any output of this layer.

Three specific honesty statements, because they are the ones easiest to get
wrong when this project is presented:

1. **The source claims contain no denials.** CMS synthetic Medicare FFS claims
   carry service dates and payment amounts, not adjudication outcomes,
   submission dates, appeals, or workflow events. Every one of those is
   fabricated here. The denial *rate* is something this simulation was told to
   produce, not something it discovered.
2. **The multi-payer dimension is fiction** (CLAUDE.md §3.5). Medicare FFS has
   one payer. The five payers below are archetypes invented for this project;
   they are deliberately not modelled on, named after, or calibrated to any
   identifiable real insurer, and any per-payer difference shown anywhere in
   this project is a parameter someone typed into a YAML file.
3. **Citations bracket ranges; they do not validate outputs.** Each anchor below
   names a publisher and the period its figure refers to. They are recorded from
   published summaries and should be treated as approximate — re-verify against
   the publisher before quoting any of them outside this repo. A citation next
   to a parameter means "this is why the range is plausible", never "this output
   was checked against real data".

Provenance classification for this entire layer: **SIMULATED** (CLAUDE.md §3.1).
Every table and column is `sim_`-prefixed (§3.2).

---

## 1. Overall denial rate

| Parameter | Value | Kind |
|---|---|---|
| `targets.overall_denial_rate` | 0.10 – 0.18, calibrated to **0.130** | DESIGN CHOICE |

**Anchors.**
- KFF's analysis of transparency-in-coverage data for HealthCare.gov marketplace
  issuers (published 2023, covering 2021 claims) reported roughly **17%** of
  in-network claims denied in aggregate, with enormous spread across issuers —
  low single digits to nearly half. The spread is the useful part: it says a
  single "correct" denial rate does not exist.
- Industry revenue-cycle reporting (Change Healthcare / Optum denials index, and
  subsequent vendor reporting through 2023–2024) has put hospital **initial**
  denial rates in the high single digits to low teens, trending upward over the
  last decade.
- Premier Inc.'s 2024 provider survey reported roughly **15%** of claims
  submitted to private payers being initially denied.

**Why 13%.** Our book is a *simulated mix* that is 40% traditional Medicare FFS
(the least denial-prone archetype) and 60% managed care. A point in the lower-
middle of the cited spread is the defensible choice; anything near the top of
the KFF range would be asserting a worst-case book we have no basis for. The
0.10–0.18 band is what the validation suite enforces.

**Deliberate limitation.** These are *inpatient* facility claims. Most published
denial-rate figures mix professional and facility claims across all settings, so
the anchor is directionally useful and no more than that.

## 2. Label noise

| Parameter | Value | Kind |
|---|---|---|
| `targets.label_noise` | 0.05 symmetric flip | DESIGN CHOICE |

No anchor, and none is claimed. This exists for a modelling reason: after
drawing the outcome from the latent probability, 5% of labels are flipped, which
puts a hard ceiling on achievable AUC and forces the Phase 4 models to face
genuine irreducible uncertainty. Without it, a sufficiently flexible model can
recover the generator and report a fraudulently perfect score. Real adjudication
has its own irreducible noise (payer discretion, human error, appeals that
reverse the original decision); 5% is our stand-in for it, not an estimate of
it.

Measured effect on this subset: the best achievable discrimination — scoring
with the *true* latent probability, which no model can beat — is **AUC ≈ 0.68**.
That is the ceiling Phase 4 should be compared against, not 1.0.

**A calibration subtlety worth stating explicitly.** Symmetric noise pulls the
observed rate toward 0.5: observed = p·(1−2ε) + ε. Solving the logistic
intercept against the 13% target directly would have produced a *latent* mean of
13% and an *observed* rate of 16.7% — outside the tolerance and, more
importantly, wrong, because the benchmarks in §1 are rates of observed denials.
The generator therefore inverts the noise first and solves for the latent mean
(8.9%) that lands the observed rate on target. A configuration whose target rate
falls outside [ε, 1−ε] is unreachable and is rejected rather than approximated.

## 3. Risk mechanisms and their odds ratios

| Mechanism | OR | Kind |
|---|---|---|
| Prior authorization required and missing | 3.00 | DESIGN CHOICE |
| Authorization obtained late | 1.45 | DESIGN CHOICE |
| Eligibility verification failed | 2.50 | DESIGN CHOICE |
| Eligibility not verified at all | 1.35 | DESIGN CHOICE |
| Documentation deficit | 1.80 | DESIGN CHOICE |
| Coding specificity deficit | 1.65 | DESIGN CHOICE |
| Late filing (past payer limit) | 2.20 | DESIGN CHOICE |
| Duplicate submission | 4.50 | DESIGN CHOICE |
| **Interaction:** auth required × auth missing | 1.60 extra | DESIGN CHOICE |
| **Interaction:** auth missing × documentation deficit | 1.30 extra | DESIGN CHOICE |

**Anchors for the ordering, not the magnitudes.** Front-end causes —
registration/eligibility errors, missing prior authorization, and missing or
insufficient documentation — are consistently reported as the largest
contributors to initial denials (Change Healthcare denials index; Experian
Health's *State of Claims* surveys, 2022–2024, which put missing/inaccurate data
and prior authorization at the top of provider-reported denial reasons). The
*relative* ordering encoded above follows that reporting. The specific odds
ratios do not come from any published estimate; they were chosen to be large
enough to be learnable and small enough to leave substantial overlap between the
denied and paid populations.

**The interaction is the point.** `auth_required` alone is close to harmless —
that is what makes the term interesting. Risk concentrates where authorization
was required *and* was not obtained. A model that learns only two main effects
will systematically misprice both populations, which is exactly the behaviour
Phase 4 needs to be able to demonstrate.

### 3.1 Prevalence of the risk facts

| Parameter | Value | Anchor |
|---|---|---|
| `auth_required_rate` per payer | 0.10 (Medicare FFS) → 0.65 (Medicaid MCO) | See below |
| `auth_missing_given_required` | 0.115 | DESIGN CHOICE |
| `eligibility_failed` | 0.055 | DESIGN CHOICE |
| `documentation_deficit` | 0.150 | DESIGN CHOICE |
| `coding_specificity_deficit` | 0.120 | DESIGN CHOICE |
| `duplicate_submission` | 0.012 | DESIGN CHOICE |

The **payer gradient on prior-authorization prevalence** is the one place the
archetypes are grounded in a real structural difference rather than invented
whole: traditional Medicare FFS applies prior authorization to very few
inpatient admissions, while Medicare Advantage and Medicaid managed care apply
it broadly. KFF's work on Medicare Advantage prior authorization (2023 and 2024
editions, covering 2021–2022 plan data) documents tens of millions of MA prior
authorization determinations annually against a traditional-Medicare program
that requires almost none. The *shape* of the gradient is anchored; the specific
rates are ours.

## 4. Non-linear mechanisms

| Mechanism | Form | Kind |
|---|---|---|
| Log billed charges | `0.22·z + 0.09·z²` | DESIGN CHOICE |
| Length of stay | +0.35 if LOS ≤ 1 day; +0.30 per 10 days beyond 14 | DESIGN CHOICE |
| Diagnosis-code count | `-0.30·(n/10) + 0.14·(n/10)²` | DESIGN CHOICE |
| Per-provider latent quality | `Normal(0, 0.45)`, one draw per billing provider | DESIGN CHOICE |

These exist so that a linear baseline is genuinely beaten by a non-linear model
for a real reason rather than by tuning luck. The narrative shapes are chosen to
be recognisable to anyone who has worked a denials queue — high-dollar claims
draw scrutiny; one-day stays invite "this should have been observation"; very
long stays trip outlier review; well-documented complexity protects a claim
until coding complexity starts working against it. **None of these shapes is an
empirical finding.** They are stories rendered as coefficients.

The per-provider quality effect also shifts that provider's probability of
missing an authorization or a document (`provider_quality_prevalence_scale`),
which is what makes provider-level historical rates genuinely predictive — and
therefore what makes the Phase 4 requirement to compute those rates with
out-of-fold or prior-period logic a real leakage risk rather than a formality.

## 5. Denial category mix

Categories are drawn **conditionally on the strongest active mechanism** for that
claim, not from a fixed marginal distribution. A claim denied with a missing
authorization overwhelmingly gets a prior-authorization category; a claim denied
by the continuous terms alone falls to the `baseline` mix.

**CARC handling (CLAUDE.md §3.7).** CARC codes appear as **category labels
only** — the code group is stored as a short string (`"197"`, `"29"`, …) and the
human-readable name beside it is plain language **written for this project**. No
CARC description text is reproduced anywhere in this repository, and no CARC
code set file is downloaded or committed.

**Anchor.** Provider-reported denial-reason rankings (Experian Health *State of
Claims*; Change Healthcare denials index) consistently place prior
authorization, eligibility/coverage, missing documentation, and medical
necessity at the top, with timely filing and duplicates as small tails. The
conditional distributions reproduce that ordering. The probabilities themselves
are design choices.

### 5.1 Service lines, and a real limitation of the source data

The service-line grouping is ten contiguous MS-DRG numeric ranges plus an
UNKNOWN member. **The boundaries are ours**, chosen for this project; they are
not an official CMS MS-DRG-to-MDC mapping, and the repo does not have the MS-DRG
reference file (it is a Phase 3 carry-forward). Ranges are contiguous so that
every numeric DRG lands in exactly one bucket and UNKNOWN can only ever mean
"the source had no DRG", never "fell in a gap".

**Measured limitation (2026-07-22).** The CMS synthetic claims are heavily
concentrated in the aftercare / rehabilitation / other-factors DRG range: DRG
951 alone accounts for about **44%** of claims, and the 940–951 bucket holds
about **46%** of the book, while several clinical buckets hold 1–2%. This is a
property of how the synthetic SOURCE data was generated, not of this simulation,
and no choice of bucket boundaries repairs it.

Two consequences worth being honest about downstream:

1. Service line is a weak stratifier here. Any per-service-line comparison in
   Phase 3 or Phase 5 must show volumes alongside rates, because several buckets
   have too few claims to support a stable rate.
2. The payer × service-line interaction is deliberately placed **only** on
   buckets with enough volume for the cross term to be estimable. Loading an
   interaction onto a 200-claim bucket would manufacture noise and then invite a
   model to learn it.

## 6. Money

| Parameter | Value | Kind |
|---|---|---|
| `allowed_ratio_beta` per payer | Beta means ≈ 0.28 – 0.43 of billed charges | DESIGN CHOICE |
| `partial_denial_share` | 0.28 of denials | DESIGN CHOICE |

**Anchor.** Hospital charges are widely reported to exceed payments by a large
multiple; CMS-published hospital **cost-to-charge ratios** have long sat in the
region of 0.25–0.35, and payment-to-charge ratios for public payers are lower
still than for commercial. The allowed-amount ratios above are drawn to sit in
that neighbourhood, with the commercial archetypes allowed a higher share of
charges than the public ones.

**Invariant enforced by the validation suite, not by hope:**
`sim_paid_amount ≤ sim_allowed_amount ≤ fact_inpatient_claim.clm_tot_chrg_amt`,
and no negative amounts anywhere. Note that `sim_billed_amount` deliberately
**does not exist**: billed charges are a SOURCE value and stay in the SOURCE
fact table, reached by join. Copying it into a `sim_` column would have made a
real value look generated.

## 7. Timelines

The source claims have **no** submission, adjudication, or payment dates — only
service dates. The whole timeline is fabricated, anchored to the SOURCE
discharge date.

| Step | Distribution | Median ≈ | Kind |
|---|---|---|---|
| Discharge → coding complete | Gamma(2.0, 2.0) | 3 days | DESIGN CHOICE |
| Coding → submission | Lognormal(0.50, 0.60) | 1.6 days | DESIGN CHOICE |
| Submission → acknowledgement | Uniform 1–4 days | 2 days | DESIGN CHOICE |
| Acknowledgement → adjudication | Lognormal, payer-specific | 13 – 23 days | see below |
| Adjudication → payment posted | Lognormal(1.40, 0.50) | 4 days | DESIGN CHOICE |
| Denial → appeal filed | Lognormal(2.70, 0.70) | 15 days | DESIGN CHOICE |
| Appeal filed → decision | Lognormal(3.60, 0.50) | 37 days | DESIGN CHOICE |

**Anchors.**
- The Medicare **14-day payment floor** for clean electronic claims (a statutory
  rule, not a benchmark) is why the Medicare FFS archetype's
  acknowledgement-to-adjudication median sits near 13 days. The managed-care
  archetypes are given longer, more dispersed adjudication.
- **Timely filing limits** are contractual and public: Medicare's is 12 months
  from the date of service; commercial limits are commonly 90–180 days. The
  per-payer `timely_filing_days` values (365 / 365 / 180 / 120 / 90) reflect that
  structure.

**Late filing is endogenous, not sprinkled on.** A small tail of claims
(`late_submission_tail_rate` = 3%) stalls in the billing office and picks up a
large extra delay; a claim is flagged late only if its *generated* submission
date actually exceeds its payer's *contractual* filing limit. This means the
short-limit archetypes generate more timely-filing denials than the long-limit
ones without that being coded anywhere as a rule — it falls out of the
interaction between the delay distribution and the limit. It also means the
late-filing feature is legitimately available before submission.

**The late tail is capped at 540 days.** Uncapped, the lognormal produced
submission delays up to 5.3 years — which no billing office would produce, and
which pushed generated submission dates (to mid-2024) well past the source
data's own service-date range (ending 2023-03). The cap sits above every
configured filing limit, so each claim in the tail is still late for its payer
and the mechanism is untouched; only the implausible dates are removed. Measured
effect: per-payer late-filing rates were unchanged to four decimal places.

**Ordering guarantee.** Each step is generated as a non-negative increment on
its predecessor and clamped to be no earlier than it, so the temporal-ordering
validation cannot fail by construction. The check is still run, because a
guarantee that is never tested is a guarantee that quietly breaks.

## 8. Appeals

| Parameter | Value | Kind |
|---|---|---|
| `targets.appeal_rate` | 0.30 – 0.45, point 0.36 | DESIGN CHOICE |
| `targets.appeal_overturn_rate` | 0.35 – 0.60, point 0.47 | DESIGN CHOICE |
| `recovery_fraction_beta` | Beta(9, 2), mean ≈ 0.82 of the disputed amount | DESIGN CHOICE |

**Anchors, and an important caveat about them.**
- Premier Inc.'s 2024 survey reported that a majority — on the order of **half
  or more** — of denials that providers contest are ultimately overturned and
  paid. KFF's Medicare Advantage work similarly found that appealed MA prior
  authorization denials were overturned at a very high rate (the large majority
  of appealed determinations), while KFF's marketplace analysis found that
  **well under 1%** of denied in-network claims were appealed at all.
- Those two facts point in opposite directions and both are real: **consumer**
  appeal rates are near zero, while **provider** appeal rates on hospital
  balances are far higher because a billing office works dollars, not
  grievances. This project simulates the *provider* side, so the 30–45% band
  follows provider-side reporting. Anyone comparing our appeal rate to the KFF
  consumer figure is comparing two different things.

Appeal propensity rises with the disputed balance (`amount_propensity_k`), which
is what makes the Phase 4 "expected net recovery" work-queue score meaningful:
the behaviour being modelled is a finite team choosing which denials to work.

## 9. Operating costs

| Parameter | Value | Kind |
|---|---|---|
| `labor_rate_per_hour` | $32.00 blended, `overhead_multiplier` 1.35 | DESIGN CHOICE |
| Touch minutes per activity | Gamma, means 4 – 42 min | DESIGN CHOICE |
| `clearinghouse_per_submission` | $0.35 | DESIGN CHOICE |
| `records_retrieval_per_appeal` | $12.00 | DESIGN CHOICE |

**Anchors.** Widely cited revenue-cycle figures put the **cost to rework a
single denied claim** in the region of **$25 to roughly $120**, depending on
complexity and on whether an appeal is prepared (MGMA's ~$25-per-appealed-claim
figure sits at the low end; Premier's 2024 reporting of roughly $44 per contested
claim, and higher figures for complex appeals, sit above it). Cost to collect is
commonly benchmarked at **2–3% of net patient revenue**, with strong performers
nearer 2% (HFMA/MGMA benchmarking).

Costs here are built **bottom-up from the workflow event log** — labour rate ×
touch minutes × overhead, plus flat per-event fees — rather than assigned as a
per-claim constant. That is deliberate: it means `sim_operating_costs` and
`sim_workflow_events` reconcile to each other by construction, and the resulting
per-denial rework cost lands inside the cited range as an *output* of the model
rather than as an input. If a calibration change pushes it outside that range,
that is a signal worth investigating, and the validation suite reports the
realized figure for exactly that reason.

**Realized on this subset** (config v0.3.0): mean total cost to collect **$23.98
per claim**, and mean denial rework + appeal cost **$29.88 per denied claim**.
The rework figure sits in the low end of the cited $25–$120 range, which is what
we would expect from a book where most denials are worked once and never
escalated.

**Where our output does not match a cited benchmark, and why.** Cost to collect
comes out near **1% of allowed amounts**, below the commonly benchmarked 2–3% of
net patient revenue. That gap is expected and we are not tuning it away: these
are inpatient facility claims averaging roughly $2,300 allowed, whereas the 2–3%
benchmark is struck against a whole revenue cycle that is dominated in *volume*
by small professional and outpatient claims carrying similar per-claim handling
cost. Inflating the labour parameters to hit the percentage would misrepresent
the per-claim cost, which is the number the Phase 4 expected-net-recovery score
actually consumes. We report the ratio and explain it instead.

## 10. Payer effects are multi-channel, and the ranking will not match `logit_offset`

Worth stating because it looks like an inconsistency and is not. A payer's
denial rate is not a function of its `logit_offset` alone — the payer also sets
prior-authorization prevalence, the contractual filing limit, adjudication
speed, the allowed-amount ratio, and its row of the service-line interaction
matrix. Those channels can reorder the marginals.

Measured example: `COM_REGIONAL` has a *lower* offset than `COM_LARGE` (0.10 vs
0.20) but a *higher* realized denial rate (13.6% vs 12.9%), because its 90-day
filing limit is the shortest of any archetype and produces 2.7× the late-filing
rate (1.90% vs 0.69%).

This is deliberate. A payer feature that reduced to one additive constant would
be trivially learnable and would make the payer × service-line interaction
pointless. Anyone reconciling realized rates against this config should compare
against the full set of per-payer parameters, not the offset column.

## 11. What the validation suite does and does not prove

It proves the generator is **internally consistent and reproducible**:
same seed ⇒ byte-identical output; money invariants hold; events are ordered;
the realized marginals land in their configured bands; the directional claims
(missing authorization raises denial probability, and so on) actually hold in
the generated data with a required margin.

It proves **nothing whatsoever** about realism. A passing validation run means
the simulation did what it was told, not that what it was told is true.

---

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-07-22 | Initial stub (seed, target range, four mechanism ORs, timeline stub) laid down at project setup. `linkage.crosswalk_seed` added by data-engineer under one-commit delegated authority. |
| 0.4.0 | 2026-07-22 | Capped the late-submission tail at 540 days (§7) after a self-audit found generated submission delays reaching 5.3 years and dates past the source period. Mechanism unaffected: per-payer late-filing rates unchanged to four decimals. Documented that payer effects are multi-channel and do not rank by `logit_offset` (§10). |
| 0.3.0 | 2026-07-22 | Service lines rebucketed after measuring the actual DRG distribution (§5.1): 10 contiguous ranges replacing 8 with gaps, splitting the 940–999 block into aftercare/rehab and trauma/HIV/unrelated, and relocating the payer × service-line interactions onto buckets with enough volume to support them. No change to denial rate, mechanism, appeal, timeline or cost parameters. Realized denial rate 12.8%, oracle AUC 0.68. |
| 0.2.0 | 2026-07-22 | Phase 2 build-out by simulation-engineer: simulated payer archetypes; service-line grouping; full mechanism set with prevalences, two explicit interactions, a payer × service-line matrix, and four non-linear terms; conditional denial-category catalog with CARC code groups as labels only; appeal propensity/overturn/recovery parameters; full timeline distributions with endogenous late filing; bottom-up operating-cost parameters; validation acceptance bands. `linkage.crosswalk_seed` reviewed and retained unchanged. |
