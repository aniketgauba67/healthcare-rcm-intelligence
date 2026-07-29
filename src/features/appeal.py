"""Model C's feature store: the denials queue at the moment of triage.

Model C answers a different question at a different time from Model A, and the
difference is the whole reason this is a separate module rather than a flag.

**The boundary is the denial, not the submission.** A denials analyst opening a
worklist can see the remittance advice: what was denied, how much, under which
category and CARC group, and when the payer adjudicated it. Those columns are
forbidden to Model A and legitimately available here
(`docs/simulated_forbidden_columns.md` §5, configured under `model_c` in
config/model.yaml). Everything Model A could see is still available too, because
a fact known before submission is still known after the denial.

**What stays forbidden is narrower but sharper.** All of §1 — the latent
internals, including `sim_appeal_latent_p`. `sim_denial_driver_mechanism`, which
is the generator's account of *why* the denial happened and appears on no
remittance advice anyone has ever read. And every `sim_appeals` column other
than the two targets: the filed and decision dates, the disputed amount and the
level-2 rows all postdate the decision being predicted. The payment date and
`sim_days_to_payment` go too — a payment posting after an appeal is the appeal's
result.

**`sim_appeal_disputed_amount` deserves a note**, because it is forbidden and
its value is available anyway. It equals `sim_denied_amount` exactly on all 967
appealed claims — a one-time manual check, not something this module recomputes
— so the recoverable amount used by the Expected Net Recovery score is read from
the permitted column. That is not a workaround: the denied amount is on the
remittance advice at triage time, and the disputed amount is a fact about an
appeal that has not been filed yet. They coincide numerically; they do not
coincide in time.

The column is now excluded at the QUERY (`extract.py: APPEAL_TARGET_QUERY`). It
was previously selected and then dropped a few lines below the join here, which
is not the same thing: the drop was one refactor from vanishing while the read
stayed, leaving a forbidden column on the frame with nothing objecting. A
forbidden column must never be READ — the `clm_utlztn_day_cnt` ruling.

**Population and selection.** The scoring population is every denied claim
(2,663). The *training* population is the 967 that were actually appealed, and
those two are not the same claims: appealed denials are larger on average
($2,934 vs $2,246 mean denied) and the appeal rate runs from 47.6% of
MEDICAL_NECESSITY denials down to 9.8% of DUPLICATE_CLAIM and 0 of 12
TIMELY_FILING. The model therefore estimates P(overturn | appealed) and is
applied to claims nobody chose to appeal. That is a real and unfixable
limitation of observational appeal data — it is stated in the model card rather
than papered over, and `appeal_selection_summary` below produces the numbers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from src.features.build import MODEL_A_FEATURES, build_model_a_frame
from src.features.extract import fetch_appeal_targets, fetch_denials
from src.features.historical import add_prior_period_rates
from src.features.leakage import assert_no_forbidden_columns, assert_sources_safe, load_model_config
from src.features.spec import FeatureSet, FeatureSpec, assert_frame_matches, validate_feature_set

LABEL = "sim_appeal_outcome"
POSITIVE_OUTCOME = "OVERTURNED"
TIME_COLUMN = "sim_denial_review_date"
RECOVERABLE = "sim_denied_amount"

# Carried for splitting, slicing, ranking and joining. Not features.
PASSTHROUGH = (
    "claim_sk",
    "sim_denial_review_date",
    "sim_submission_date",
    "prvdr_num",
    "sim_appeal_outcome",
    "sim_appeal_recovered_amount",
    "sim_denied_amount",
    "appealed",
)

_DENIAL_CATEGORICALS: tuple[tuple[str, str], ...] = (
    ("sim_denial_type", "FULL or PARTIAL denial, from the remittance advice."),
    ("sim_denial_category", "Denial reason category — the strongest triage signal."),
    ("sim_denial_carc_group", "CARC code group as a category label (CLAUDE.md §3.7)."),
)


def _spec_list() -> tuple[FeatureSpec, ...]:
    """Model A's features, plus what the remittance advice adds."""
    specs: list[FeatureSpec] = list(MODEL_A_FEATURES.specs)
    for name, description in _DENIAL_CATEGORICALS:
        specs.append(
            FeatureSpec(name=name, kind="categorical", description=description, sources=(name,))
        )
    specs.extend(
        [
            FeatureSpec(
                name="log_sim_denied_amount",
                kind="numeric",
                description="log1p of the denied dollars — the amount in dispute. Heavy-tailed "
                "(median $395, max $306k), so the linear model needs the compressed scale.",
                sources=("sim_denied_amount",),
            ),
            FeatureSpec(
                name="sim_denied_share_of_billed",
                kind="numeric",
                description="Denied dollars as a share of the billed charge. Separates a full "
                "denial from a line-level reduction independently of claim size.",
                sources=("sim_denied_amount", "clm_tot_chrg_amt"),
            ),
            FeatureSpec(
                name="sim_allowed_share_of_billed",
                kind="numeric",
                description="Allowed dollars as a share of billed — how much the payer conceded.",
                sources=("sim_allowed_amount", "clm_tot_chrg_amt"),
            ),
            FeatureSpec(
                name="sim_days_to_adjudication",
                kind="numeric",
                description="Submission to adjudication. A fast denial is often an automated "
                "edit; a slow one has usually been looked at.",
                sources=("sim_days_to_adjudication",),
            ),
            FeatureSpec(
                name="sim_denial_review_lag_days",
                kind="numeric",
                description="Adjudication to denial posting — how long the denial sat before it "
                "reached the worklist.",
                sources=("sim_adjudication_date", "sim_denial_review_date"),
            ),
            FeatureSpec(
                name="sim_payer_prior_overturn_rate",
                kind="numeric",
                description="Payer's prior-period appeal overturn rate. The single number a "
                "denials manager quotes when arguing for an appeal.",
                sources=("sim_payer_id", "sim_denial_review_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_appeal_outcome",),
            ),
            FeatureSpec(
                name="sim_payer_prior_appeals",
                kind="numeric",
                description="Appeals behind that rate. Thin here — read the rate with it.",
                sources=("sim_payer_id", "sim_denial_review_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_appeal_outcome",),
            ),
            FeatureSpec(
                name="sim_category_prior_overturn_rate",
                kind="numeric",
                description="Denial category's prior-period overturn rate.",
                sources=("sim_denial_category", "sim_denial_review_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_appeal_outcome",),
            ),
            FeatureSpec(
                name="sim_category_prior_appeals",
                kind="numeric",
                description="Appeals behind the category rate.",
                sources=("sim_denial_category", "sim_denial_review_date"),
                point_in_time="prior_period",
                prior_period_sources=("sim_appeal_outcome",),
            ),
            FeatureSpec(
                name="sim_overall_prior_overturn_rate",
                kind="numeric",
                description="Book-wide prior-period overturn rate — the moving base rate the "
                "others shrink toward.",
                sources=("sim_denial_review_date",),
                point_in_time="prior_period",
                prior_period_sources=("sim_appeal_outcome",),
            ),
        ]
    )
    return tuple(specs)


