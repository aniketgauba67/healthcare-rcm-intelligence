"""Typed access to config/simulation.yaml, plus the RNG stream scheme.

Nothing in the simulation layer hardcodes a seed or a coefficient (CLAUDE.md
§2): every number is read from the YAML, and every value read here is a DESIGN
CHOICE documented in docs/assumptions.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from src.ingestion.paths import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "simulation.yaml"


@dataclass(frozen=True)
class Payer:
    """One simulated payer archetype. Not modelled on any real insurer."""

    id: str
    name: str
    mix: float
    logit_offset: float
    auth_required_rate: float
    timely_filing_days: int
    adjudication_lognormal: dict[str, float]
    allowed_ratio_beta: dict[str, float]


@dataclass(frozen=True)
class ServiceLine:
    """A coarse MS-DRG numeric range. The boundaries are our design choice, not
    an official CMS MDC taxonomy — see docs/assumptions.md §"Service lines"."""

    id: str
    name: str
    lo: int | None
    hi: int | None
    logit_offset: float


class SimulationConfig:
    """Parsed config/simulation.yaml."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc
        self.version: str = str(doc["version"])
        self.seed: int = int(doc["seed"])
        self.targets: dict[str, Any] = doc["targets"]
        self.mechanisms: dict[str, Any] = doc["mechanisms"]
        self.denial_categories: dict[str, Any] = doc["denial_categories"]
        self.appeal: dict[str, Any] = doc["appeal"]
        self.timelines: dict[str, Any] = doc["timelines"]
        self.operating_costs: dict[str, Any] = doc["operating_costs"]
        self.validation: dict[str, Any] = doc["validation"]
        self.payers: list[Payer] = [Payer(**p) for p in doc["payers"]]
        self.service_lines: list[ServiceLine] = [ServiceLine(**s) for s in doc["service_lines"]]
        self._validate()

    def _validate(self) -> None:
        mix = sum(p.mix for p in self.payers)
        if abs(mix - 1.0) > 1e-9:
            raise ValueError(f"payer mix must sum to 1.0, got {mix}")
        if len({p.id for p in self.payers}) != len(self.payers):
            raise ValueError("duplicate payer id")
        if len({s.id for s in self.service_lines}) != len(self.service_lines):
            raise ValueError("duplicate service_line id")
        known = {c["id"] for c in self.denial_categories["catalog"]}
        for mech, dist in self.denial_categories["by_mechanism"].items():
            unknown = set(dist) - known
            if unknown:
                raise ValueError(f"denial_categories.by_mechanism.{mech} references {unknown}")
            total = sum(dist.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"denial_categories.by_mechanism.{mech} sums to {total}, not 1.0")

    @property
    def payer_ids(self) -> list[str]:
        return [p.id for p in self.payers]

    def payer(self, payer_id: str) -> Payer:
        return next(p for p in self.payers if p.id == payer_id)

    @property
    def category_ids(self) -> list[str]:
        return [c["id"] for c in self.denial_categories["catalog"]]

    def carc_group(self, category_id: str) -> str:
        """CARC code used as a CATEGORY LABEL only (CLAUDE.md §3.7)."""
        return next(
            c["carc_group"] for c in self.denial_categories["catalog"] if c["id"] == category_id
        )


def load_config(path: Path | None = None) -> SimulationConfig:
    return SimulationConfig(yaml.safe_load((path or CONFIG_PATH).read_text()))


def stream(seed: int, name: str) -> np.random.Generator:
    """An independent, named RNG stream derived from the master seed.

    Named rather than positional on purpose: `SeedSequence(seed).spawn(n)` would
    renumber every downstream stream the moment a new component is inserted,
    silently changing output that is supposed to be reproducible. Hashing the
    name into the spawn key means adding a component never disturbs an existing
    one, so a calibration diff shows only what actually changed.
    """
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return np.random.default_rng(
        np.random.SeedSequence(entropy=seed, spawn_key=(int.from_bytes(digest, "big"),))
    )
