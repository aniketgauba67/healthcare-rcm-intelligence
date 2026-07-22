# Forbidden and Permitted Columns of the Simulated Layer

**Audience: ml-engineer.** CLAUDE.md §4.5 forbids the ml-engineer from reading
`src/simulation/` internals — the feature store must be built as if the data
were real. This document exists so that separation costs nothing: it is the
authoritative, generator-side statement of which simulated columns are available
at scoring time and which are not, published here rather than requiring anyone
to open the generator.

Copy the forbidden names into `config/model.yaml: forbidden_features`. That file
is ml-engineer's; simulation-engineer does not edit it.

The rule is a single question: **would a biller know this at the moment before
the claim is submitted?** Everything produced by the generator after the denial
is drawn is post-submission, and so is every date after the submission date.

---

## 1. FORBIDDEN — latent generator internals (never a feature, in any model)

These are not observable in any real revenue cycle at any time. They exist
purely so the simulation can be validated (docs/assumptions.md §10). Using one
as a feature would produce a model that scores brilliantly and means nothing.

| Column | Table | What it is |
|---|---|---|
| `sim_latent_p` | `sim_claim_adjudication` | The true pre-noise denial probability the outcome was drawn from. |
| `sim_provider_quality_latent` | `sim_claim_adjudication` | The provider's latent "clean claim" quality draw. |
| `sim_label_noise_applied` | `sim_claim_adjudication` | Whether this row's label was deliberately flipped. Reveals the label. |
| `sim_appeal_latent_p` | `sim_appeals` | The true overturn probability the appeal outcome was drawn from. |

`sim_provider_quality_latent` deserves a specific warning. A provider's
historical clean-claim rate *is* a legitimate feature, and it is predictive
precisely because this latent value drives it. But the latent value itself is
the answer key. Compute the historical rate from observed prior-period outcomes
with out-of-fold or prior-period logic (CLAUDE.md §4.2); never read this column.

## 2. FORBIDDEN for Model A (pre-submission denial risk) — post-submission columns

### Outcome
`sim_denial_flag`, `sim_denial_type`, `sim_denial_category`,
`sim_denial_carc_group`, `sim_denial_driver_mechanism`

`sim_denial_driver_mechanism` is a direct statement of which mechanism caused
the denial. It is the label's explanation and is strictly more informative than
the label.

### Money
`sim_allowed_amount`, `sim_paid_amount`, `sim_patient_responsibility_amount`,
`sim_contractual_adjustment_amount`, `sim_denied_amount`

All of these are adjudication results. Note that `sim_denied_amount > 0` is
exactly equivalent to the label.

### Dates and durations after submission
`sim_ack_date`, `sim_adjudication_date`, `sim_denial_review_date`,
`sim_payment_date`, `sim_days_to_adjudication`, `sim_days_to_payment`

`sim_denial_review_date` is non-null if and only if the claim was denied — a
null-indicator on it reconstructs the label perfectly.

### Whole tables
- **`sim_appeals`** — every column. Appeals only exist for denied claims, so the
  mere presence of a row leaks the label.
- **`sim_operating_costs`** — every column. Costs are accumulated from the full
  workflow, including denial rework and appeal preparation;
  `sim_denial_rework_cost > 0` implies a denial.
- **`sim_workflow_events`** — every event with `sim_event_ts` at or after the
  `CLAIM_SUBMITTED` event for that claim. See §4 for the safe subset.

## 3. PERMITTED for Model A — genuinely pre-submission

Everything below is knowable before the claim goes out the door.

| Column | Table |
|---|---|
| `sim_payer_id` | `sim_claim_adjudication`, `sim_authorization_eligibility` |
| `sim_service_line_id` | `sim_claim_adjudication` |
| `sim_coded_date`, `sim_submission_date` | `sim_claim_adjudication` |
| `sim_filing_limit_days`, `sim_days_service_to_submission`, `sim_late_filing_flag` | `sim_claim_adjudication` |
| all columns | `sim_authorization_eligibility` |
| all columns | `sim_documentation_coding` |
| all columns | `sim_payer`, `sim_service_line` (config-only dimensions) |

Two notes on the ones that look borderline:

- **`sim_late_filing_flag`** is derived by comparing the claim's own submission
  date against the payer's contractual filing limit. Both are known at
  submission, so the flag is legitimately available then — it is not a
  post-hoc payer determination.
- **`sim_submission_date`** is permitted as the point-in-time anchor and as the
  basis for time-based features, but it is also the boundary itself. Any feature
  computed from it must not reach forward past it.

## 4. `sim_workflow_events` — the safe subset for Model A

Only events strictly at or before submission:

| Event type | Safe for Model A |
|---|---|
| `CODING_COMPLETE` | yes |
| `CLAIM_SUBMITTED` | yes (the boundary itself) |
| `PAYER_ACKNOWLEDGED` | no |
| `ADJUDICATED` | no |
| `DENIAL_POSTED`, `DENIAL_REVIEWED` | no — presence implies the label |
| `PAYMENT_POSTED` | no |
| `APPEAL_FILED`, `APPEAL_DECISION`, `APPEAL_RECOVERY_POSTED` | no — presence implies the label |
| `CLAIM_CLOSED` | no |

The robust filter is timestamp-based rather than type-based: keep events whose
`sim_event_ts` is at or before that claim's `CLAIM_SUBMITTED` timestamp. That
stays correct if new event types are added later.

Note also that `sim_touch_minutes` aggregated across a claim's full history
encodes rework effort and therefore the label. Aggregate only over the safe
subset.

## 5. Model C (appeal success) — a different boundary

Model C predicts, at the moment a denial has been posted and is being triaged,
whether an appeal would succeed. Its boundary is the **denial**, not the
submission, so the Model A forbidden list does not apply unchanged:

- **Permitted:** everything in §3, plus the denial outcome columns, the money
  columns, and adjudication dates up to `sim_denial_review_date`. Those are what
  a denials analyst is looking at.
- **Still forbidden:** every column in §1 (the latent internals, including
  `sim_appeal_latent_p`), and everything in `sim_appeals` other than the target
  itself — the filed/decision dates, the recovered amount, and the level-2 rows
  all postdate the decision being predicted.
- `sim_appeal_recovered_amount` is the Model C regression target's own input;
  treat it as a label, never a feature.

## 6. A caution on `clm_id` and `claim_sk`

Neither is a `sim_` column — they are the warehouse's SOURCE degenerate key and
DERIVED surrogate key. Neither should be a feature. `claim_sk` in particular is
assigned in source-file order, so it correlates with time and will act as a
smuggled date feature if a model is allowed to see it.

---

Maintained by simulation-engineer, in the same commit as any change to the
simulated schema (CLAUDE.md §3.3). If a column appears in the warehouse that is
not listed here, treat it as forbidden until this document is updated.
