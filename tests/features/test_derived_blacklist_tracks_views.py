"""`forbidden_derived_features` must still name columns `sql/views/` actually emits.

THE GAP THIS CLOSES, stated precisely because "a test exists" was true and not
enough. `forbidden_derived_features` names DERIVED view columns, so it cannot be
resolved against the generated schema — the generator never emits them. The CI
check (`tests/leakage/test_forbidden_config_agreement.py:
test_no_configured_pattern_matches_zero_columns`) says so in its own docstring and
scopes itself to `forbidden_features` alone. The block IS checked for dead
patterns, by `tests/leakage/test_live_leakage.py:
test_no_configured_pattern_is_dead_against_the_live_catalog` — but that module is
`pytest.mark.integration`, excluded from CI unit runs, and it `pytest.skip`s
outright when the warehouse has no views. So on a clean clone, in CI, and on any
machine where `make views` has not run, a rename in `sql/views/` could strand
every entry in this block and nothing would say a word.

That matters now because a view rename is in flight (Phase 5 [QUEUE-PREFIX] at the
warehouse layer, app-engineer under §5 delegated rename authority) and it is the
SECOND time a name-based protection has been put at risk by a rename.

WHAT THIS IS AND IS NOT. It is a staleness tripwire, not proof a column exists: it
reads the view SQL as text, so a name surviving only in a comment would satisfy it.
The live catalog check remains the authority on existence, and this does not
replace it — it moves the moment of discovery from "someone with a loaded warehouse
runs the integration suite" to "CI, on the commit that renamed the column".

WHY THE BLACKLIST IS NOT SIMPLY SELF-HEALING, since it nearly is: the runtime guard
(`src/features/leakage.py: _offenders`) matches exact-then-SUBSTRING, so an entry
survives a rename to a SUPERSTRING of itself — `dollars_at_stake` still blocks
`sim_dollars_at_stake`, measured. It does not survive a changed word, and an entry
naming a column that no longer exists is decoration either way. Both are what this
test is for.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS_DIR = REPO_ROOT / "sql" / "views"
CONFIG_PATH = REPO_ROOT / "config" / "model.yaml"


@pytest.fixture(scope="module")
def derived_block() -> dict[str, str]:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    block = config.get("forbidden_derived_features")
    assert block, "config/model.yaml has no `forbidden_derived_features` block"
    return block


@pytest.fixture(scope="module")
def view_sources() -> dict[str, str]:
    sources = {path.name: path.read_text() for path in sorted(VIEWS_DIR.glob("*.sql"))}
    assert sources, f"no view SQL found under {VIEWS_DIR}"
    return sources


def _views_naming(column: str, view_sources: dict[str, str]) -> list[str]:
    # Word-bounded: `ar_open_flag` must not be satisfied by `sim_ar_open_flag_x`,
    # which is a different column and would hide exactly the drift being hunted.
    pattern = re.compile(rf"\b{re.escape(column)}\b")
    return [name for name, text in view_sources.items() if pattern.search(text)]


def test_every_derived_forbid_names_a_column_some_view_still_emits(
    derived_block: dict[str, str], view_sources: dict[str, str]
) -> None:
    stranded = {
        column: reason
        for column, reason in derived_block.items()
        if not _views_naming(column, view_sources)
    }
    assert not stranded, (
        f"{len(stranded)} entr(ies) in `forbidden_derived_features` name a column that appears "
        f"in NO file under sql/views/:\n  "
        + "\n  ".join(f"{column}  ({reason})" for column, reason in sorted(stranded.items()))
        + "\n\nEither the view renamed the column and this block was not updated in lockstep — "
        "in which case the entry is now decoration and the RENAMED column may be unguarded — or "
        "the column was dropped and the entry should go. Do not delete the entry to go green "
        "without checking which. While a rename is in flight, carry BOTH spellings, the way "
        "`forbidden_crosswalk_display_features` already does."
    )


def test_the_work_queue_view_entries_are_the_ones_in_flight(
    derived_block: dict[str, str], view_sources: dict[str, str]
) -> None:
    """The four entries the Phase 5 view rename touches, pinned by name.

    Named individually rather than left to the sweep above so that the rename
    cannot be landed on one side only and still look tidy: if the view renames and
    the config does not, this says which columns, not just how many.
    """
    queue_view = "vw_work_queue_priority.sql"
    assert queue_view in view_sources
    for column in ("dollars_at_stake", "heuristic_priority_score", "priority_tier", "action_type"):
        assert column in derived_block, (
            f"`{column}` is a DERIVED column of {queue_view} built on simulated adjudication "
            "facts and is not on the forbidden_derived_features list. `action_type` is a CASE "
            "on sim_denial_flag and encodes the label directly."
        )
        assert queue_view in _views_naming(column, view_sources), (
            f"`{column}` is on the blacklist but {queue_view} no longer names it. If it was "
            "renamed, add the new spelling ALONGSIDE this one and drop the old one only once "
            "the view rename has merged."
        )


def test_no_entry_is_written_as_a_glob(derived_block: dict[str, str]) -> None:
    """A glob here passes the fnmatch-based tests and blocks nothing at runtime.

    The test suite resolves these names with `fnmatch`, but the guard that actually
    stops a training run (`src/features/leakage.py: _offenders`) does exact-then-
    substring matching and has no glob support. `*dollars_at_stake` would therefore
    look like coverage in every test and provide none — the Phase 2 placeholder
    defect, reintroduced through the one door the existing checks cannot see.
    """
    globbed = sorted(c for c in derived_block if any(ch in c for ch in "*?["))
    assert not globbed, (
        f"glob metacharacters in `forbidden_derived_features`: {globbed}. The runtime guard "
        "matches exact-then-substring and does not expand globs, so these block nothing while "
        "passing the fnmatch-based tests. Write both literal spellings instead."
    )
