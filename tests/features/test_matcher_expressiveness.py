"""Any protection whose test-time and runtime matchers differ is a latent placeholder.

THE STANDING RULE, earned twice and generalised here.

A protection in this repo is usually two things: a list of names, and code that
decides whether a given name is on it. When the check that runs in the TESTS
resolves those names more generously than the check that runs in PRODUCTION, the
protection is green and empty — it reports coverage for names it never blocks.
That is the Phase 2 placeholder defect wearing a different hat, and it arrives
through the one door the existing checks cannot see, because every check agrees
the list is fine.

Two instances are pinned below. Both were MEASURED before being fixed.

**1. Globs on the leakage blacklist.** `tests/leakage/` resolves every configured
name with `fnmatch.fnmatchcase` and its own vocabulary calls them "patterns".
`src/features/leakage.py::_offenders` matches exact-then-substring and expands
nothing. So `*denied_amount` resolves to the real column in every test and blocks
it at runtime in none. Measured: planted in `forbidden_features`,
`forbidden_features_defensive` and `forbidden_crosswalk_display_features` in
turn, `_offenders` failed to block `sim_denied_amount` in all three.
`tests/features/test_derived_blacklist_tracks_views.py` already refuses this in
`forbidden_derived_features` (f18dfc7); it is the whole family that needs it,
because the hole is in the matcher, not in one block.

**2. Two `sim_` predicates inside one module.** `provenance.py` asked
`"sim_" in name` when checking whether a column CARRIES the marker and
`name.startswith("sim_")` when deciding whether one was REQUIRED. A column
declared `amount_in_dispute <- log_sim_denied_amount` therefore had empty
simulated lineage and published an unmarked simulated dollar figure with rule 3
silent, while the identical column sourced from `sim_denied_amount` was refused.
Measured on both before the fix. Now one predicate, `names_simulated`, and the
test below fails if a second one reappears.

The general form, for whoever adds the third: **two matchers for one concept, of
different reach, and the protection stops in the gap between them.** The fix is
never to make the test stricter — it is to make there be one matcher.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest
import yaml

from src.features import provenance
from src.features.leakage import MODEL_CONFIG_PATH, _offenders, forbidden_columns
from src.features.provenance import (
    ColumnProvenance,
    ProvenanceError,
    PublishedSchema,
    assert_schema_is_marked,
    names_simulated,
)

GLOB_METACHARACTERS = "*?["

# Every config block whose entries the tests resolve with fnmatch. The two
# `*_tables` keys are included even though `assert_config_agrees_with_doc`
# compares them by SET EQUALITY — a glob there fails loudly rather than silently,
# so they are covered by a different mechanism, and listing them anyway keeps the
# rule stated over the whole family instead of over a remembered subset.
BLACKLIST_KEYS = (
    "forbidden_features",
    "forbidden_tables",
    "forbidden_table_columns",
    "forbidden_derived_features",
    "forbidden_source_features",
    "forbidden_features_defensive",
    "forbidden_crosswalk_tables",
    "forbidden_crosswalk_display_features",
)


@pytest.fixture(scope="module")
def model_config() -> dict:
    return yaml.safe_load(pathlib.Path(MODEL_CONFIG_PATH).read_text())


def _entries(block: object) -> list[str]:
    """Every configured name out of a block, in either shape the config uses."""
    if isinstance(block, dict):
        return [str(name) for name in block]
    if isinstance(block, list):
        return [str(name) for name in block]
    return []


# --- instance 1: globs block nothing at runtime ----------------------------


def test_no_blacklist_entry_anywhere_is_written_as_a_glob(model_config: dict) -> None:
    """The `forbidden_derived_features` rule, applied to the whole family."""
    blocks = {key: model_config.get(key) for key in BLACKLIST_KEYS}
    blocks["model_c.forbidden_features"] = (model_config.get("model_c") or {}).get(
        "forbidden_features"
    )

    globbed = {
        key: found
        for key, block in blocks.items()
        if (
            found := sorted(
                name
                for name in _entries(block)
                if any(char in name for char in GLOB_METACHARACTERS)
            )
        )
    }
    assert not globbed, (
        f"glob metacharacters in the leakage blacklist: {globbed}. The tests resolve these "
        "with fnmatch and the runtime guard (src/features/leakage.py::_offenders) matches "
        "exact-then-substring with no glob support, so an entry like `*denied_amount` reads "
        "as coverage everywhere and blocks nothing. Write both literal spellings instead."
    )


def test_a_glob_really_does_block_nothing_at_runtime(model_config: dict) -> None:
    """The measurement behind the rule above, kept executable.

    Without this the previous test is an unexplained style rule that a future
    author can reasonably talk themselves out of. This one shows the cost.
    """
    target = "sim_denied_amount"
    assert _offenders([target], forbidden_columns("A", model_config)), (
        f"{target} is not blocked by the real config; this control has stopped controlling"
    )

    # The same column, guarded only by a glob that names it under fnmatch.
    empty = {key: ({} if isinstance(model_config.get(key), dict) else []) for key in BLACKLIST_KEYS}
    globbed = {**model_config, **empty, "forbidden_features": ["*denied_amount"], "model_c": {}}
    assert not _offenders([target], forbidden_columns("A", globbed)), (
        "a glob now blocks at runtime — if `_offenders` gained glob support, delete "
        "test_no_blacklist_entry_anywhere_is_written_as_a_glob rather than leaving a rule "
        "whose reason has expired"
    )


# --- instance 2: one marker predicate, not two -----------------------------


def test_the_module_has_exactly_one_sim_marker_predicate() -> None:
    """A second spelling of "does this name carry the marker" is the defect returning.

    Read off the source rather than the behaviour, because the two predicates
    agreed on every name in the repo at the moment they diverged — the repo has
    zero infixed names today. Behaviour could not have caught it; shape can.
    """
    source = inspect.getsource(provenance)
    body = source.split("    return SIMULATED_MARKER in name", 1)
    assert len(body) == 2, "names_simulated no longer holds the module's only marker test"

    stray = [
        line.strip()
        for line in body[1].splitlines()
        if "SIMULATED_MARKER" in line
        and ("startswith" in line or " in " in line)
        and not line.strip().startswith("#")
    ]
    assert not stray, (
        f"a second `sim_` matcher has appeared in provenance.py: {stray}. Every rule in that "
        "module must ask names_simulated(), because the last time there were two — `in` for "
        "the marker check and `startswith` for the lineage walk — a column sourced from "
        "`log_sim_denied_amount` had empty lineage and published unmarked."
    )


def test_an_infixed_source_still_demands_a_marker() -> None:
    """The measured hole, as a test. `startswith` misses this; `in` does not."""
    laundered = PublishedSchema(
        name="probe",
        grain="one row per claim",
        columns=(
            ColumnProvenance(
                name="amount_in_dispute",
                classification="DERIVED",
                description="A simulated dollar figure under a clean name.",
                sources=("log_sim_denied_amount",),
            ),
        ),
    )
    assert laundered.simulated_lineage("amount_in_dispute") == ("log_sim_denied_amount",)
    with pytest.raises(ProvenanceError, match="amount_in_dispute"):
        assert_schema_is_marked(laundered)


def test_the_predicate_does_not_degrade_to_bare_sim() -> None:
    """The negative control: `sim_` is the marker, `sim` is a prefix of ordinary words.

    A predicate that had quietly become `"sim" in name` would pass every test
    above while flagging `similar_claims_count` — and a rule that fires on
    innocent names is one a future author disables.
    """
    assert names_simulated("sim_denied_amount")
    assert names_simulated("log_sim_denied_amount"), "the infix case is the one that was missed"
    assert not names_simulated("simulation_notes")
    assert not names_simulated("similar_claims_count")
    assert not names_simulated("claim_sk")


def test_the_real_schemas_are_unmoved_by_the_widened_predicate() -> None:
    """Closing the hole must not have changed a single live declaration.

    Every source named in PUBLISHED_SURFACES is prefixed or plainly unsimulated,
    so `in` and `startswith` agree on all of them — which is exactly why the
    divergence survived. Pinned so the claim is checked rather than remembered.
    """
    for surface in provenance.PUBLISHED_SURFACES:
        if surface.schema is not None:
            assert_schema_is_marked(surface.schema)
