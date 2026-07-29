"""Provenance contracts for the tables this project PUBLISHES.

`src/features/spec.py` declares where a FEATURE comes from, and
`tests/features/test_matrix_provenance.py` holds the committed training matrix to
that declaration. Neither reaches the artifacts a reader actually opens, and that
gap has a name: `work_queue_backtest.csv` shipped a column called
`recoverable_amt`, which is `sim_denied_amount` with the marker renamed off — one
row per claim, beside a `recommended_action` column telling an analyst to appeal
because "expected recovery exceeds the cost of working it". Nothing failed,
because nothing was checking that file. The same sentence describes
`overall_prior_denial_rate` three months earlier. Twice is a pattern, and the
pattern is that the guards were pointed at the training matrix while the defects
landed on the artifacts.

This module is the check, and it is DECLARATION-BASED for the same reason the
feature guard is: **a name check cannot catch a name defect.** `recoverable_amt`
looks perfectly reasonable. What condemns it is that it is copied out of
`sim_denied_amount`, and only a declaration can say so.

Three rules, in the order they bite:

1. **Coverage.** Every tabular file under a published root must be covered by a
   registered `PublishedSurface`. An artifact nobody declared is precisely the
   condition that let `recoverable_amt` run for four commits, so a new output —
   a Phase 5 dashboard extract, an API response table, a demo Parquet — fails
   the build until it is declared here. This is the rule that generalises;
   the other two only act on what rule 1 has already caught.
2. **Completeness.** A surface with a declared schema publishes exactly the
   columns it declared. An undeclared column is one that skipped rule 3.
3. **Marking (CLAUDE.md §3.2).** A column whose declared LINEAGE touches a
   `sim_` source carries the `sim_` marker in its own name.

Rule 3 has one carve-out, and it is deliberately expensive to use. A column that
is metadata about OUR system rather than a statement about the simulated world
may set `marker_exempt` to a written justification. The settled example is the
`split` fold label: it reads a simulated date, but a fold assignment is something
we invented for our experiment, not an attribute of the claim (QA ruling C,
2026-07-28). There is no boolean and no default — exempting a column costs a
sentence, in the declaration, that a reviewer will read.

**Adding a Phase 5 surface.** Register it in `PUBLISHED_SURFACES` below. If it is
claim-grain, declare a schema; if it is an aggregate whose columns are metric
vocabulary, register it with an `undeclared_reason` saying so. Either way the
decision is written down, which is the whole point.
"""

from __future__ import annotations

import csv
import pathlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# The four classes CLAUDE.md §3.1 defines. Nothing else is a classification here:
# a fifth class invented in this file would be a taxonomy the provenance register
# does not have, and the register is the document of record.
Classification = Literal["SOURCE", "DERIVED", "REFERENCE", "SIMULATED"]

SIMULATED_MARKER = "sim_"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Directories whose contents are PUBLISHED — read by a person or served to one.
# `models_artifacts/` is gitignored and still counts: a gitignored file is one
# drag away from a slide deck, and the exposure test the team settled on is
# "committed, or surfaced by a dashboard", not "tracked by git".
PUBLISHED_ROOTS: tuple[str, ...] = (
    "artifacts",
    "models_artifacts",
    "dashboard/demo_data",
)

# Coverage (rule 1) applies to anything tabular. `.duckdb` is listed so the
# Phase 5 demo extract cannot appear undeclared; its columns are not read here.
TABULAR_SUFFIXES: tuple[str, ...] = (".csv", ".parquet", ".duckdb")


class ProvenanceError(AssertionError):
    """A published table breaks its provenance contract."""


@dataclass(frozen=True)
class ColumnProvenance:
    """One published column, its class, and what it was computed from."""

    name: str
    classification: Classification
    description: str
    sources: tuple[str, ...] = ()
    # Non-empty justification for publishing simulated lineage without the
    # marker. See the module docstring; this is not a boolean on purpose.
    marker_exempt: str = ""

    @property
    def directly_simulated(self) -> bool:
        """Simulated by its own class, or reading a `sim_` column at one hop."""
        return self.classification == "SIMULATED" or any(
            source.startswith(SIMULATED_MARKER) for source in self.sources
        )

    @property
    def carries_marker(self) -> bool:
        return SIMULATED_MARKER in self.name

    def __post_init__(self) -> None:
        if self.marker_exempt and self.carries_marker:
            raise ValueError(
                f"{self.name}: carries the {SIMULATED_MARKER!r} marker AND claims an exemption "
                "from carrying it. One of the two is wrong."
            )


