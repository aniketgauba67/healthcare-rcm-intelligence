"""The check that would have caught `recoverable_amt`, and the reason it will again.

`models_artifacts/model_c/work_queue_backtest.csv` shipped a column called
`recoverable_amt`. It is `sim_denied_amount` copied out under a name with the
simulated marker removed, one row per claim, beside a `recommended_action` column
reading "Appeal — expected recovery exceeds the cost of working it". A dashboard
renders those column names as its headers, so the file said, to a user, that a
real recoverable amount was worth chasing.

Nothing failed. The declaration-based guard that caught the same defect on
`overall_prior_denial_rate` was scoped to the Model A training matrix and did not
reach the Model C artifact path — "nothing failed because nothing was checking",
for the second time.

So the tests below check three things, and only the first one is really about
the work queue:

* rule 3, on the declaration: simulated lineage implies the marker, with the
  negative control that puts the old name back and watches it fail;
* rule 2, on the frame the builder actually returns, so the declaration cannot
  drift away from the artifact;
* rule 1, coverage: any tabular file under a published root that no registered
  surface covers is a failure. That is the rule that generalises to Phase 5 —
  a dashboard extract or an API response table cannot appear undeclared, which
  is the condition that let this run for four commits.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from src.features.provenance import (
    PUBLISHED_SURFACES,
    REPO_ROOT,
    WORK_QUEUE_SCHEMA,
    ColumnProvenance,
    ProvenanceError,
    PublishedSchema,
    PublishedSurface,
    assert_columns_match_schema,
    assert_every_published_file_is_covered,
    assert_publishable,
    assert_schema_is_marked,
    published_files,
    read_columns,
    surface_for,
)
from src.models.work_queue import AppealEconomics, build_work_queue

ECONOMICS = AppealEconomics(
    processing_cost_usd=45.0,
    recovery_ratio=0.8,
    filing_window_days=120,
    deadline_critical_days=14,
    mandatory_review_categories=("MEDICAL_NECESSITY",),
)


@pytest.fixture(scope="module")
def queue() -> pd.DataFrame:
    as_of = pd.Timestamp("2023-06-01")
    frame = pd.DataFrame(
        [
            {
                "claim_sk": i,
                "sim_denied_amount": 1_000.0 * (i + 1),
                "sim_denial_review_date": as_of - pd.Timedelta(days=10 * i),
                "sim_denial_category": "BUNDLING",
            }
            for i in range(6)
        ]
    )
    return build_work_queue(frame, np.linspace(0.1, 0.9, 6), ECONOMICS, as_of=as_of)


# --- rule 3: the marker survives the rename -------------------------------


def test_the_work_queue_declaration_marks_every_simulated_column() -> None:
    assert_schema_is_marked(WORK_QUEUE_SCHEMA)


def test_restoring_the_old_name_fails_and_names_the_column() -> None:
    """The negative control. Without it this test file only proves today is fine.

    Renames `sim_recoverable_amt` back to what actually shipped and asserts the
    check fires, quoting the column and its simulated source — because a guard
    that has never been seen to fail is a guard nobody has tested.
    """
    columns = tuple(
        dataclasses.replace(column, name="recoverable_amt")
        if column.name == "sim_recoverable_amt"
        else column
        for column in WORK_QUEUE_SCHEMA.columns
    )
    regressed = dataclasses.replace(WORK_QUEUE_SCHEMA, columns=columns)

    with pytest.raises(ProvenanceError) as raised:
        assert_schema_is_marked(regressed)
    message = str(raised.value)
    assert "recoverable_amt" in message
    assert "sim_denied_amount" in message, "the failure must name the lineage, not just the column"

    # And it passes again on restore, so the test is measuring the rename and
    # not some other property that happens to be broken.
    assert_schema_is_marked(WORK_QUEUE_SCHEMA)


def test_an_exemption_cannot_be_claimed_where_the_rule_does_not_apply() -> None:
    """`marker_exempt` is a waiver. A waiver on a clean column is misinformation."""
    with pytest.raises(ValueError, match="no simulated lineage"):
        PublishedSchema(
            name="x",
            grain="one row",
            columns=(
                ColumnProvenance(
                    name="claim_sk",
                    classification="DERIVED",
                    description="key",
                    sources=("clm_uniq_id",),
                    marker_exempt="nothing here needs one, which is exactly the problem",
                ),
            ),
        )


def test_lineage_is_resolved_through_an_unmarked_intermediate() -> None:
    """Two renames must not launder a simulated column past the marker rule.

    This is the hole the module shipped with for one draft: `tier` reads
    `sim_days_to_deadline`, `tier_rank` reads `tier`, and a one-hop check sees
    `tier_rank` as clean. The exemption on `tier_rank` is only legitimate if the
    guard knows it needed one.
    """
    assert WORK_QUEUE_SCHEMA.simulated_lineage("tier_rank"), (
        "tier_rank rests on sim_days_to_deadline two hops away; a guard that cannot see "
        "that can be defeated by one unmarked intermediate column."
    )
    assert not WORK_QUEUE_SCHEMA.simulated_lineage("claim_sk")

    laundered = PublishedSchema(
        name="laundered",
        grain="one row per claim",
        columns=(
            ColumnProvenance(
                name="staging_amt",
                classification="DERIVED",
                description="an innocent-looking intermediate",
                sources=("sim_denied_amount",),
            ),
            ColumnProvenance(
                name="recoverable_amt",
                classification="DERIVED",
                description="the defect, one hop further out",
                sources=("staging_amt",),
            ),
        ),
    )
    with pytest.raises(ProvenanceError) as raised:
        assert_schema_is_marked(laundered)
    assert "recoverable_amt" in str(raised.value)
    assert "sim_denied_amount" in str(raised.value)


def test_every_exemption_is_written_prose_not_a_shrug() -> None:
    """The exemption's cost is that a reviewer reads it, so it has to say something."""
    for column in WORK_QUEUE_SCHEMA.columns:
        if column.marker_exempt:
            assert len(column.marker_exempt.split()) >= 12, (
                f"{column.name}: an exemption from CLAUDE.md §3.2 in {column.marker_exempt!r} "
                "is a decision, and the declaration is where it gets justified."
            )


