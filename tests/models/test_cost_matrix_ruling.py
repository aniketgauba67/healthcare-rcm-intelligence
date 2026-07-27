"""`prevented_denial_value_multiplier: 1.0` asserts two things, and both are wrong.

QA-AUTHORED REVIEW GATE. This file lives in tests/models/, which is
ml-engineer's directory, but it is a reviewer's gate on a team-lead ruling and is
qa's under the 2026-07-27 test-ownership ruling. It is expected to be RED until
the ruling is met. Satisfy the ruling; do not edit or delete this file to make
the suite green — raise it with qa-reviewer or team-lead if you think the gate
itself is wrong.

TEAM-LEAD RULING (tasks.md, Phase 4, 2026-07-27): the configured cost matrix is
degenerate — a $25 review against a mean $3,800 at stake at a 12% denial rate
makes reviewing an average claim worth ~$456, so the cost-optimal threshold flags
99% of the queue. The fault is CONCEPTUAL, not a bad constant. A multiplier of
1.0 asserts BOTH that a review prevents the denial with certainty AND that a
denial costs the full claim value. The second is the worse error: denials are
appealed and substantially overturned, so the real loss is rework cost +
unrecovered fraction + carrying cost of delay, not the claim.

RULING: decompose the multiplier into named factors —
    P(review prevents | flagged and worked) x (share of claim value permanently
    lost when a denial occurs)
— each set from PUBLISHED benchmarks with citations, labelled DESIGN CHOICE,
mirroring docs/assumptions.md.

TWO HARD CONSTRAINTS, both of which this file also checks:
 1. Do NOT derive the factors from the generator's realized overturn/rework
    rates. That reaches through the §4.5 firewall to set a business parameter and
    makes the operating point a function of what the firewall exists to hide.
 2. Do NOT choose factors to produce a pleasing flagged share. Report whatever
    threshold falls out, even if still degenerate — a cost matrix
    reverse-engineered from a desirable operating point is the same failure as
    tuning a model to beat a baseline.

Constraint 1 is why this file also fails on a config that anchors a factor to a
generator-REALIZED quantity.

On the route, carefully, because the accusation available here is the most
serious one in this project. `appeal_processing_cost_usd` was anchored against
"the simulation's own realized ... $29.88 per DENIED claim", and qa-reviewer-p10
confirmed on live PG that avg(sim_denial_rework_cost + sim_appeal_cost) over the
2,663 denied claims is 29.8818 — the match is not coincidence. It does NOT follow
that a forbidden table was queried, and qa's first write-up wrongly said so. The
figure is PUBLISHED in two places ml-engineer is expected to read — tasks.md, the
Phase 2 record, and docs/assumptions.md — and §4.5 firewalls `src/simulation/`,
not the board. The board is the likely route. What is established is that a
generator-realized figure influenced a business parameter; by what path is not,
and this file asserts nothing about that.

The finding survives the correction, because the ruling is about the anchoring
and not about the route: even as a consistency remark, it makes the operating
point a function of the layer the firewall exists to hide.

A HOLE THIS EXPOSES, for Phase 5, and nobody's fault: docs/assumptions.md and
tasks.md republish generator-realized values to an agent that is firewalled from
the generator. The firewall is enforced on source files and leaks through
documentation.

The sensitivity sweep is NOT a substitute for the decomposition. It shows how
much of the answer is the assumption, which is the honest representation of a
guess; the ruling asked for the guess to be made of named, cited parts.
"""

from __future__ import annotations

import pathlib
import re

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "model.yaml"

_BARE_MULTIPLIER = "prevented_denial_value_multiplier"

# Any of these spellings counts as a named factor; the ruling fixed the meaning,
# not the key name.
_PREVENTION_FACTOR = re.compile(r"prevent", re.IGNORECASE)
_LOSS_FACTOR = re.compile(r"loss|unrecovered|permanent|written_off|writeoff", re.IGNORECASE)


def _config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text())


def test_the_multiplier_is_decomposed_into_named_factors() -> None:
    cost_matrix = _config()["cost_matrix"]
    keys = list(cost_matrix)

    prevention = [key for key in keys if _PREVENTION_FACTOR.search(key) and key != _BARE_MULTIPLIER]
    loss = [key for key in keys if _LOSS_FACTOR.search(key)]

    assert prevention and loss, (
        "cost_matrix still carries an undecomposed value multiplier. "
        f"keys present: {keys}. The team-lead ruling requires two named factors — "
        "P(review prevents | flagged and worked) and the share of claim value permanently "
        "lost when a denial occurs — each cited to a published benchmark and labelled "
        "DESIGN CHOICE. If honest factors still flag ~99% of the queue, that is a finding "
        "about this problem's economics and must be reported plainly; it is not a reason "
        "to leave the multiplier at 1.0."
    )

    for key in prevention + loss:
        value = cost_matrix[key]
        assert isinstance(value, (int, float)), f"{key} must be a number, got {value!r}"
        assert 0.0 < float(value) <= 1.0, (
            f"{key} = {value} is not a probability/share. Both factors are bounded by one; "
            "a factor at exactly 1.0 re-asserts the certainty the ruling rejected."
        )


def test_the_factors_are_not_validated_against_the_generator() -> None:
    """Constraint 1: no factor may be anchored to a generator-realized quantity.

    Checked on the comment text, because that is where such an anchor shows up.
    No code reads `sim_operating_costs`, and this test takes no position on how
    the figure was obtained — the published board is the likely route. The harm
    is the anchoring itself: it makes the business parameter a function of the
    layer the §4.5 firewall exists to hide, whichever way it was read.
    """
    text = _CONFIG_PATH.read_text()
    economics_start = text.find("# Decision thresholds and economics")
    assert economics_start != -1, "could not locate the economics section of config/model.yaml"
    economics = text[economics_start:]

    # Deliberately narrow. A test that flagged every mention of the simulation here
    # would be over-broad and would get deleted; these three phrases are the
    # unambiguous ones — each anchors a parameter to generator-realized output.
    offenders = [
        line.strip()
        for line in economics.splitlines()
        if re.search(r"simulation's own|\brealized\b|sim_operating_costs", line)
    ]
    assert not offenders, (
        "a business parameter is being reconciled against the generator's realized output, "
        "which constraint 1 of the cost-matrix ruling forbids:\n  "
        + "\n  ".join(offenders)
        + "\nCite the published benchmark and stop there. Whether the generator happens to "
        "agree is not evidence about the real world, and checking makes the operating point "
        "a function of the layer the firewall exists to hide."
    )
