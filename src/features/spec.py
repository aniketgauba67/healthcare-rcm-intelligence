"""Feature declarations: every feature states what it is computed from.

A leakage guard that only sees a finished matrix can check names, and names lie.
`payer_prior_denial_rate` is a legitimate feature and `payer_denial_rate` is the
label in a hat, and nothing about the two strings says which is which. So every
feature in the store is declared here with its source columns, and the guard
checks the declaration (`src/features/leakage.py: assert_sources_safe`).

The one sanctioned exception is CLAUDE.md §4.2: historical-rate features are
*supposed* to read the outcome column, because that is what a historical rate
is. They are allowed to, on three conditions — they declare
`point_in_time="prior_period"`, they name the outcome explicitly in
`prior_period_sources` so it appears in an audit, and they are computed by
`src/features/historical.py`, which never looks forward. Anything that reads the
outcome without that declaration fails the build.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from src.features.leakage import LeakageError, forbidden_columns

Kind = Literal["numeric", "boolean", "categorical"]
PointInTime = Literal["direct", "prior_period"]

# The only forbidden columns a prior-period feature may aggregate: the outcome
# itself, lagged. Anything else — the latent probability, the denied amount, the
# rework cost — has no historical-rate interpretation and is simply a leak.
_PRIOR_PERIOD_ALLOWED_SOURCES = frozenset({"sim_denial_flag", "sim_appeal_outcome"})


@dataclass(frozen=True)
class FeatureSpec:
    """One column of the feature matrix, and where it came from."""

    name: str
    kind: Kind
    description: str
    sources: tuple[str, ...] = ()
    point_in_time: PointInTime = "direct"
    # Outcome columns this feature aggregates over a strictly prior window.
    prior_period_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.point_in_time == "prior_period" and not self.prior_period_sources:
            raise ValueError(f"{self.name}: prior_period features must name their outcome source")
        if self.point_in_time == "direct" and self.prior_period_sources:
            raise ValueError(f"{self.name}: only prior_period features may aggregate an outcome")
        bad = set(self.prior_period_sources) - _PRIOR_PERIOD_ALLOWED_SOURCES
        if bad:
            raise ValueError(
                f"{self.name}: {sorted(bad)} has no historical-rate reading; "
                f"only {sorted(_PRIOR_PERIOD_ALLOWED_SOURCES)} may be lagged"
            )


@dataclass(frozen=True)
class FeatureSet:
    """The declared feature matrix for one model."""

    model: Literal["A", "C"]
    specs: tuple[FeatureSpec, ...]
    label: str
    time_column: str
    entity_column: str = "claim_sk"
    # Columns carried alongside the matrix for splitting, slicing and joining,
    # and dropped before fitting. They are NOT features.
    passthrough: tuple[str, ...] = field(default=())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs)

    def by_kind(self, kind: Kind) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs if spec.kind == kind)

    def spec(self, name: str) -> FeatureSpec:
        for candidate in self.specs:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    def lineage(self) -> dict[str, tuple[str, ...]]:
        """Feature -> declared source columns, for the audit trail."""
        return {spec.name: spec.sources for spec in self.specs}

    def prior_period_features(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.specs if s.point_in_time == "prior_period")


def validate_feature_set(feature_set: FeatureSet, config: dict | None = None) -> None:
    """Fail if any declaration would put a forbidden column into the matrix.

    Checked here rather than only on the finished frame so that a bad feature is
    rejected when it is *defined*, with the offending source named, instead of
    surfacing later as a column that scores suspiciously well.
    """
    blacklist = forbidden_columns(feature_set.model, config)
    problems: list[str] = []

    if feature_set.label not in blacklist:
        problems.append(
            f"label {feature_set.label!r} is not on the forbidden list — a label that is "
            "safe to use as a feature is not a label"
        )

    duplicates = [n for n in feature_set.names if feature_set.names.count(n) > 1]
    if duplicates:
        problems.append(f"duplicate feature names: {sorted(set(duplicates))}")

    for spec in feature_set.specs:
        if spec.name in blacklist:
            problems.append(f"{spec.name}: the feature itself is forbidden")
        offending = sorted(set(spec.sources) & blacklist)
        if offending:
            problems.append(f"{spec.name}: reads {offending}")
        if spec.point_in_time == "prior_period":
            declared = set(spec.prior_period_sources)
            if not declared:  # pragma: no cover - __post_init__ already rejects
                problems.append(f"{spec.name}: prior_period with no declared outcome")
            if declared & set(spec.sources):
                problems.append(
                    f"{spec.name}: the outcome must be declared in prior_period_sources only, "
                    "so a direct read is never mistaken for a lagged one"
                )

    if problems:
        raise LeakageError(
            f"invalid feature declarations for Model {feature_set.model}: " + "; ".join(problems)
        )


def audit_rows(feature_set: FeatureSet) -> list[dict[str, str]]:
    """A flat, printable lineage table — the evidence for the model card."""
    rows: list[dict[str, str]] = []
    for spec in feature_set.specs:
        rows.append(
            {
                "feature": spec.name,
                "kind": spec.kind,
                "point_in_time": spec.point_in_time,
                "sources": ", ".join(spec.sources) or "-",
                "lagged_outcome": ", ".join(spec.prior_period_sources) or "-",
                "description": spec.description,
            }
        )
    return rows


def assert_frame_matches(
    columns: Iterable[str], feature_set: FeatureSet, allow_extra: Sequence[str] = ()
) -> None:
    """Fail if a built matrix has columns the feature set never declared.

    An undeclared column is one that skipped the lineage check, which is the
    whole mechanism. Silently ignoring it would defeat the point.
    """
    present = set(columns)
    permitted = set(feature_set.names) | set(feature_set.passthrough) | set(allow_extra)
    undeclared = sorted(present - permitted)
    missing = sorted(set(feature_set.names) - present)
    problems = []
    if undeclared:
        problems.append(f"columns present but never declared: {undeclared}")
    if missing:
        problems.append(f"declared features absent from the frame: {missing}")
    if problems:
        raise LeakageError("; ".join(problems))
