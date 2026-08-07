"""§3.2 must survive feature engineering, not just the warehouse.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). Do not delete it to go green.

STATUS: GREEN as of ml-engineer-4's 0a7960a. This gate was RED when written
against c565ea3 and is kept as a regression guard, not deleted — the violation it
caught had shipped silently in the committed matrix since cd3e30c precisely
because nothing was checking.

HISTORY, because the disagreement is worth preserving. qa-reviewer-p11 reported
four offending names against c565ea3; team-lead could not reproduce it and
challenged it, having measured a tree where `sim_overall_prior_denial_rate`
already existed. Both measurements were correct and of DIFFERENT COMMITS —
ml-engineer-4's fix (0a7960a) landed between the report and the challenge, and
team-lead's cited line numbers (historical.py:210, appeal.py:155) are 0a7960a's,
where c565ea3 has historical.py:202 unprefixed. Three of the four names were real
and are now fixed; the fourth (`log_sim_denied_amount`) was ruled a naming
preference and this gate no longer fires on it — see below.

docs/project_rules.md §3.2: "Every simulated table and column name is prefixed `sim_`." The
warehouse satisfies that and Phase 3 proved it — data-engineer-p4 renamed the
crosswalk columns and qa-reviewer-p10 built an end-state guard that fails if a
column that carried `sim_` before a run loses it.

Nothing was guarding the FEATURE layer, and that is where the rule is easiest to
lose, because a feature is a NEW column with a NEW name chosen by hand. A rate
computed from `sim_denial_flag` is a simulated quantity no matter what it is
called; calling it `overall_prior_denial_rate` does not make the denials real, it
just stops the reader being told they are not.

WHY THIS IS A §1 PROBLEM AND NOT A STYLE NIT. Judge the artifacts ALONE, without
the prose — which is the standard team-lead set for the Phase 4 honesty pass. A
SHAP global-importance plot lists feature names down an axis. A reader who sees

    sim_payer_prior_denial_rate
    sim_provider_prior_denial_rate
    overall_prior_denial_rate

reasonably concludes that the first two are simulated and the third is a real
book rate — the inconsistency actively teaches them to read the prefix as
meaningful and then withholds it on one row. It is worse than no convention. The
same names reach `src/models/explain.py`'s analyst reason codes (BOOK-01 is
described as "Book-wide denial rate at the time of submission", with nothing
saying whose book or that the denials in it are generated) and are headed for the
Phase 5 dashboard.

That the sibling rates DO carry the prefix is what makes this a defect rather
than a design choice: the intent is already expressed in the code, and these
names are inconsistent with it.

SCOPE. This checks a feature's DECLARED sources, which is the right instrument
here — `src/features/spec.py` already requires every feature to declare them and
refuses a frame that does not match the declaration, so the declaration is
load-bearing rather than a comment. A feature whose sources include any `sim_`
column is a simulated derivative and must say so in its own name.
"""

from __future__ import annotations

import pytest

from src.features.appeal import MODEL_C_FEATURES
from src.features.build import MODEL_A_FEATURES
from src.features.spec import FeatureSet


def _sim_derived_without_any_marker(feature_set: FeatureSet) -> list[tuple[str, tuple[str, ...]]]:
    """Features built from a `sim_` column whose own name carries NO `sim_` marker at all.

    TEAM-LEAD RULING 2026-07-28, and this test implements it rather than arguing
    with it. The blocking property is that a reader can see the provenance in the
    name. A name with `sim_` INFIXED — `log_sim_denied_amount` — carries the
    marker, keeps the base column legible, and nobody would read it as a real
    Medicare quantity; team-lead called that a naming preference at most, and qa
    agrees on the merits. A name with no marker at all —
    `overall_prior_denial_rate`, which shipped in the committed matrix from
    cd3e30c until ml-engineer-4 fixed it in 0a7960a — is the real failure, because
    it is indistinguishable from the genuine CMS SOURCE columns beside it.

    So the guard fires on ABSENCE of the marker, not on its position. That keeps
    it aimed at the property §3.2 exists to protect instead of at a spelling.
    """
    offenders: list[tuple[str, tuple[str, ...]]] = []
    for spec in feature_set.specs:
        sources = tuple(spec.sources or ()) + tuple(
            getattr(spec, "prior_period_sources", None) or ()
        )
        if any(source.startswith("sim_") for source in sources) and "sim_" not in spec.name:
            offenders.append((spec.name, sources))
    return offenders


@pytest.mark.parametrize(
    ("label", "feature_set"),
    [("Model A", MODEL_A_FEATURES), ("Model C", MODEL_C_FEATURES)],
)
def test_a_feature_derived_from_a_simulated_column_keeps_the_prefix(
    label: str, feature_set: FeatureSet
) -> None:
    offenders = _sim_derived_without_any_marker(feature_set)
    assert not offenders, (
        f"{label}: {len(offenders)} feature(s) are derived from SIMULATED columns and carry no "
        "`sim_` marker anywhere in the name, so §3.2's provenance is lost at the feature "
        "layer. Their sibling rates in the same builder do carry it, so these are "
        "inconsistent with the code's own intent:\n  "
        + "\n  ".join(f"{name}  <-  {', '.join(sources)}" for name, sources in offenders)
        + "\nRename them (e.g. `sim_overall_prior_denial_rate`) and update the feature spec, "
        "the reason-code map in src/models/explain.py, and the model card. These names reach "
        "the SHAP plots and the Phase 5 dashboard, where a reader sees the name and nothing "
        "else — and next to a prefixed sibling an unprefixed name reads as a real quantity."
    )


def test_the_prefix_convention_is_actually_applied_somewhere() -> None:
    """Control: proves the test above is measuring a real convention, not an empty set.

    A guard that would pass on a feature store using no prefixes at all is not
    evidence of anything. If this ever fails, the convention has collapsed and the
    test above is silent for the wrong reason.
    """
    prefixed = [spec.name for spec in MODEL_A_FEATURES.specs if spec.name.startswith("sim_")]
    assert len(prefixed) >= 20, (
        "fewer than 20 Model A features carry the `sim_` prefix, so the convention this file "
        f"enforces is not actually in use: {prefixed}"
    )