# --- rule 2: the declaration matches what the builder returns --------------


def test_the_built_queue_publishes_exactly_the_declared_columns(queue: pd.DataFrame) -> None:
    assert_publishable(queue.columns, WORK_QUEUE_SCHEMA)


def test_an_undeclared_column_on_the_queue_fails(queue: pd.DataFrame) -> None:
    extra = queue.assign(net_recovery_usd=queue["sim_expected_net_recovery"])
    with pytest.raises(ProvenanceError, match="net_recovery_usd"):
        assert_columns_match_schema(extra.columns, WORK_QUEUE_SCHEMA)


def test_every_money_and_probability_column_on_the_queue_is_marked(queue: pd.DataFrame) -> None:
    """The property the rename was for, asserted on the artifact rather than the spec."""
    unmarked = [
        column
        for column in queue.columns
        if pd.api.types.is_numeric_dtype(queue[column])
        and column not in ("claim_sk", "tier_rank", "queue_position")
        and "sim_" not in column
    ]
    assert not unmarked, (
        f"{unmarked} are numeric columns on a claim-grain worklist with no simulated marker. "
        "A dashboard renders these names as headers; an unmarked simulated dollar amount "
        "there is a user-visible claim about real money."
    )


# --- rule 1: coverage, which is the part that reaches Phase 5 --------------


def test_every_registered_surface_is_well_formed() -> None:
    for surface in PUBLISHED_SURFACES:
        assert (surface.schema is None) != (not surface.undeclared_reason)
        if surface.schema is not None:
            assert_schema_is_marked(surface.schema)
        else:
            assert len(surface.undeclared_reason.split()) >= 12, (
                f"{surface.glob}: registering a surface without a schema is a judgement call "
                "and the reason is where it is recorded."
            )


def test_a_new_published_table_must_be_declared(tmp_path) -> None:
    """A Phase 5 output nobody declared fails. This is the whole generalisation.

    Simulates the dashboard demo extract landing with no registration, which is
    exactly the shape of the original defect: a file that no rule covered.
    """
    extract = tmp_path / "dashboard" / "demo_data"
    extract.mkdir(parents=True)
    (extract / "denial_worklist.csv").write_text("claim_sk,recoverable_amt\n1,100.0\n")

    with pytest.raises(ProvenanceError) as raised:
        assert_every_published_file_is_covered(tmp_path)
    assert "dashboard/demo_data/denial_worklist.csv" in str(raised.value)


def test_the_files_actually_on_disk_are_all_covered() -> None:
    files = published_files()
    if not files:
        pytest.skip("no artifacts generated; run `make train` / `make train-appeal`")
    assert_every_published_file_is_covered()


def test_the_work_queue_csvs_on_disk_match_the_declaration() -> None:
    """Reads the real CSV headers when a run has produced them.

    Skips rather than passing when the artifacts are absent — the directory is
    gitignored, so a clean worktree genuinely has nothing to check, and this
    project's own lesson is that a skip must not be mistaken for a pass. Rule 1
    above is the check that runs unconditionally.
    """
    matched = [
        path
        for path in published_files()
        if (surface := surface_for(path)) is not None and surface.schema is WORK_QUEUE_SCHEMA
    ]
    if not matched:
        pytest.skip("no work queue CSVs on disk; run `make train-appeal`")
    for path in matched:
        columns = read_columns(REPO_ROOT / path)
        assert columns is not None
        assert_columns_match_schema(columns, WORK_QUEUE_SCHEMA)


def test_a_surface_must_carry_a_schema_or_a_reason_never_both() -> None:
    schema = PublishedSchema(name="x", grain="one row", columns=())
    with pytest.raises(ValueError, match="EITHER a schema OR"):
        PublishedSurface(glob="a/b.csv", grain="one row", schema=schema, undeclared_reason="both")
    with pytest.raises(ValueError, match="EITHER a schema OR"):
        PublishedSurface(glob="a/b.csv", grain="one row")