MODEL_C_FEATURES = FeatureSet(
    model="C",
    specs=_spec_list(),
    label=LABEL,
    time_column=TIME_COLUMN,
    passthrough=PASSTHROUGH,
)


def _engineer(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = frame.copy()
    for column in ("sim_denial_review_date", "sim_adjudication_date"):
        out[column] = pd.to_datetime(out[column])

    billed = out["billed_charge_amt"].astype(float).replace(0, np.nan)
    out["log_sim_denied_amount"] = np.log1p(out["sim_denied_amount"].astype(float))
    out["sim_denied_share_of_billed"] = out["sim_denied_amount"].astype(float) / billed
    out["sim_allowed_share_of_billed"] = out["sim_allowed_amount"].astype(float) / billed
    out["sim_denial_review_lag_days"] = (
        out["sim_denial_review_date"] - out["sim_adjudication_date"]
    ).dt.days

    # Prior-period overturn rates, on the same machinery and the same rules as
    # Model A's denial rates: strictly prior window, embargoed, shrunk toward the
    # moving book rate. The outcome is binary-coded here rather than in SQL so the
    # UPHELD/OVERTURNED strings never reach a model as a feature.
    # The rates are computed over the WHOLE denied population, not the appealed
    # subset, because the queue has to score claims nobody appealed. Un-appealed
    # rows carry a NaN outcome, and the running sums count non-null values only,
    # so they contribute no history while still receiving a rate. Restricting the
    # frame to appealed rows instead would leave every un-appealed claim — two
    # thirds of the queue — with a null feature.
    # The embargo is the APPEAL embargo, not Model A's 60-day posting cycle. An
    # appeal outcome is known far later than a denial: the published Medicare
    # Part A/B redetermination timetable allows 120 days to file and 60 more for
    # the contractor's decision, so an outcome is only certainly on the books
    # 180 days after the denial posted. Using 60 here would let a claim's rate
    # be built from appeals that had not been decided yet.
    prior = config["feature_store"]["prior_period"]
    out["_overturned"] = (out[LABEL] == POSITIVE_OUTCOME).astype(float)
    out.loc[out[LABEL].isna(), "_overturned"] = np.nan
    rated = add_prior_period_rates(
        out,
        date_column=TIME_COLUMN,
        outcome_column="_overturned",
        definitions={"sim_payer": "sim_payer_id", "sim_category": "sim_denial_category"},
        embargo_days=int(prior["appeal_embargo_days"]),
        smoothing=float(prior["smoothing"]),
    )
    carried = [
        "sim_payer_prior_denial_rate",
        "sim_payer_prior_claims",
        "sim_category_prior_denial_rate",
        "sim_category_prior_claims",
        "sim_overall_prior_denial_rate",
    ]
    renamed = {
        "sim_payer_prior_denial_rate": "sim_payer_prior_overturn_rate",
        "sim_payer_prior_claims": "sim_payer_prior_appeals",
        "sim_category_prior_denial_rate": "sim_category_prior_overturn_rate",
        "sim_category_prior_claims": "sim_category_prior_appeals",
        "sim_overall_prior_denial_rate": "sim_overall_prior_overturn_rate",
    }
    # Model A's book DENIAL rate arrives on the merged frame under exactly the
    # name `add_prior_period_rates` produces for the book OVERTURN rate. Renaming
    # before the join is what keeps them apart — both are legitimate features
    # here, they answer different questions, and a silent overwrite would leave
    # the model reading one while the audit trail named the other.
    rates = rated.loc[:, carried].rename(columns=renamed)
    collisions = sorted(set(rates.columns) & set(out.columns))
    if collisions:  # pragma: no cover - guards the rename above
        raise ValueError(f"appeal-side rates would overwrite Model A features: {collisions}")
    out = out.join(rates)
    return out.drop(columns=["_overturned"])


def build_model_c_frame(engine: Engine, config: dict | None = None) -> pd.DataFrame:
    """Every denied claim, with its triage-time facts and (where it exists) its outcome.

    Returns the full denied population, not just the appealed subset: the work
    queue has to rank claims nobody has appealed yet, which is the entire point.
    `appealed` marks the rows that carry a label and can be trained or scored
    against.
    """
    cfg = config or load_model_config()
    validate_feature_set(MODEL_C_FEATURES, cfg)
    assert_sources_safe(
        {
            spec.name: spec.sources
            for spec in MODEL_C_FEATURES.specs
            if spec.point_in_time == "direct"
        },
        model="C",
        config=cfg,
    )

    base = build_model_a_frame(engine, cfg)
    denials = fetch_denials(engine)
    targets = fetch_appeal_targets(engine)
    if targets["claim_sk"].duplicated().any():
        raise ValueError("more than one level-1 appeal per claim; the grain is not what it claims")

    # No drop here any more: `sim_appeal_disputed_amount` is never selected by
    # APPEAL_TARGET_QUERY, so there is nothing to remove. Enforcing it at the
    # query means a future edit to this join cannot silently readmit it.
    merged = base.merge(denials, on="claim_sk", how="inner").merge(
        targets, on="claim_sk", how="left"
    )
    if len(merged) != len(denials):
        raise ValueError(f"denial grain broken: {len(denials)} denials became {len(merged)} rows")
    merged["appealed"] = merged[LABEL].notna()

    engineered = _engineer(merged, cfg)
    columns = [*MODEL_C_FEATURES.passthrough, *MODEL_C_FEATURES.names]
    frame = engineered.loc[:, columns].copy()
    assert_frame_matches(frame.columns, MODEL_C_FEATURES)
    assert_no_forbidden_columns(
        MODEL_C_FEATURES.names, model="C", config=cfg, context="Model C feature store"
    )
    return frame.sort_values([TIME_COLUMN, "claim_sk"]).reset_index(drop=True)


def labels(frame: pd.DataFrame) -> pd.Series:
    """1 where the appeal was overturned. Only meaningful on appealed rows."""
    return (frame[LABEL] == POSITIVE_OUTCOME).astype(int)


def appeal_selection_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Who gets appealed, by category — the evidence for the selection caveat.

    The model is fitted on appealed denials and scored on all of them, so the
    size of that gap is not a footnote; it is the main thing a reader needs in
    order to know how far to trust a score on an un-appealed claim.
    """
    rows = (
        frame.groupby("sim_denial_category", observed=True)
        .agg(
            denials=("claim_sk", "size"),
            appealed=("appealed", "sum"),
            appeal_rate=("appealed", "mean"),
            mean_denied_amount=("sim_denied_amount", "mean"),
        )
        .reset_index()
    )
    overturned = (
        frame.loc[frame["appealed"]]
        .assign(won=lambda d: labels(d))
        .groupby("sim_denial_category", observed=True)["won"]
        .mean()
        .rename("overturn_rate_given_appealed")
    )
    out = rows.join(overturned, on="sim_denial_category")
    out["overturn_rate_given_appealed"] = out["overturn_rate_given_appealed"].where(
        out["appealed"] > 0
    )
    out["no_training_support"] = out["appealed"] == 0
    return out.sort_values("denials", ascending=False).reset_index(drop=True)
