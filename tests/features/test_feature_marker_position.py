"""§3.2 literally: on a simulated derivative the `sim_` marker LEADS.

This is a STRICTER companion to qa's `tests/leakage/test_feature_prefix_survival.py`
and does not replace or weaken it. That gate fires on the ABSENCE of the marker,
which is the blocking property — an unmarked `overall_prior_denial_rate` sitting
beside genuine CMS columns is indistinguishable from a real Medicare book rate,
and that one shipped in the committed matrix for three commits. Where the marker
is present but infixed the property already holds, so qa's gate correctly stays
quiet; this file holds the remaining difference between "the reader is told" and
what CLAUDE.md §3.2 actually says: "every simulated table and column name is
prefixed `sim_`".

WHY IT IS WORTH A TEST NOW, when it twice was not. `log_sim_denied_amount` was
ruled a naming preference twice, both times on a MEASURED exposure of zero: no
committed Model C matrix, `models_artifacts/` gitignored, no SHAP for Model C, so
the name reached no artifact a reader could open. That measurement still held when
this file was written — the name is on no published surface today. What changed is
that it was the LAST infixed name in either feature set (Model A: 39 specs, zero
offenders; Model C: 52 specs, one), so renaming it costs a single feature and buys
a rule with no exception list. An exception is free only while every future author
remembers it, and Phase 5 adds a dashboard, an API and a demo extract — three new
ways for a feature name to become a column header — each of which would otherwise
have to remember.

SCOPE AND LIMIT. This checks DECLARATIONS, not the frame, so it runs on a clean
clone with no database. That is sufficient because the declaration is enforced
against the real frame elsewhere: `src/features/spec.py: assert_frame_matches`
refuses a frame whose columns do not match the declared names, so a spec renamed
without renaming the engineered column fails every live build immediately.

A feature is a simulated derivative if any DECLARED source — including
`prior_period_sources` — is a `sim_` column. Names of the SOURCE and DERIVED
features are not this test's business: `log_billed_charge_amt` reads a genuine CMS
charge and must NOT carry the marker.
"""

from __future__ import annotations

import pytest

from src.features.appeal import MODEL_C_FEATURES
from src.features.build import MODEL_A_FEATURES
from src.features.spec import FeatureSet, FeatureSpec


def _marker_not_leading(feature_set: FeatureSet) -> list[tuple[str, tuple[str, ...]]]:
    """Simulated derivatives whose name does not START with `sim_`.

    Catches both the unmarked name (which qa's gate also catches) and the infixed
    one (which it deliberately does not).
    """
    offenders: list[tuple[str, tuple[str, ...]]] = []
    for spec in feature_set.specs:
        sources = tuple(spec.sources) + tuple(spec.prior_period_sources)
        if any(source.startswith("sim_") for source in sources) and not spec.name.startswith(
            "sim_"
        ):
            offenders.append((spec.name, sources))
    return offenders


@pytest.mark.parametrize(
    ("label", "feature_set"),
    [("Model A", MODEL_A_FEATURES), ("Model C", MODEL_C_FEATURES)],
)
def test_every_simulated_derivative_leads_with_the_marker(
    label: str, feature_set: FeatureSet
) -> None:
    offenders = _marker_not_leading(feature_set)
    assert not offenders, (
        f"{label}: {len(offenders)} feature(s) are computed from SIMULATED columns but do not "
        "LEAD with `sim_`, so §3.2 is satisfied in spirit at best. There is no exception list "
        "here and adding one is the thing to argue about, not to do quietly — the value of "
        "this rule is that a reader never has to know which names are the exceptions:\n  "
        + "\n  ".join(f"{name}  <-  {', '.join(sources)}" for name, sources in offenders)
        + "\nPut the marker first and leave the rest of the name alone "
        "(`log_sim_denied_amount` -> `sim_log_denied_amount`), which keeps the base column "
        "legible and matches how the work-queue columns were renamed."
    )


def test_the_check_distinguishes_an_infixed_marker_from_a_leading_one() -> None:
    """The negative control, because presence and position are one character apart.

    Without this, a check that had silently degraded to `"sim_" in name` — which is
    qa's gate, and the easy thing to write — would pass the suite above unchanged
    and this file would be testing nothing it does not already test.
    """
    infixed = FeatureSet(
        model="C",
        specs=(
            FeatureSpec(
                name="log_sim_denied_amount",
                kind="numeric",
                description="The pre-Phase-5 name, replayed.",
                sources=("sim_denied_amount",),
            ),
        ),
        label="sim_appeal_outcome",
        time_column="sim_denial_review_date",
    )
    assert _marker_not_leading(infixed) == [("log_sim_denied_amount", ("sim_denied_amount",))]

    leading = FeatureSet(
        model="C",
        specs=(
            FeatureSpec(
                name="sim_log_denied_amount",
                kind="numeric",
                description="The Phase 5 name.",
                sources=("sim_denied_amount",),
            ),
        ),
        label="sim_appeal_outcome",
        time_column="sim_denial_review_date",
    )
    assert _marker_not_leading(leading) == []


def test_a_genuine_source_derivative_is_left_alone() -> None:
    """`log_billed_charge_amt` reads a real CMS charge. Marking it would be a lie."""
    unsimulated = FeatureSet(
        model="A",
        specs=(
            FeatureSpec(
                name="log_billed_charge_amt",
                kind="numeric",
                description="log1p of the CMS total charge.",
                sources=("clm_tot_chrg_amt",),
            ),
        ),
        label="sim_denial_flag",
        time_column="sim_submission_date",
    )
    assert _marker_not_leading(unsimulated) == []
