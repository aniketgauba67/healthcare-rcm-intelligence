"""The §4.5 firewall document must not drift from the schema it describes.

`docs/simulated_forbidden_columns.md` is the interface that lets ml-engineer
populate `config/model.yaml: forbidden_features` without reading
`src/simulation/` (CLAUDE.md §4.5). A generated column that is not mentioned
there is a column nobody has classified as pre- or post-submission — which is
exactly how a leak gets shipped. These tests fail the build in that case, so
adding a column forces a decision about it.
"""

from __future__ import annotations

import pathlib

import pytest

from src.simulation.validate import LATENT_ONLY_COLUMNS

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs" / "simulated_forbidden_columns.md"

# Tables the document classifies wholesale rather than column by column.
_TABLE_LEVEL = {
    "sim_authorization_eligibility",
    "sim_documentation_coding",
    "sim_payer",
    "sim_service_line",
    "sim_appeals",
    "sim_operating_costs",
}
# Warehouse join keys, deliberately not sim_-prefixed and handled in their own
# section of the document.
_JOIN_KEYS = {"claim_sk", "clm_id"}


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC.exists(), f"missing firewall document: {DOC}"
    return DOC.read_text()


def test_every_generated_column_is_classified(result, doc_text):
    unclassified: list[str] = []
    for table, df in sorted(result.tables.items()):
        if table in _TABLE_LEVEL:
            assert table in doc_text, f"{table} is classified wholesale but is not named"
            continue
        for column in df.columns:
            if column in _JOIN_KEYS:
                continue
            if column not in doc_text:
                unclassified.append(f"{table}.{column}")
    assert not unclassified, (
        "columns generated but not classified in docs/simulated_forbidden_columns.md: "
        f"{unclassified}"
    )


def test_every_table_is_named_in_the_document(result, doc_text):
    missing = [name for name in result.tables if name not in doc_text]
    assert not missing, f"tables absent from the firewall document: {missing}"


def test_latent_columns_are_all_documented_as_forbidden(doc_text):
    """The answer keys must be named explicitly, not merely implied."""
    section = doc_text.split("## 2.")[0]
    for column in LATENT_ONLY_COLUMNS:
        assert column in section, f"{column} is not in the forbidden-latent section"


def test_document_states_it_is_authoritative(doc_text):
    """Team-lead ruling (b): this file is the authoritative list, and it has to
    say so — an interface document that reads as advisory gets treated as such."""
    lowered = doc_text.lower()
    assert "authoritative" in lowered
    assert "config/model.yaml" in doc_text
