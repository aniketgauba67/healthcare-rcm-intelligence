"""The blacklist must catch a forbidden quantity under EITHER spelling.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

Audit finding #1. `_offenders` matches by substring, so protection used to be
one-directional and depended silently on which spelling `config/model.yaml`
happened to hold:

    entry `dollars_at_stake`      catches `sim_dollars_at_stake`  -- substring
    entry `sim_dollars_at_stake`  catches `dollars_at_stake`      -- NOT a
                                  substring: the entry is LONGER than the column,
                                  so nothing matched and the column walked in

The §3.2 marker rename moved every `forbidden_derived_features` entry to the
prefixed spelling, which left every bare name unguarded. Nothing was exploiting
it, because the views were renamed in the same change -- but a blacklist whose
coverage depends on a spelling is exactly the placeholder defect that opened
Phase 4: green, and empty. It would have stayed invisible until something
reintroduced a bare name, which is the one situation where the guard is the only
thing standing between a fabricated denial and a training matrix.

These tests use scratch frames rather than the real config so they state the
property directly and cannot be quietly satisfied by an unrelated config edit.
"""

from __future__ import annotations

from src.features.leakage import _offenders

#: The quantity, under both labels. `sim_denial_flag` is Model A's actual label.
FORBIDDEN_MARKED = "sim_denial_flag"
FORBIDDEN_BARE = "denial_flag"


def _caught(columns: list[str], blacklist: set[str]) -> dict[str, str]:
    """Column -> the blacklist entry that caught it. Empty means nothing matched."""
    return _offenders(columns, frozenset(blacklist))


def test_a_bare_forbidden_column_is_caught_by_a_marked_blacklist_entry() -> None:
    """The direction that was broken: entry carries the marker, column does not."""
    hits = _caught(["billed_charge_amt", FORBIDDEN_BARE], {FORBIDDEN_MARKED})
    assert FORBIDDEN_BARE in hits, (
        "a bare forbidden column was not reported. A blacklist entry names a QUANTITY; "
        "dropping the marker from the column must not drop it out of the blacklist."
    )


def test_a_marked_forbidden_column_is_caught_by_a_bare_blacklist_entry() -> None:
    """The direction that already worked. It must keep working."""
    hits = _caught(["billed_charge_amt", FORBIDDEN_MARKED], {FORBIDDEN_BARE})
    assert FORBIDDEN_MARKED in hits


def test_a_derived_bare_column_is_still_caught() -> None:
    """`log_denial_flag_ratio` is the forbidden quantity wearing a hat and no marker."""
    hits = _caught(["log_denial_flag_ratio"], {FORBIDDEN_MARKED})
    assert "log_denial_flag_ratio" in hits


def test_permitted_columns_are_not_swept_up() -> None:
    """Stripping the marker must not turn the guard into a blunt instrument.

    Over-blocking is its own defect: a guard that rejects legitimate features
    gets loosened, and the loosening is where real leaks enter. These are the
    genuine CMS SOURCE columns the model is entitled to see.
    """
    hits = _caught(
        [
            "billed_charge_amt",
            "medicare_source_paid_amt",
            "length_of_stay_days",
            "diagnosis_count",
            "drg_cd",
        ],
        {FORBIDDEN_MARKED, "sim_paid_amount", "sim_allowed_amount"},
    )
    assert hits == {}, f"permitted columns were rejected: {hits}"


def test_the_marker_alone_is_not_a_blacklist_entry() -> None:
    """A degenerate entry must not match everything.

    If `sim_` itself ever reached the blacklist, stripping it would leave an
    empty string, and an empty substring is in every name -- the guard would
    reject every column and then be switched off. The empty result is ignored
    rather than matched.
    """
    hits = _caught(["billed_charge_amt", "length_of_stay_days"], {"sim_"})
    assert hits == {}, f"a degenerate `sim_` entry matched everything: {hits}"
