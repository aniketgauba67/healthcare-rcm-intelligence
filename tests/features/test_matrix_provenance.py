"""The committed training matrix must stay provenance-honest and diff-quiet.

`artifacts/features/model_a_training_matrix.parquet` is the only data file
committed to git, so it is the only one a reader can open from a clean clone with
no database — and therefore the one most likely to be read out of context. Its
per-column classification is published in `docs/provenance_register.md`. These
tests hold the two properties that document depends on.

The prefix test is the one that earns its place. `overall_prior_denial_rate`
shipped in this matrix unprefixed for three commits: it is computed entirely from
`sim_denial_flag`, so it is an aggregate of a *fabricated* denial, but its name
said "denial rate" with nothing marking it as simulated. Nothing failed, because
nothing was checking. A reader opening the Parquet would have read it as a real
Medicare book rate. It is now `sim_overall_prior_denial_rate`, and this test is
what stops the next one.
"""

from __future__ import annotations

import json

import pytest

from src.features.build import MODEL_A_FEATURES
from src.features.store import (
    MANIFEST_PATH,
    MATRIX_PATH,
    persist_training_matrix,
    read_manifest,
    read_persisted_matrix,
)

# The complete set of matrix columns that are NOT simulated, with the class each
# one carries in docs/provenance_register.md. Kept as a literal rather than
# derived from the frame: the point is that adding a column forces an explicit
# provenance decision here, in a test, rather than defaulting to silence.
SOURCE_COLUMNS = frozenset({"billed_charge_amt", "drg_cd", "provider_state_cd", "prvdr_num"})
DERIVED_COLUMNS = frozenset(
    {
        "claim_sk",
        "diagnosis_count",
        "length_of_stay_days",
        "log_billed_charge_amt",
        "patient_age_years",
        # A modelling fold label, not a claim fact. It reads a SIMULATED date and
        # still keeps its bare name, on its own merits: a fold assignment is
        # metadata about OUR experiment, not a statement about the simulated
        # world, and §3.2 governs simulated values. The earlier justification —
        # that the §4.1 guard discovers the split column by name and a rename
        # would blind it — was REJECTED by QA ruling C (2026-07-28) while the
        # outcome was upheld. A guard must never be the reason a correctness-
        # improving rename cannot happen.
        "split",
    }
)


@pytest.fixture(scope="module")
def matrix():
    frame = read_persisted_matrix()
    if frame is None:
        pytest.skip(f"no committed matrix at {MATRIX_PATH}")
    return frame


def test_every_column_is_classified(matrix) -> None:
    """No column may be neither sim_-prefixed nor explicitly classified."""
    unclassified = sorted(
        c
        for c in matrix.columns
        if not c.startswith("sim_") and c not in SOURCE_COLUMNS | DERIVED_COLUMNS
    )
    assert not unclassified, (
        f"{unclassified} carry no provenance class. Every column in the committed "
        "matrix must be sim_-prefixed (SIMULATED) or listed above with its class, "
        "and docs/provenance_register.md must agree."
    )


def test_no_simulated_feature_hides_behind_an_unprefixed_name() -> None:
    """A feature whose declared lineage is simulated must carry the prefix (§3.2).

    Checked against the DECLARATION, not the column name, which is the only way
    to catch the failure this test exists for: a feature aggregating
    `sim_denial_flag` is simulated no matter what it is called.
    """
    offenders = []
    for spec in MODEL_A_FEATURES.specs:
        lineage = (*spec.sources, *spec.prior_period_sources)
        touches_simulation = any(source.startswith("sim_") for source in lineage)
        if touches_simulation and not spec.name.startswith("sim_"):
            offenders.append((spec.name, lineage))
    assert not offenders, (
        f"features computed from simulated inputs without the sim_ prefix: {offenders}. "
        "docs/project_rules.md §3.2 — the prefix survives the engineering step, so a matrix, a "
        "SHAP plot or a dashboard column carries its provenance with it."
    )


def test_rewriting_the_artifact_is_byte_identical(matrix, tmp_path) -> None:
    """Same frame in, same bytes out — no wall clock, no other ambient input.

    This is the property, stated directly rather than by grepping the manifest for
    words that look like timestamps. `make train` rewrites this artifact on every
    training run and one test in the suite trains against live Postgres, so a
    single clock field made the COMMITTED file dirty after any full run. Reviewers
    who learn to ignore a diff on a committed artifact are exactly how a real
    content change slips through unnoticed; qa reverted a spurious timestamp diff
    twice before the field was dropped.

    Writes to tmp_path, never to the committed artifact.
    """
    first = tmp_path / "matrix_a.parquet"
    second = tmp_path / "matrix_b.parquet"
    features = [c for c in matrix.columns if c != "split"]
    persist_training_matrix(matrix[features], path=first)
    persist_training_matrix(matrix[features], path=second)

    def manifest_bytes(path):
        return (path.parent / f"{path.stem}.json").read_bytes()

    assert manifest_bytes(first) == manifest_bytes(second)
    assert first.read_bytes() == second.read_bytes()


def test_the_manifest_describes_the_file_beside_it(matrix) -> None:
    manifest = read_manifest()
    if manifest is None:
        pytest.skip(f"no manifest at {MANIFEST_PATH}")
    assert manifest["rows"] == len(matrix)
    assert manifest["feature_count"] == len(manifest["features"])
    assert set(manifest["features"]) | set(manifest["passthrough"]) | {
        manifest["split_column"]
    } == set(matrix.columns)


def test_the_manifest_is_valid_json_with_no_nan_tokens() -> None:
    """Bare NaN is valid Python and invalid JSON; app-engineer parses these files."""
    if not MANIFEST_PATH.exists():
        pytest.skip(f"no manifest at {MANIFEST_PATH}")
    text = MANIFEST_PATH.read_text()
    json.loads(text)
    assert "NaN" not in text