@dataclass(frozen=True)
class PublishedSchema:
    """The complete column contract for one published table.

    Lineage is resolved TRANSITIVELY across the columns declared here, and that
    is not a refinement — it is the difference between a working rule and a
    decorative one. A one-hop check asks only whether a column names a `sim_`
    source directly, so laundering a simulated value through a single unmarked
    intermediate defeats it: `tier` reads `sim_days_to_deadline`, `tier_rank`
    reads `tier`, and at one hop `tier_rank` looks clean. The transitive closure
    is what makes "the marker survives the rename" survive two renames. This
    module's first draft got it wrong and its own test caught it.
    """

    name: str
    grain: str
    columns: tuple[ColumnProvenance, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def column(self, name: str) -> ColumnProvenance:
        for candidate in self.columns:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    def simulated_lineage(self, name: str) -> tuple[str, ...]:
        """The simulated sources `name` rests on, however many hops away.

        Empty when the column has no simulated ancestry. Returns the sources
        rather than a boolean because a failure message that names the lineage is
        the difference between a guard someone fixes and one someone silences.
        """
        found: set[str] = set()
        seen: set[str] = set()
        frontier = [name]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            # A source that is not declared here is a warehouse column, and its
            # own name is then the only evidence available.
            declared = next((c for c in self.columns if c.name == current), None)
            simulated = current.startswith(SIMULATED_MARKER) or (
                declared is not None and declared.classification == "SIMULATED"
            )
            if simulated:
                found.add(current)
            if declared is not None:
                frontier.extend(declared.sources)
        return tuple(sorted(found))

    def __post_init__(self) -> None:
        for column in self.columns:
            if column.marker_exempt and not self.simulated_lineage(column.name):
                raise ValueError(
                    f"{self.name}.{column.name}: marker_exempt is set on a column with no "
                    "simulated lineage, direct or transitive. An exemption from a rule that "
                    "does not apply reads, to the next person, as evidence that the rule does "
                    "apply and was waived."
                )


@dataclass(frozen=True)
class PublishedSurface:
    """A path (or glob) this project writes, and the contract it publishes under."""

    glob: str
    grain: str
    schema: PublishedSchema | None = None
    undeclared_reason: str = ""

    def __post_init__(self) -> None:
        if (self.schema is None) == (not self.undeclared_reason):
            raise ValueError(
                f"{self.glob}: register a surface with EITHER a schema OR a written "
                "undeclared_reason, never both and never neither."
            )


# --------------------------------------------------------------------------
# The declared schemas.
# --------------------------------------------------------------------------

# The work queue. Every dollar figure, probability and countdown on this table is
# a statement about a denial that never happened, so every one of them carries
# the marker. The five exempt columns are the queue's own policy output — which
# bucket OUR rules put a claim in, in what order OUR ranking works it, and what
# OUR system recommends. Those are the `split` case: metadata about the
# experiment, not an attribute of the claim (QA ruling C). None of them is a
# money or rate claim, which is the misreading §3.2 exists to prevent.
_POLICY_OUTPUT = (
    "Output of this project's own ranking policy, not an attribute of the claim — the "
    "same class as the `split` fold label under QA ruling C (2026-07-28). Two different "
    "configs give two different values for the same denial. Carries no dollar or rate "
    "reading, so it cannot be mistaken for a claim about real money."
)

WORK_QUEUE_SCHEMA = PublishedSchema(
    name="work_queue",
    grain="one row per denied claim, keyed on claim_sk",
    columns=(
        ColumnProvenance(
            name="claim_sk",
            classification="DERIVED",
            description="Surrogate key of the CMS synthetic claim.",
            sources=("clm_uniq_id",),
        ),
        ColumnProvenance(
            name="sim_p_overturn",
            classification="SIMULATED",
            description="Model C's calibrated probability that an appeal is overturned. "
            "Predicts a simulated outcome from simulated adjudication facts.",
            sources=("sim_appeal_outcome",),
        ),
        ColumnProvenance(
            name="sim_recoverable_amt",
            classification="SIMULATED",
            description="Dollars denied and therefore recoverable on appeal. This is "
            "sim_denied_amount under another name; it shipped as `recoverable_amt` and that "
            "is the defect this whole module exists for.",
            sources=("sim_denied_amount",),
        ),
        ColumnProvenance(
            name="sim_expected_recovery_amt",
            classification="SIMULATED",
            description="sim_p_overturn x sim_recoverable_amt x the fit-fold recovery ratio.",
            sources=("sim_p_overturn", "sim_recoverable_amt"),
        ),
        ColumnProvenance(
            name="sim_expected_net_recovery",
            classification="SIMULATED",
            description="Expected recovery less the appeal processing cost. The score.",
            sources=("sim_expected_recovery_amt",),
        ),
        ColumnProvenance(
            name="sim_days_to_deadline",
            classification="SIMULATED",
            description="Days left to file, counted from the simulated denial posting plus "
            "the configured filing window.",
            sources=("sim_denial_review_date",),
        ),
        ColumnProvenance(
            name="sim_denial_category",
            classification="SIMULATED",
            description="CARC-derived denial category from the simulation layer.",
            sources=("sim_denial_category",),
        ),
        ColumnProvenance(
            name="tier",
            classification="DERIVED",
            description="Queue tier: DEADLINE_CRITICAL / MANDATORY_REVIEW / VALUE / "
            "BELOW_COST / EXPIRED.",
            sources=("sim_days_to_deadline", "sim_denial_category"),
            marker_exempt=_POLICY_OUTPUT,
        ),
        ColumnProvenance(
            name="tier_rank",
            classification="DERIVED",
            description="Integer sort key for `tier`.",
            sources=("tier",),
            marker_exempt=_POLICY_OUTPUT,
        ),
        ColumnProvenance(
            name="queue_position",
            classification="DERIVED",
            description="1-based position in the ranked worklist.",
            sources=("tier_rank", "sim_expected_net_recovery"),
            marker_exempt=_POLICY_OUTPUT,
        ),
        ColumnProvenance(
            name="recommended_action",
            classification="DERIVED",
            description="The sentence an analyst reads, mapped from `tier`.",
            sources=("tier",),
            marker_exempt=_POLICY_OUTPUT,
        ),
        ColumnProvenance(
            name="as_of",
            classification="DERIVED",
            description="The instant this queue was built for. A parameter of the build, "
            "not a fact about any claim on it.",
            sources=("sim_denial_review_date",),
            marker_exempt=_POLICY_OUTPUT,
        ),
    ),
)


# --------------------------------------------------------------------------
# The registry. Rule 1 reads this and nothing else.
# --------------------------------------------------------------------------

_EVALUATION_TABLE = (
    "Model-evaluation output, one row per slice or bin rather than per claim. Its columns "
    "are metric vocabulary (n, positives, base_rate, roc_auc) describing OUR measurement, "
    "not fields of the simulated world. The simulated content is in the row VALUES — the "
    "payer archetypes, the denial categories — and §3.2 governs names. The mitigation of "
    "record for that is the per-directory provenance README the run writes, which names "
    "slice_payer.csv explicitly (CLAUDE.md §3.5). FLAGGED FOR REVIEW: `base_rate` in "
    "slice_payer.csv is a simulated denial rate under a bare name, which is structurally "
    "the overall_prior_denial_rate defect. It is recorded here rather than renamed because "
    "renaming the metric vocabulary reaches every table in the report; a reviewer should "
    "decide it deliberately, not inherit it from this file."
)

PUBLISHED_SURFACES: tuple[PublishedSurface, ...] = (
    PublishedSurface(
        glob="models_artifacts/model_c/work_queue_*.csv",
        grain="one row per denied claim",
        schema=WORK_QUEUE_SCHEMA,
    ),
    PublishedSurface(
        glob="artifacts/features/model_a_training_matrix.parquet",
        grain="one row per claim",
        undeclared_reason="The committed Model A training matrix. Held to a declaration "
        "already, by tests/features/test_matrix_provenance.py against MODEL_A_FEATURES.specs "
        "— the lineage check there is the same rule as rule 3 here, applied at the feature "
        "declaration instead of the file. Registered so coverage is complete, not exempted "
        "from checking.",
    ),
    PublishedSurface(
        glob="models_artifacts/model_*/slice_*.csv",
        grain="one row per slice",
        undeclared_reason=_EVALUATION_TABLE,
    ),
    PublishedSurface(
        glob="models_artifacts/model_a/calibration_curve.csv",
        grain="one row per score decile",
        undeclared_reason=_EVALUATION_TABLE,
    ),
    PublishedSurface(
        glob="models_artifacts/model_a/shap_*.csv",
        grain="one row per feature",
        undeclared_reason="Global SHAP importance. One row per FEATURE, and the feature "
        "names in the `feature` column already carry their own markers because the feature "
        "declarations enforce it; the columns are importance statistics about the model.",
    ),
    PublishedSurface(
        glob="models_artifacts/model_c/appeal_selection.csv",
        grain="one row per denial category",
        undeclared_reason=_EVALUATION_TABLE,
    ),
)


def surface_for(relative_path: str) -> PublishedSurface | None:
    """The registered surface covering `relative_path`, or None."""
    path = pathlib.PurePosixPath(relative_path)
    for surface in PUBLISHED_SURFACES:
        if path.match(surface.glob):
            return surface
    return None


def published_files(root: pathlib.Path = REPO_ROOT) -> list[str]:
    """Every tabular file sitting under a published root, repo-relative."""
    found: list[str] = []
    for published_root in PUBLISHED_ROOTS:
        base = root / published_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in TABULAR_SUFFIXES:
                found.append(path.relative_to(root).as_posix())
    return found


def read_columns(path: pathlib.Path) -> tuple[str, ...] | None:
    """Column names of a CSV or Parquet, without loading the data.

    Returns None for a format this cannot read cheaply (`.duckdb`), which is
    honest about the limit rather than reporting an empty column list as though
    the file had no columns.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="") as handle:
            header = next(csv.reader(handle), [])
        return tuple(header)
    if suffix == ".parquet":
        import pyarrow.parquet as pq

        return tuple(pq.read_schema(path).names)
    return None


# --------------------------------------------------------------------------
# The assertions. Callable from a test, and from the write path itself.
# --------------------------------------------------------------------------


def assert_schema_is_marked(schema: PublishedSchema) -> None:
    """Rule 3, on the declaration alone — no file needed."""
    offenders = [
        (column.name, schema.simulated_lineage(column.name))
        for column in schema.columns
        if not column.carries_marker
        and not column.marker_exempt
        and schema.simulated_lineage(column.name)
    ]
    if offenders:
        raise ProvenanceError(
            f"{schema.name}: columns with simulated lineage and no {SIMULATED_MARKER!r} marker: "
            f"{offenders}. CLAUDE.md §3.2 — the marker survives the rename, so a CSV, a "
            "dashboard header or an API field carries its provenance with it. If the column is "
            "metadata about our system rather than a statement about the simulated world, say "
            "so in marker_exempt."
        )


def assert_columns_match_schema(columns: Iterable[str], schema: PublishedSchema) -> None:
    """Rule 2: exactly the declared columns, no more and no fewer."""
    present = list(columns)
    undeclared = sorted(set(present) - set(schema.names))
    missing = sorted(set(schema.names) - set(present))
    problems = []
    if undeclared:
        problems.append(f"published but never declared: {undeclared}")
    if missing:
        problems.append(f"declared but absent: {missing}")
    if problems:
        raise ProvenanceError(
            f"{schema.name} does not match its declaration — "
            + "; ".join(problems)
            + f". Declare it in {__name__}: an undeclared column is one that skipped the "
            "lineage check, which is the entire mechanism."
        )


def assert_publishable(columns: Iterable[str], schema: PublishedSchema) -> None:
    """Rules 2 and 3 together. Call this before writing or serving a table.

    Cheap enough to run on every build, which is the point: the guarantee should
    be structural rather than something a test remembers to look for later.
    """
    assert_schema_is_marked(schema)
    assert_columns_match_schema(columns, schema)


def assert_every_published_file_is_covered(root: pathlib.Path = REPO_ROOT) -> None:
    """Rule 1: nothing is published that nobody declared."""
    uncovered = [path for path in published_files(root) if surface_for(path) is None]
    if uncovered:
        raise ProvenanceError(
            f"published tables covered by no registered surface: {uncovered}. Register each "
            f"one in {__name__}: PUBLISHED_SURFACES, with a schema if it is claim-grain or a "
            "written undeclared_reason if it is not. An artifact nobody declared is how "
            "`recoverable_amt` shipped for four commits with nothing objecting."
        )


def audit_rows(schema: PublishedSchema) -> list[dict[str, str]]:
    """A flat lineage table for the provenance register and the model card."""
    return [
        {
            "column": column.name,
            "classification": column.classification,
            "sources": ", ".join(column.sources) or "-",
            "marker": (
                "carried"
                if column.carries_marker
                else "EXEMPT"
                if column.marker_exempt
                else "not required"
            ),
            "description": column.description
            + (f" EXEMPTION: {column.marker_exempt}" if column.marker_exempt else ""),
        }
        for column in schema.columns
    ]
