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

import pytest
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


# A phrase that attributes a quantity to OUR generated layer rather than to a
# published source. "this warehouse" and "simulated appeals" are here because the
# defect is not limited to the word "realized" — see the second instance below.
_GENERATOR_VOICE = re.compile(
    r"simulation's|the simulation\b|generator's|the generator\b|realized|"
    r"this warehouse|simulated (?:appeals|claims|denials)|sim_[a-z_]+",
    re.IGNORECASE,
)

# Cross-references carry digits that are not measurements. Stripping them is what
# lets the numeric test below stay simple without firing on "CLAUDE.md §4.5".
_REFERENCE_NOISE = re.compile(r"§\s*\d+(?:\.\d+)*|CLAUDE\.md|[\w/]+\.(?:md|yaml|py)\b")

_NUMERIC = re.compile(r"\d+(?:\.\d+)?")


def _declared_values(config: dict) -> set[float]:
    """Every number this config actually sets as a parameter.

    The discriminator for the check below. A generator-voice sentence that quotes
    only the config's OWN parameter values discloses nothing about the generated
    layer; one that quotes a number from somewhere else, in that voice, has taken
    it from the layer the firewall hides.
    """
    found: set[float] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            found.add(float(node))

    walk(config)
    return found


def _generator_anchors(text: str, declared: set[float]) -> list[str]:
    """Sentences that speak a FOREIGN number in the generator's voice.

    Third iteration of this check, and the two it replaces both failed the same
    way — they could not tell an anchor from a statement ABOUT an anchor:

      1. LINE-based: reported a fragment of the "NEITHER FACTOR IS DERIVED FROM
         THE SIMULATION" disclaimer, because YAML comments wrap mid-sentence.
      2. SENTENCE-based + "contains any digit": reported ml-engineer-4's REMOVAL
         NOTE, "A previous version of this comment reconciled $45 against the
         simulation's own realized cost per denied claim." That sentence deletes
         the anchor and discloses no generator value — $29.88 is gone — and $45
         is this config's own `appeal_processing_cost_usd`.

    Both misfires push a reader toward deleting the honest sentence, which is the
    opposite of what the ruling wants. Team-lead's standing preference is on the
    board: "Recorded rather than scrubbed — an honest record of a near-miss is
    worth more than a clean-looking config."

    So the offence is stated as the property the ruling actually protects: NO
    GENERATOR-REALIZED VALUE MAY BE READABLE FROM THIS FILE. A number that the
    config itself sets is not such a value; any other number in generator voice
    is. That keeps both known true positives red (29.88; 965 of 967) and lets a
    removal note say what was removed without re-committing the offence.
    """
    offenders = []
    for sentence in _economics_prose(text):
        if not _GENERATOR_VOICE.search(sentence):
            continue
        cleaned = _REFERENCE_NOISE.sub("", sentence)
        foreign = [number for number in _NUMERIC.findall(cleaned) if float(number) not in declared]
        if foreign:
            offenders.append(f"{sentence}   [foreign numbers: {', '.join(foreign)}]")
    return offenders


def _economics_prose(text: str) -> list[str]:
    """The economics section's comment prose, as sentences.

    Sentence-level rather than line-level because YAML comments wrap mid-sentence,
    and the previous line-based version of this check reported a fragment of a
    DISCLAIMER ("NEITHER FACTOR IS DERIVED FROM THE SIMULATION. The generator's
    realized") as an offence. A test that cannot tell an anchor from a denial that
    there is an anchor is worse than no test: the fix a reader reaches for is
    deleting the honest sentence.
    """
    start = text.find("# Decision thresholds and economics")
    assert start != -1, "could not locate the economics section of config/model.yaml"

    prose = " ".join(
        line.strip().lstrip("#").strip()
        for line in text[start:].splitlines()
        if line.strip().startswith("#")
    )
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", prose) if sentence.strip()]


