"""`AGENTS.md` and `CLAUDE.md` are one ruleset in two files, and must stay so.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

Audit finding #5. This repository is worked by two different agent runtimes:
Claude reads `CLAUDE.md`, Codex reads `AGENTS.md` — each by its own convention,
and neither can be pointed at the other's filename. So the rules exist twice.

`AGENTS.md` was UNTRACKED for the whole Codex engagement. That is the actual
finding: an untracked ruleset has no history, no review, and no way for anyone to
notice it drifting from the tracked one. A team can be following two different
sets of non-negotiables and every commit will look fine. Both files are now
tracked, and this test makes divergence fail the build instead of going unseen.

WHY NOT MAKE ONE A POINTER TO THE OTHER. That was the tidier option and it was
rejected: it depends on the agent actually following the pointer, and if it does
not, that agent runs with NO rules at all rather than with stale ones. Stale
rules fail loudly here; absent rules fail silently. Duplication with an enforced
equality is the safer trade.

THE ONLY PERMITTED DIFFERENCES are each file naming itself: the H1 title, and the
§5 line listing which file needs human approval to edit. Everything else —
every locked decision, every provenance rule, every leakage rule — must be
byte-identical.
"""

from __future__ import annotations

import difflib
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

#: Substitutions applied to AGENTS.md before comparing. Each is a file naming
#: itself; anything else differing is drift.
SELF_REFERENCES = (
    (
        "# AGENTS.md — Healthcare RCM Intelligence Platform",
        "# CLAUDE.md — Healthcare RCM Intelligence Platform",
    ),
    ("`AGENTS.md` (human approval required)", "`CLAUDE.md` (human approval required)"),
)


def test_both_rulesets_are_tracked() -> None:
    """An untracked ruleset cannot be reviewed and cannot be seen to drift."""
    for path in (CLAUDE_MD, AGENTS_MD):
        assert path.is_file(), (
            f"{path.name} is missing. Both runtimes' rulesets live in this repository; "
            "one of them vanishing means an agent runs with no rules."
        )


def test_the_two_rulesets_are_identical_apart_from_self_reference() -> None:
    claude = CLAUDE_MD.read_text()
    agents = AGENTS_MD.read_text()

    normalized = agents
    for agents_form, claude_form in SELF_REFERENCES:
        assert agents_form in agents, (
            f"AGENTS.md no longer contains its expected self-reference {agents_form!r}. "
            "Either it was reworded, in which case update SELF_REFERENCES here, or it has "
            "drifted from CLAUDE.md in a way this test was not told about."
        )
        normalized = normalized.replace(agents_form, claude_form)

    if normalized != claude:
        diff = "\n".join(
            difflib.unified_diff(
                claude.splitlines(),
                normalized.splitlines(),
                fromfile="CLAUDE.md",
                tofile="AGENTS.md (self-references normalized)",
                lineterm="",
            )
        )
        raise AssertionError(
            "the two agent rulesets have diverged, so Claude and Codex are working to "
            "different non-negotiables:\n\n" + diff + "\n\n"
            "CLAUDE.md §5 requires human approval to change either. Reconcile them in one "
            "commit rather than letting the runtimes disagree."
        )


def test_the_normalization_cannot_hide_a_real_change() -> None:
    """Control: the substitutions must not be broad enough to mask drift.

    If SELF_REFERENCES ever grew to something general, this test would keep
    passing while real rule changes were normalized away.
    """
    claude = CLAUDE_MD.read_text()
    tampered = claude.replace(
        "Every simulated table and column name is prefixed `sim_`.",
        "Simulated columns may be named freely.",
    )
    assert tampered != claude, "the §3.2 sentence this control relies on has moved"

    normalized = tampered
    for agents_form, claude_form in SELF_REFERENCES:
        normalized = normalized.replace(agents_form, claude_form)
    assert normalized != claude, (
        "normalizing self-references erased a real rule change. SELF_REFERENCES has become "
        "too broad and this gate is no longer comparing the rules."
    )
