"""The firewall-document parser must be trustworthy before anything is built on it.

Everything in this suite compares `config/model.yaml` and ml-engineer's feature
matrices against a set of names extracted from prose. If that extraction quietly
degrades — a heading is renumbered, a table gains a column, a list becomes a
paragraph — the sets shrink and every downstream test goes green while checking
nothing. That is the failure mode this module exists to prevent, so the parser is
pinned three ways: against anchors read by hand out of the document, against the real
generated schema, and against a floor on how much it extracted.
"""

from __future__ import annotations

import pytest

from tests.leakage import firewall_doc

# Read by hand out of docs/simulated_forbidden_columns.md. Deliberately independent of
# the parser: if the parser is what tells us what the document says, it cannot also be
# what proves it read it correctly.
ANCHOR_FORBIDDEN = {
    "sim_latent_p",  # §1 latent
    "sim_provider_quality_latent",  # §1 latent, the pure answer key
    "sim_label_noise_applied",  # §1 latent
    "sim_appeal_latent_p",  # §1 latent
    "sim_denial_flag",  # §2 outcome
    "sim_denial_driver_mechanism",  # §2 outcome, explains the label
    "sim_denied_amount",  # §2 money, equivalent to the label
    "sim_paid_amount",  # §2 money
    "sim_denial_review_date",  # §2 dates, null-indicator reconstructs the label
    "sim_days_to_payment",  # §2 durations
    "sim_appeal_level",  # §4 workflow events, forbidden by name
    "sim_event_sk",  # §4 surrogate key, never a feature
    "sim_provenance",  # §6 stamp
    "sim_seed",  # §6 stamp
}

ANCHOR_PERMITTED = {
    "sim_payer_id",
    "sim_service_line_id",
    "sim_submission_date",
    "sim_filing_limit_days",
    "sim_late_filing_flag",
    "sim_days_service_to_submission",
    "sim_event_type",  # §4, safe within the filtered subset
    "sim_touch_minutes",  # §4, safe within the filtered subset
}

ANCHOR_FORBIDDEN_TABLES = {"sim_appeals", "sim_operating_costs"}
ANCHOR_PERMITTED_TABLES = {
    "sim_authorization_eligibility",
    "sim_documentation_coding",
    "sim_payer",
    "sim_service_line",
}


@pytest.mark.parametrize("column", sorted(ANCHOR_FORBIDDEN))
def test_anchor_forbidden_columns_are_parsed_as_forbidden(firewall, column):
    assert column in firewall.forbidden_columns or column in firewall.stamp_columns, (
        f"{column} is forbidden in docs/simulated_forbidden_columns.md but the parser "
        "did not extract it — tests/leakage/firewall_doc.py needs updating for the "
        "document's current structure"
    )
    assert column not in firewall.permitted_columns


@pytest.mark.parametrize("column", sorted(ANCHOR_PERMITTED))
def test_anchor_permitted_columns_are_parsed_as_permitted(firewall, column):
    assert column in firewall.permitted_columns, (
        f"{column} is permitted in docs/simulated_forbidden_columns.md but the parser "
        f"did not extract it as such"
    )
    assert column not in firewall.forbidden_columns


def test_wholesale_table_classifications(firewall):
    assert firewall.forbidden_tables == ANCHOR_FORBIDDEN_TABLES
    assert firewall.permitted_tables == ANCHOR_PERMITTED_TABLES


def test_workflow_events_is_not_swallowed_as_a_wholesale_table(firewall):
    """§2 lists it under "Whole tables" but §4 rules on it column by column.

    Treating it wholesale would forbid `sim_event_type` and `sim_touch_minutes`, which
    the document permits inside the at-or-before-submission subset, and would make the
    config-agreement test demand a blacklist the document does not support.
    """
    assert "sim_workflow_events" not in firewall.forbidden_tables


def test_model_c_section_does_not_leak_into_the_model_a_boundary(firewall):
    """§5 permits denial outcomes and money for Model C — a different boundary.

    Those columns must stay forbidden for Model A. If §5 were scraped into the
    permitted set, the blacklist would lose exactly the columns that reconstruct the
    Model A label.
    """
    for column in ("sim_denial_flag", "sim_denied_amount", "sim_paid_amount"):
        assert column in firewall.forbidden_columns
        assert column not in firewall.permitted_columns


def test_every_generated_column_is_bucketed(firewall, generated_schema):
    """No generated column may fall through the parser unclassified.

    This is the completeness guarantee. `tests/simulation/test_forbidden_columns_doc.py`
    already fails the build if a generated column is absent from the document; this
    asserts the stronger property that the column is not merely *mentioned* but lands
    in a bucket the blacklist can act on.
    """
    unbucketed = [
        f"{table}.{column}"
        for table, columns in sorted(generated_schema.items())
        for column in columns
        if firewall.classify(table, column) == "unclassified"
    ]
    assert not unbucketed, (
        "generated columns the firewall document does not clearly classify as "
        f"forbidden or permitted: {unbucketed}"
    )


def test_parser_extracted_a_plausible_amount(firewall, generated_schema):
    """A floor, so that a structural change cannot silently empty the sets.

    Anchors catch a parser that loses a specific column; this catches one that loses a
    whole section.
    """
    assert len(firewall.forbidden_columns) >= 20
    assert len(firewall.permitted_columns) >= 10
    assert len(firewall.stamp_columns) == 3
    assert len(firewall.latent_columns) == 4
    resolved = firewall.model_a_forbidden(generated_schema)
    assert len(resolved) >= 35, f"only {len(resolved)} forbidden columns resolved"


def test_join_keys_are_forbidden_as_features(firewall, generated_schema):
    """§7: neither key may be a feature, `claim_sk` least of all."""
    resolved = firewall.model_a_forbidden(generated_schema)
    assert firewall_doc.JOIN_KEYS <= resolved


def test_parser_rejects_a_restructured_document(tmp_path):
    """Renumbering the sections must break loudly, not silently return empty sets."""
    mangled = tmp_path / "mangled.md"
    mangled.write_text("# No numbered sections here\n\nsome prose\n")
    with pytest.raises(ValueError, match="missing sections"):
        firewall_doc.parse(mangled)
