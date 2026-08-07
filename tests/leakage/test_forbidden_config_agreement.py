"""GATE 2, first assertion: `config/model.yaml` and the firewall document must AGREE.

docs/project_rules.md §4 puts the forbidden-column blacklist in `config/model.yaml`, and §4.5 puts
the authoritative statement of the boundary in `docs/simulated_forbidden_columns.md`,
because ml-engineer may not read `src/simulation/`. The config is therefore a
transcription, and nothing was checking the transcription.

What that cost is on the record. The placeholder list carried eleven patterns, five of
which (`sim_denial_reason`, `sim_recovered_*`, `adjudication_date`, `payment_date`,
`post_submission_workflow_*`) matched zero real columns — two of them because they were
written without the `sim_` prefix every generated column actually carries — while
`sim_provider_quality_latent`, the provider's latent quality draw and a pure answer key,
was left unprotected. The list looked like coverage and was mostly decoration.

So this module refuses to trust either file alone. It resolves the config's patterns
against the real generated schema and requires the result to equal the set the document
describes, exactly — a missing entry is an unguarded leak, and a spurious one is a
pattern nobody has validated. It separately rejects any pattern that matches nothing,
which is the specific failure that made the placeholder look adequate.
"""

from __future__ import annotations

import fnmatch

import pytest

FORBIDDEN_KEY = "forbidden_features"

# `forbidden_*` blocks that hold TABLE names rather than column names. Everything
# else under that prefix is treated as column-bearing and folded into the blacklist,
# so a new block cannot be added and then quietly left out of the union.
TABLE_NAME_KEYS = frozenset({"forbidden_tables", "forbidden_crosswalk_tables"})


def _all_columns(schema: dict[str, list[str]]) -> set[str]:
    return {column for columns in schema.values() for column in columns}


def _resolve(patterns: list[str], universe: set[str]) -> dict[str, set[str]]:
    """Map each configured pattern to the real columns it matches."""
    return {
        pattern: {c for c in universe if fnmatch.fnmatchcase(c, pattern)} for pattern in patterns
    }


def _names(block) -> set[str]:
    """Column names out of a config block, in any of the three shapes it uses.

    The config carries forbidden columns three ways, and the difference matters —
    reading one as another is how this fixture originally lost the whole-table
    expansion and reported seven `sim_operating_costs` columns as unblocked when
    they were blocked all along:

      * a plain list, where the name is all there is
        (`forbidden_features: [sim_denial_flag, ...]`);
      * an ANNOTATED dict, keyed by column, valued by a note saying what the
        column was derived from (`forbidden_derived_features: {clean_claim_flag:
        "function of sim_denial_flag"}`) — the names are the KEYS;
      * a GROUPED dict, keyed by TABLE, valued by that table's column list
        (`forbidden_table_columns: {sim_appeals: [sim_appeal_sk, ...]}`) — the
        names are the VALUES, and the keys are table names that are not columns
        at all.

    The two dict shapes are told apart by their values, not by hardcoding the key,
    so a new grouped block is read correctly without editing this function.
    """
    if isinstance(block, dict):
        values = list(block.values())
        if values and all(isinstance(v, list) for v in values):
            return {str(c) for group in values for c in group}
        return {str(k) for k in block}
    if isinstance(block, list):
        return {str(v) for v in block}
    return set()


def _column_bearing_blocks(config: dict) -> dict[str, set[str]]:
    """Every `forbidden_*` block that names columns, top level and under `model_c`."""
    blocks = {
        key: _names(value)
        for key, value in config.items()
        if key.startswith("forbidden") and key not in TABLE_NAME_KEYS
    }
    nested = (config.get("model_c") or {}).get("forbidden_features")
    if nested:
        blocks["model_c.forbidden_features"] = _names(nested)
    return blocks


@pytest.fixture(scope="module")
def configured(model_config) -> list[str]:
    """The blacklist as a flat list of names, unioned across every column block.

    docs/project_rules.md §4 names `forbidden_features` as the blacklist's home, and the config
    keeps that key as the surface that must equal the firewall document exactly. It
    then carries further blocks for things the document does not cover — whole-table
    expansions, DERIVED view columns, SOURCE adjudication outputs, the crosswalk
    linkage. All of them bar columns from a matrix, so the guard is their union, and
    that union is what these tests check.
    """
    assert FORBIDDEN_KEY in model_config, (
        f"config/model.yaml has no `{FORBIDDEN_KEY}` key — docs/project_rules.md §4 requires the "
        "forbidden-column blacklist to live there"
    )
    blocks = _column_bearing_blocks(model_config)
    union = set().union(*blocks.values())
    assert union, "no forbidden columns configured"
    return sorted(union)