def test_the_factors_are_not_validated_against_the_generator() -> None:
    """Constraint 1: no parameter may be anchored to a generator-realized quantity.

    Checked on the comment text, because that is where such an anchor shows up.
    No code reads `sim_operating_costs`, and this test takes no position on how
    the figure was obtained — the published board is the likely route. The harm
    is the anchoring itself: it makes the business parameter a function of the
    layer the §4.5 firewall exists to hide, whichever way it was read.

    The offence is a FOREIGN number spoken in the generator's voice — foreign
    meaning "not a value this config itself sets". Saying "neither factor is
    derived from the simulation" is the opposite of the offence and must not be
    flagged; nor must a note recording that an anchor was REMOVED, so long as it
    does not repeat the number. Saying "the simulation's own realized cost is
    $29.88" is the offence whether or not the sentence goes on to call it
    non-load-bearing. See `_generator_anchors` for why this is the third
    formulation of the check.
    """
    offenders = _generator_anchors(_CONFIG_PATH.read_text(), _declared_values(_config()))
    assert not offenders, (
        "a business parameter is reconciled against a quantity measured on our own generated "
        "layer, which constraint 1 of the cost-matrix ruling forbids — team-lead ruled the "
        "reference comes OUT of config/model.yaml, 'even as a consistency remark':\n  "
        + "\n  ".join(offenders)
        + "\nCite the published benchmark and stop there. Whether the generator happens to "
        "agree is not evidence about the real world, and checking makes the operating point "
        "a function of the layer the firewall exists to hide. Move the observation to the "
        "model card if it is worth keeping; it does not belong in the file that sets the "
        "parameter."
    )


# --------------------------------------------------------------------------
# Negative controls. The two anchors that were really in this file are replayed
# against the detector, because a check rewritten to stop firing on a false
# positive is exactly the check most likely to have stopped firing on the true
# ones. Both instances below are the verbatim text that shipped in
# config/model.yaml before the fix.
# --------------------------------------------------------------------------

_HISTORICAL_ANCHORS = {
    "cost anchor ($29.88 per denied claim)": (
        "  # The simulation's own realized denial rework\n"
        "  # + appeal cost is $29.88 per DENIED claim, which averages over the two thirds\n"
        "  # of denials nobody appeals, so an appeal-specific cost above it is consistent\n"
        "  # rather than contradictory.\n"
    ),
    "filing-window check (965 of 967)": (
        "  # Consistent with this warehouse: 965 of 967 simulated appeals\n"
        "  # were filed within 120 days of the denial posting (median 15 days).\n"
    ),
}


@pytest.mark.parametrize("label", sorted(_HISTORICAL_ANCHORS))
def test_the_detector_still_catches_the_anchors_that_were_really_there(label: str) -> None:
    """Replay each removed anchor; the detector must reject it."""
    text = _CONFIG_PATH.read_text()
    marker = "# Decision thresholds and economics"
    injected = text.replace(marker, marker + "\n" + _HISTORICAL_ANCHORS[label], 1)
    assert injected != text, "could not inject the anchor; the section marker moved"

    offenders = _generator_anchors(injected, _declared_values(_config()))
    assert offenders, (
        f"the detector went silent on {label}, an anchor that really shipped in "
        "config/model.yaml. The check has been relaxed past the thing it exists to catch."
    )


def test_a_removal_note_is_not_itself_an_offence() -> None:
    """The false positive that forced the third formulation, pinned as a control.

    A note saying an anchor was removed, quoting only the config's own parameter
    value, must stay quiet — otherwise the fix a reader reaches for is deleting
    the honest record, and team-lead ruled that record worth keeping.
    """
    text = _CONFIG_PATH.read_text()
    marker = "# Decision thresholds and economics"
    note = (
        "  # A previous version of this comment reconciled $45 against the\n"
        "  # simulation's own realized cost per denied claim.\n"
    )
    injected = text.replace(marker, marker + "\n" + note, 1)

    assert not _generator_anchors(injected, _declared_values(_config())), (
        "a removal note that discloses no generator value was reported as an anchor. "
        "That is the false positive this check has now made three times; it drives the "
        "reader to scrub the honest sentence rather than the offending one."
    )
