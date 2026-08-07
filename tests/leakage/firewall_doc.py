"""Parse `docs/simulated_forbidden_columns.md` into a machine-checkable classification.

docs/project_rules.md §4.5 forbids ml-engineer from reading `src/simulation/`, so that document
is the interface through which the leakage boundary is published, and
`config/model.yaml: forbidden_features` is meant to be a transcription of it. A
transcription that nobody diffs is exactly how the current placeholder list ended up
matching zero real columns for 5 of its 11 patterns. This module turns the prose into
sets so the two can be diffed by a test.

The parser is deliberately section-aware rather than "grep every backtick": the
document names *tables* in the right-hand column of its markdown tables, names safe
columns inside forbidden sections when explaining a boundary, and carries a whole
section (§5, Model C) whose boundary is a different one. A naive scrape would fold all
of that together and produce a set that looks authoritative and is wrong.

A parser that silently under-extracts is worse than no test at all, so
`tests/leakage/test_firewall_doc_parser.py` pins the parser against independently
hand-read anchors and asserts that every generated column lands in exactly one bucket.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

# Warehouse join keys. Not sim_ columns and not generator output; §7 of the document
# addresses them separately and says neither may be a feature.
JOIN_KEYS = frozenset({"claim_sk", "clm_id"})

_BACKTICKED = re.compile(r"`([^`]+)`")
_IDENTIFIER = re.compile(r"\b(sim_[a-z0-9_]+|claim_sk|clm_id)\b")
_SECTION = re.compile(r"^## (\d+)\.", re.MULTILINE)


def _identifiers(text: str) -> list[str]:
    """Identifiers appearing inside backticks, in document order.

    Reading only inside backticks keeps prose words out; reading identifiers *within*
    each span keeps expressions like `sim_denied_amount > 0` usable.
    """
    out: list[str] = []
    for span in _BACKTICKED.findall(text):
        out.extend(_IDENTIFIER.findall(span))
    return out


def _table_rows(text: str) -> list[list[str]]:
    """Cells of every markdown table row, excluding header and separator rows."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() in {"column", "event type"}:
            continue
        rows.append(cells)
    return rows


@dataclass(frozen=True)
class FirewallDoc:
    """The document's classification, as sets of names.

    `forbidden_columns` and `permitted_columns` are column names stated explicitly.
    `forbidden_tables` / `permitted_tables` are classified wholesale, and are resolved
    against the real schema by `model_a_forbidden()` rather than guessed at here.
    """

    forbidden_columns: frozenset[str]
    permitted_columns: frozenset[str]
    forbidden_tables: frozenset[str]
    permitted_tables: frozenset[str]
    latent_columns: frozenset[str]
    stamp_columns: frozenset[str]
    sections: dict[str, str] = field(compare=False, default_factory=dict)

    def model_a_forbidden(self, schema: dict[str, list[str]]) -> frozenset[str]:
        """Every column that must never be a Model A feature, resolved against `schema`.

        `schema` maps table name -> column names, taken from the generator output or
        from live Postgres. Wholesale-forbidden tables contribute all of their columns.

        The join keys are in the result: §7 rules that neither `claim_sk` nor `clm_id`
        may be a feature (`claim_sk` is assigned in source-file order and acts as a
        smuggled date feature). They are still legitimately present on a matrix as its
        key, so the training-matrix guard exempts declared identifier columns rather
        than this set being narrowed.
        """
        forbidden: set[str] = set()
        for table, columns in schema.items():
            if table in self.forbidden_tables:
                forbidden |= {c for c in columns if c not in JOIN_KEYS}
            else:
                forbidden |= {c for c in columns if c in self.forbidden_columns}
            forbidden |= {c for c in columns if c in self.stamp_columns}
        return frozenset(forbidden)

    def model_a_permitted(self, schema: dict[str, list[str]]) -> frozenset[str]:
        """Every column the document permits as a Model A feature, resolved against `schema`.

        Wholesale-permitted tables (§3's "all columns" rows) contribute their columns
        here; `sim_auth_request_date` is permitted that way rather than by name, and a
        check that only consulted the named columns would reject it.
        """
        permitted: set[str] = set()
        for table, columns in schema.items():
            for column in columns:
                if self.classify(table, column) == "permitted":
                    permitted.add(column)
        return frozenset(permitted)

    def classify(self, table: str, column: str) -> str:
        """Bucket a single generated column: forbidden / permitted / join_key."""
        if column in JOIN_KEYS:
            return "join_key"
        if column in self.stamp_columns:
            return "forbidden"
        if table in self.forbidden_tables:
            return "forbidden"
        if column in self.forbidden_columns:
            return "forbidden"
        if column in self.permitted_columns or table in self.permitted_tables:
            return "permitted"
        return "unclassified"