def test_no_configured_pattern_matches_zero_columns(model_config, generated_schema):
    """A pattern that matches nothing is worse than an absent one: it reads as coverage.

    This is the check that would have caught the placeholder on the day it was written.

    Scoped to `forbidden_features`, because the generated schema is the only universe
    available without a database and it is the only universe that key is allowed to
    draw from — `forbidden_features` must equal the firewall document exactly, and the
    document describes generator output. The other blocks deliberately name columns the
    generator never emits (DERIVED view columns, real CMS SOURCE columns, crosswalk
    linkage columns), so resolving them here would report every one of them as dead.
    They get the same treatment against the full live catalog in
    tests/leakage/test_live_leakage.py, where those columns actually exist.
    """
    patterns = list(model_config.get(FORBIDDEN_KEY) or [])
    assert patterns, f"config/model.yaml has no `{FORBIDDEN_KEY}` entries"
    universe = _all_columns(generated_schema)
    dead = sorted(p for p, matched in _resolve(patterns, universe).items() if not matched)
    assert not dead, (
        f"{len(dead)} pattern(s) in config/model.yaml `{FORBIDDEN_KEY}` match no column "
        f"in the generated schema: {dead}\n"
        "Check the `sim_` prefix — every generated column carries it. Correct names are "
        "in docs/simulated_forbidden_columns.md."
    )


def test_config_covers_every_forbidden_column(configured, generated_schema, forbidden_columns):
    """Under-coverage: a column the document forbids that the config does not block."""
    universe = _all_columns(generated_schema)
    resolved = set().union(*_resolve(configured, universe).values())
    missing = sorted(forbidden_columns - resolved)
    assert not missing, (
        f"{len(missing)} column(s) forbidden by docs/simulated_forbidden_columns.md are "
        f"NOT blocked by config/model.yaml `{FORBIDDEN_KEY}`:\n  "
        + "\n  ".join(missing)
        + "\n\nEach of these can enter a training matrix today. `claim_sk` and `clm_id` "
        "are included deliberately (§7: neither may be a feature); the training-matrix "
        "guard exempts them where they are the matrix key."
    )


def test_config_blocks_nothing_the_document_permits(
    configured, generated_schema, forbidden_columns
):
    """Over-coverage: a pattern blocking a column the document explicitly permits.

    Over-blocking is not a leak, but it is drift, and it is how a blacklist silently
    stops matching the boundary it is supposed to encode. It usually means a wildcard
    is broader than intended.
    """
    universe = _all_columns(generated_schema)
    resolved = _resolve(configured, universe)
    spurious = {
        pattern: sorted(matched - forbidden_columns)
        for pattern, matched in resolved.items()
        if matched - forbidden_columns
    }
    assert not spurious, (
        "config/model.yaml blocks columns docs/simulated_forbidden_columns.md permits "
        f"as Model A features:\n{spurious}\n"
        "Either narrow the pattern, or update the document if the boundary really moved."
    )


def test_latent_columns_are_forbidden_for_every_model(model_config, firewall):
    """§1 latent internals are forbidden "in any model", Model C included.

    Model C's boundary is the denial rather than the submission, so it legitimately
    permits the denial outcome and money columns. It never permits the answer keys.

    Checked per MODEL, against that model's effective blacklist. The earlier version
    of this test iterated every `forbidden_*` key that happened to be a YAML list and
    required all of them to name every latent column, which asked `forbidden_tables`
    — a list of TABLE names — to contain a column, and failed on a category error
    rather than on a leak.
    """
    latents = sorted(firewall.latent_columns)
    assert latents, "the firewall document declares no §1 latent columns"

    model_a = set().union(*_column_bearing_blocks(model_config).values())
    model_c = _names((model_config.get("model_c") or {}).get("forbidden_features"))
    assert model_c, "config/model.yaml declares no `model_c.forbidden_features`"

    for label, blacklist in (("Model A", model_a), ("Model C", model_c)):
        for column in latents:
            assert any(fnmatch.fnmatchcase(column, p) for p in blacklist), (
                f"latent answer key {column} is not blocked for {label} — §1 of "
                "docs/simulated_forbidden_columns.md forbids it in any model"
            )


def test_forbidden_tables_hold_table_names_not_column_names(model_config, generated_schema):
    """The table-name keys must really name tables.

    `forbidden_tables` is excluded from the column union above on the grounds that it
    names tables. If a column name were ever added to it, that column would be excluded
    from the union AND matched by nothing — silently unblocked by the very exclusion
    that was supposed to be a formality.
    """
    for key in sorted(TABLE_NAME_KEYS):
        entries = model_config.get(key) or []
        not_tables = sorted(e for e in entries if e not in generated_schema)
        # Crosswalk tables are not generator output, so they are absent from the
        # generated schema legitimately; only assert on the generator-owned key.
        if key == "forbidden_tables":
            assert not not_tables, (
                f"`{key}` contains {not_tables}, which are not tables in the generated "
                f"schema. Entries here are excluded from the column blacklist, so a "
                f"column placed here is blocked by nothing."
            )


def test_split_strategy_is_temporal(model_config):
    """docs/project_rules.md §4.3: temporal splits, never random, wherever time features exist."""
    strategy = model_config.get("split", {}).get("strategy")
    assert strategy == "temporal", (
        f"config/model.yaml split.strategy is {strategy!r}; docs/project_rules.md §4.3 requires 'temporal'"
    )


def test_seed_is_configured_not_hardcoded(model_config):
    """A locked decision: split seeds live in config, never in code."""
    assert isinstance(model_config.get("seed"), int), (
        "config/model.yaml must declare an integer `seed` (docs/project_rules.md §2, locked decision)"
    )
