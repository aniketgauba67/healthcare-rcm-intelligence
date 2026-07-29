"""Leakage enforcement for the feature store (CLAUDE.md §4, NON-NEGOTIABLE).

Two jobs:

1. **Agreement.** `config/model.yaml: forbidden_features` must equal the set of
   columns that `docs/simulated_forbidden_columns.md` names as forbidden for
   Model A. That document is the §4.5 firewall interface — the ml-engineer never
   opens `src/simulation/`, so the document is the only statement of where the
   pre-submission boundary falls. A hand-copied list would drift from it
   silently, so it is parsed and compared instead of trusted.

2. **Guarding.** Nothing forbidden, and nothing *derived* from something
   forbidden, may enter a training matrix. Two guards are provided: a name-based
   one for a finished matrix, and a lineage-based one for feature definitions
   that declare their source columns. The lineage guard is the real protection —
   the name guard only catches a forbidden column passed through unrenamed.

Model A's boundary is the moment before submission. Model C's is the posting of
the denial, which is a genuinely different and more permissive boundary
(document §5); `model="C"` selects it.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG_PATH = REPO_ROOT / "config" / "model.yaml"
FIREWALL_DOC_PATH = REPO_ROOT / "docs" / "simulated_forbidden_columns.md"

Model = Literal["A", "C"]

# A backticked span that looks like a SQL identifier: starts with a letter, ends
# with a letter or digit. The trailing-character rule is what keeps the prose
# token `sim_` (from "Neither is a `sim_` column") out of the parsed sets.
_IDENTIFIER = re.compile(r"`([a-z][a-z0-9_]*[a-z0-9])`")

# The document's own section numbers, used in error messages so a failure points
# at the paragraph a human has to go read.
_SECTION_A_FORBIDDEN = (1, 2, 4, 6, 7)


class LeakageError(AssertionError):
    """A forbidden column, or something derived from one, reached a matrix."""


@dataclass(frozen=True)
class FirewallDoc:
    """The forbidden/permitted sets parsed out of the firewall document."""

    latent_internals: frozenset[str]  # §1 — never a feature, any model
    outcome: frozenset[str]  # §2 Outcome
    money: frozenset[str]  # §2 Money
    post_submission_dates: frozenset[str]  # §2 Dates and durations
    whole_tables: frozenset[str]  # §2 Whole tables
    workflow_columns: frozenset[str]  # §4 rows marked forbidden
    provenance_stamps: frozenset[str]  # §6
    warehouse_keys: frozenset[str]  # §7
    permitted_model_a: frozenset[str] = field(default=frozenset())  # §3 columns
    permitted_tables: frozenset[str] = field(default=frozenset())  # §3 "all columns" rows

    @property
    def model_a_forbidden(self) -> frozenset[str]:
        """Every column the document names as forbidden for Model A.

        Excludes the whole-table names of §2: those are tables, not columns, and
        are carried separately in `config/model.yaml: forbidden_tables`.
        """
        return frozenset().union(
            self.latent_internals,
            self.outcome,
            self.money,
            self.post_submission_dates,
            self.workflow_columns,
            self.provenance_stamps,
            self.warehouse_keys,
        )


def _sections(text: str) -> dict[int, str]:
    """Split the document on its numbered `## N.` headings."""
    parts = re.split(r"^## (\d+)\.", text, flags=re.MULTILINE)
    # parts[0] is the preamble; then alternating (number, body).
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _table_rows(block: str) -> list[list[str]]:
    """Markdown table rows as cell lists, skipping header and separator rows."""
    rows: list[list[str]] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue  # separator
        rows.append(cells)
    return rows


def _first_column_identifiers(block: str) -> frozenset[str]:
    """Identifiers appearing in the first cell of each table row."""
    found: set[str] = set()
    for cells in _table_rows(block):
        found.update(_IDENTIFIER.findall(cells[0]))
    return frozenset(found)


def _subsection(block: str, heading: str) -> str:
    """The text under a `### heading` up to the next `###` or end of block."""
    pattern = re.compile(rf"^### {re.escape(heading)}\s*$(.*?)(?=^### |\Z)", re.M | re.S)
    match = pattern.search(block)
    if match is None:  # pragma: no cover - guarded by the agreement test
        raise LeakageError(f"firewall document is missing the '### {heading}' subsection")
    return match.group(1)


def parse_firewall_doc(path: pathlib.Path | None = None) -> FirewallDoc:
    """Parse `docs/simulated_forbidden_columns.md` into forbidden/permitted sets.

    The parse is deliberately structural (section number, then subsection
    heading, then table position) rather than a bare scan for backticks: §2 and
    §7 contain explanatory prose that mentions permitted columns such as
    `sim_event_ts`, and a scan would forbid them.
    """
    doc = (path or FIREWALL_DOC_PATH).read_text()
    sections = _sections(doc)
    missing = [n for n in (*_SECTION_A_FORBIDDEN, 3) if n not in sections]
    if missing:
        raise LeakageError(f"firewall document is missing sections {missing}")

    s2 = sections[2]
    # §2 "Whole tables" is a bullet list; the table name is the first backticked
    # token on each bullet, and later tokens on the line are examples.
    whole_tables: set[str] = set()
    for line in _subsection(s2, "Whole tables").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            names = _IDENTIFIER.findall(stripped)
            if names:
                whole_tables.add(names[0])

    # §4 marks a column forbidden in its notes cell rather than by position.
    workflow: set[str] = set()
    for cells in _table_rows(sections[4]):
        if len(cells) < 2:
            continue
        notes = cells[1].lower()
        if "forbidden" in notes or "never a feature" in notes:
            workflow.update(_IDENTIFIER.findall(cells[0]))

    # §3 classifies four config/precondition tables wholesale ("all columns"),
    # so their columns are permitted without being named individually.
    permitted_tables: set[str] = set()
    for cells in _table_rows(sections[3]):
        if len(cells) >= 2 and cells[0].strip().lower() == "all columns":
            permitted_tables.update(_IDENTIFIER.findall(cells[1]))

    return FirewallDoc(
        latent_internals=_first_column_identifiers(sections[1]),
        outcome=frozenset(_IDENTIFIER.findall(_subsection(s2, "Outcome"))),
        money=frozenset(_IDENTIFIER.findall(_subsection(s2, "Money"))),
        post_submission_dates=frozenset(
            _IDENTIFIER.findall(_subsection(s2, "Dates and durations after submission"))
        ),
        whole_tables=frozenset(whole_tables),
        workflow_columns=frozenset(workflow),
        provenance_stamps=frozenset(_IDENTIFIER.findall(sections[6])),
        warehouse_keys=frozenset(_IDENTIFIER.findall(sections[7])),
        permitted_model_a=_first_column_identifiers(sections[3]),
        permitted_tables=frozenset(permitted_tables),
    )


def load_model_config(path: pathlib.Path | None = None) -> dict:
    """Load `config/model.yaml`."""
    return yaml.safe_load((path or MODEL_CONFIG_PATH).read_text())


def _config_sets(config: Mapping) -> dict[str, frozenset[str]]:
    table_columns: set[str] = set()
    for columns in (config.get("forbidden_table_columns") or {}).values():
        table_columns.update(columns)
    return {
        "documented": frozenset(config.get("forbidden_features") or []),
        "table_columns": frozenset(table_columns),
        "derived": frozenset(config.get("forbidden_derived_features") or {}),
        "source": frozenset(config.get("forbidden_source_features") or {}),
        "defensive": frozenset(config.get("forbidden_features_defensive") or []),
        "crosswalk": frozenset(config.get("forbidden_crosswalk_display_features") or []),
    }


def forbidden_columns(model: Model = "A", config: Mapping | None = None) -> frozenset[str]:
    """Every column name barred from `model`'s feature matrix.

    Model A takes the union of every `forbidden_*` block. Model C starts from
    the same union, then adds back the post-denial facts a denials analyst can
    legitimately see (document §5) and adds its own appeal-side forbids.
    """
    cfg = dict(config or load_model_config())
    parts = _config_sets(cfg)
    union = frozenset().union(*parts.values())
    if model == "A":
        return union
    if model != "C":  # pragma: no cover - Literal already narrows this
        raise ValueError(f"unknown model {model!r}; expected 'A' or 'C'")
    model_c = cfg.get("model_c") or {}
    permitted = frozenset(model_c.get("permitted_beyond_model_a") or [])
    return (union - permitted) | frozenset(model_c.get("forbidden_features") or [])


def _offenders(columns: Iterable[str], blacklist: frozenset[str]) -> dict[str, str]:
    """Map each offending column to the forbidden name that caught it.

    Exact match first, then substring: a feature called `sim_denied_amount_log`
    or `log_sim_paid_amount` is a forbidden column wearing a hat. Substring
    matching is intentionally blunt — a false positive costs a rename, a false
    negative ships a leak.
    """
    hits: dict[str, str] = {}
    for column in columns:
        lowered = column.lower()
        if lowered in blacklist:
            hits[column] = lowered
            continue
        for forbidden in blacklist:
            if forbidden in lowered:
                hits[column] = forbidden
                break
    return hits


def assert_no_forbidden_columns(
    columns: Iterable[str],
    model: Model = "A",
    config: Mapping | None = None,
    context: str = "training matrix",
) -> None:
    """Fail if any column is, or is named after, a forbidden column."""
    blacklist = forbidden_columns(model, config)
    hits = _offenders(columns, blacklist)
    if hits:
        detail = ", ".join(f"{col} (matches {why})" for col, why in sorted(hits.items()))
        raise LeakageError(
            f"forbidden columns in {context} for Model {model}: {detail}. "
            "See CLAUDE.md §4 and docs/simulated_forbidden_columns.md."
        )


def assert_sources_safe(
    sources_by_feature: Mapping[str, Iterable[str]],
    model: Model = "A",
    config: Mapping | None = None,
) -> None:
    """Fail if any engineered feature is *computed from* a forbidden column.

    This is the guard that matters. A renamed passthrough is caught by the name
    check above, but `payer_prior_denial_rate` reveals nothing about its
    lineage — the feature store declares its source columns and they are checked
    here. Historical-rate features computed on a prior period are the intended
    exception and declare that separately (CLAUDE.md §4.2).
    """
    blacklist = forbidden_columns(model, config)
    problems: list[str] = []
    for feature, sources in sorted(sources_by_feature.items()):
        hits = _offenders(sources, blacklist)
        if hits:
            named = ", ".join(sorted(hits))
            problems.append(f"{feature} <- {named}")
    if problems:
        raise LeakageError(
            f"features derived from forbidden columns for Model {model}: {'; '.join(problems)}. "
            "See CLAUDE.md §4.1."
        )


def assert_config_agrees_with_doc(
    config: Mapping | None = None, doc_path: pathlib.Path | None = None
) -> None:
    """Fail if `forbidden_features` and the firewall document have drifted.

    Set equality, both directions: a column the document forbids that the config
    omits is an unguarded leak, and a column the config forbids that the
    document does not name is a private opinion about the pre-submission
    boundary, which is exactly what §4.5 says ml-engineer does not get to have.
    """
    cfg = dict(config or load_model_config())
    doc = parse_firewall_doc(doc_path)
    configured = frozenset(cfg.get("forbidden_features") or [])
    documented = doc.model_a_forbidden

    missing = sorted(documented - configured)
    extra = sorted(configured - documented)
    problems: list[str] = []
    if missing:
        problems.append(f"forbidden in the document but not in config/model.yaml: {missing}")
    if extra:
        problems.append(f"in config/model.yaml but not forbidden by the document: {extra}")

    configured_tables = frozenset(cfg.get("forbidden_tables") or [])
    # sim_workflow_events is a whole-table entry in the document but is filtered
    # by row rather than dropped (§4), so it is configured under `workflow_events`.
    row_filtered = frozenset({"sim_workflow_events"})
    documented_tables = doc.whole_tables - row_filtered
    if configured_tables != documented_tables:
        problems.append(
            f"forbidden_tables {sorted(configured_tables)} != document §2 whole tables "
            f"{sorted(documented_tables)}"
        )
    if row_filtered & doc.whole_tables and not (cfg.get("workflow_events") or {}).get(
        "boundary_event"
    ):
        problems.append("sim_workflow_events needs a workflow_events.boundary_event rule")

    if problems:
        raise LeakageError(
            "config/model.yaml has drifted from docs/simulated_forbidden_columns.md — "
            + "; ".join(problems)
        )
