"""Reproducibility: same seed ⇒ byte-identical output (CLAUDE.md §7).

"Byte-identical" is defined as the SHA-256 of each table's canonical CSV
serialization — see the docstring in src/simulation/run.py for why that, and
not the Parquet bytes.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.simulation.config import SimulationConfig, load_config, stream
from src.simulation.generator import generate
from src.simulation.run import table_hashes

from .conftest import make_base


def test_same_seed_produces_identical_tables(cfg, base):
    first = table_hashes(generate(cfg, base))
    second = table_hashes(generate(cfg, base))
    assert first == second


def test_different_seed_produces_different_tables(cfg, base):
    other = load_config()
    other.seed = cfg.seed + 1
    changed = generate(other, base)
    baseline = generate(cfg, base)
    changed_hashes, baseline_hashes = table_hashes(changed), table_hashes(baseline)
    for table in ("sim_claim_adjudication", "sim_workflow_events", "sim_operating_costs"):
        assert changed_hashes[table] != baseline_hashes[table], f"{table} ignored the seed"
    # The dimensions are pure config: only the stamped seed column may move.
    for table in ("sim_payer", "sim_service_line"):
        left = changed.table(table).drop(columns=["sim_seed"])
        right = baseline.table(table).drop(columns=["sim_seed"])
        assert left.equals(right), f"{table} is config-only and must not depend on the seed"


def test_named_streams_are_stable_and_independent(cfg):
    """A named stream depends on its own name only.

    This is the property that lets a new component be added without silently
    renumbering every existing one — the thing positional `SeedSequence.spawn`
    would get wrong.
    """
    first = stream(cfg.seed, "money").random(5)
    again = stream(cfg.seed, "money").random(5)
    other = stream(cfg.seed, "appeals").random(5)
    np.testing.assert_array_equal(first, again)
    assert not np.allclose(first, other)
    # And a different master seed moves it.
    assert not np.allclose(first, stream(cfg.seed + 1, "money").random(5))


def test_config_rejects_invalid_payer_mix():
    doc = load_config()._doc
    broken = {**doc, "payers": [{**p, "mix": 0.9} for p in doc["payers"]]}
    with pytest.raises(ValueError, match="payer mix"):
        SimulationConfig(broken)


def test_config_rejects_category_distribution_that_does_not_sum_to_one():
    doc = load_config()._doc
    categories = {**doc["denial_categories"]}
    by_mechanism = {**categories["by_mechanism"]}
    by_mechanism["late_filing"] = {"TIMELY_FILING": 0.5}
    categories["by_mechanism"] = by_mechanism
    with pytest.raises(ValueError, match="sums to"):
        SimulationConfig({**doc, "denial_categories": categories})


def test_generation_is_independent_of_base_row_order(cfg):
    """Shuffling the input must not change the output.

    Row order is an accident of how the warehouse frame was built. If it leaked
    into the draws, reproducibility would hold only as long as nobody touched
    the upstream sort.
    """
    ordered = make_base(n=800)
    shuffled = ordered.sample(frac=1.0, random_state=3).reset_index(drop=True)
    left = generate(cfg, ordered).table("sim_claim_adjudication")
    right = (
        generate(cfg, shuffled)
        .table("sim_claim_adjudication")
        .sort_values("claim_sk")
        .reset_index(drop=True)
    )
    assert left["sim_latent_p"].round(6).equals(right["sim_latent_p"].round(6))
    assert left["sim_denial_flag"].equals(right["sim_denial_flag"])
