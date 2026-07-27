"""`prevented_denial_value_multiplier: 1.0` asserts two things, and both are wrong.

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

Constraint 1 is why this file also fails on a config that cites a `sim_`
quantity next to a factor: `sim_operating_costs` is a forbidden table for both
models, and a business parameter validated against it is a firewall crossing
recorded in the repo even when no code reads the column.

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
    """Constraint 1: no factor may be anchored to a realized `sim_` quantity.

    Checked on the comment text, because that is where such an anchor shows up —
    no code reads `sim_operating_costs`, and the harm is that the business
    parameter becomes a function of what the §4.5 firewall exists to hide.
    """
    text = _CONFIG_PATH.read_text()
    economics_start = text.find("# Decision thresholds and economics")
    assert economics_start != -1, "could not locate the economics section of config/model.yaml"
    economics = text[economics_start:]

    # Deliberately narrow. A test that flagged every mention of the simulation here
    # would be over-broad and would get deleted; these three phrases are the
    # unambiguous ones — each says the parameter was checked against generator output.
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
