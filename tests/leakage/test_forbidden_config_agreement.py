"""GATE 2, first assertion: `config/model.yaml` and the firewall document must AGREE.

CLAUDE.md §4 puts the forbidden-column blacklist in `config/model.yaml`, and §4.5 puts
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


def _all_columns(schema: dict[str, list[str]]) -> set[str]:
    return {column for columns in schema.values() for column in columns}


def _resolve(patterns: list[str], universe: set[str]) -> dict[str, set[str]]:
    """Map each configured pattern to the real columns it matches."""
    return {
        pattern: {c for c in universe if fnmatch.fnmatchcase(c, pattern)} for pattern in patterns
    }


@pytest.fixture(scope="module")
def configured(model_config) -> list[str]:
    assert FORBIDDEN_KEY in model_config, (
        f"config/model.yaml has no `{FORBIDDEN_KEY}` key — CLAUDE.md §4 requires the "
        "forbidden-column blacklist to live there"
    )
    patterns = model_config[FORBIDDEN_KEY]
    assert isinstance(patterns, list) and patterns, f"`{FORBIDDEN_KEY}` must be a non-empty list"
    return list(patterns)


def test_no_configured_pattern_matches_zero_columns(configured, generated_schema):
    """A pattern that matches nothing is worse than an absent one: it reads as coverage.

    This is the check that would have caught the placeholder on the day it was written.
    """
    universe = _all_columns(generated_schema)
    dead = sorted(p for p, matched in _resolve(configured, universe).items() if not matched)
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


def test_config_blocks_nothing_the_document_permits(configured, generated_schema, forbidden_columns):
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


def test_latent_columns_are_forbidden_in_every_configured_list(model_config, firewall):
    """§1 latent internals are forbidden "in any model", Model C included.

    Model C's boundary is the denial rather than the submission, so it legitimately
    permits the denial outcome and money columns. It never permits the answer keys.
    """
    lists = {
        key: value
        for key, value in model_config.items()
        if key.startswith("forbidden") and isinstance(value, list)
    }
    assert lists, "config/model.yaml declares no forbidden-feature list"
    for key, patterns in lists.items():
        for column in sorted(firewall.latent_columns):
            assert any(fnmatch.fnmatchcase(column, p) for p in patterns), (
                f"latent answer key {column} is not blocked by `{key}` — §1 of "
                "docs/simulated_forbidden_columns.md forbids it in any model"
            )


def test_split_strategy_is_temporal(model_config):
    """CLAUDE.md §4.3: temporal splits, never random, wherever time features exist."""
    strategy = model_config.get("split", {}).get("strategy")
    assert strategy == "temporal", (
        f"config/model.yaml split.strategy is {strategy!r}; CLAUDE.md §4.3 requires "
        "'temporal'"
    )


def test_seed_is_configured_not_hardcoded(model_config):
    """A locked decision: split seeds live in config, never in code."""
    assert isinstance(model_config.get("seed"), int), (
        "config/model.yaml must declare an integer `seed` (CLAUDE.md §2, locked decision)"
    )