def parse(path: pathlib.Path) -> FirewallDoc:
    """Read the firewall document and return its classification."""
    text = path.read_text()
    bounds = [(m.group(1), m.start()) for m in _SECTION.finditer(text)]
    sections: dict[str, str] = {}
    for i, (number, start) in enumerate(bounds):
        end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
        sections[number] = text[start:end]

    missing = {"1", "2", "3", "4", "6", "7"} - set(sections)
    if missing:
        raise ValueError(
            f"{path.name} is missing sections {sorted(missing)} — the parser in "
            "tests/leakage/firewall_doc.py is pinned to its structure and must be "
            "updated in the same commit that restructures the document"
        )

    forbidden: set[str] = set()
    permitted: set[str] = set()
    forbidden_tables: set[str] = set()
    permitted_tables: set[str] = set()

    # §1 — latent generator internals. Markdown table; first cell is the column, the
    # second is the table it lives on, so only the first cell may be read as a column.
    latent = {ident for row in _table_rows(sections["1"]) for ident in _identifiers(row[0])}
    forbidden |= latent

    # §2 — post-submission columns, forbidden for Model A. The "### Whole tables"
    # subsection is read for table names only: its prose legitimately names
    # `sim_event_ts`, which §4 classifies as safe inside the filtered subset. Reading
    # it as a column here would contradict the section that actually rules on it.
    body, _, whole_tables = sections["2"].partition("### Whole tables")
    forbidden |= set(_identifiers(body))
    for line in whole_tables.splitlines():
        if not line.strip().startswith("- **"):
            continue
        names = _identifiers(line)
        # `sim_workflow_events` is listed here but explicitly defers to §4 for its
        # safe subset, so only tables stated as "every column" are wholesale.
        if names and "every column" in line.lower():
            forbidden_tables.add(names[0])

    # §3 — permitted for Model A. Markdown table; an "all columns" left cell means the
    # right cell lists wholesale-permitted tables.
    for row in _table_rows(sections["3"]):
        if len(row) < 2:
            continue
        if row[0].lower().startswith("all columns"):
            permitted_tables |= set(_identifiers(row[1]))
        else:
            permitted |= set(_identifiers(row[0]))

    # §4 — sim_workflow_events, column by column. A note that calls the column
    # forbidden, or says it is never a feature, forbids it; everything else is safe
    # only once the rows have been filtered to at-or-before submission. That row
    # filter is not a column-name property, so it is enforced by the value-based
    # detectors rather than by the blacklist.
    for row in _table_rows(sections["4"]):
        if len(row) < 2:
            continue
        names = _identifiers(row[0])
        if not names:
            continue
        note = row[1].lower()
        if "forbidden" in note or "never a feature" in note:
            forbidden |= set(names)
        else:
            permitted |= set(names)

    # §6 — provenance stamps, present on every generated table.
    stamps = set(_identifiers(sections["6"]))

    # §7 — the warehouse keys. Forbidden as features; see model_a_forbidden().
    forbidden |= set(_identifiers(sections["7"])) & JOIN_KEYS

    # A column stated forbidden anywhere outranks a permitted mention: §4's per-column
    # ruling and §2's outcome list must not be softened by a passing reference.
    permitted -= forbidden
    permitted -= stamps

    return FirewallDoc(
        forbidden_columns=frozenset(forbidden),
        permitted_columns=frozenset(permitted),
        forbidden_tables=frozenset(forbidden_tables),
        permitted_tables=frozenset(permitted_tables),
        latent_columns=frozenset(latent),
        stamp_columns=frozenset(stamps),
        sections=sections,
    )
