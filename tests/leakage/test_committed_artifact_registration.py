"""A committed data file that no document classifies is a §3.3 hole.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). Expected RED until the matrix is registered. Do not delete it to go
green.

docs/project_rules.md §3.3: the provenance register and the data dictionary "must be updated
in the same PR that adds or changes any table or column".
`artifacts/features/model_a_training_matrix.parquet` has been committed since
cd3e30c — 20,867 rows x 44 columns mixing SOURCE, DERIVED and SIMULATED — and
appears in neither document. Team-lead verified this independently on the ml
branch on 2026-07-28 and made it BLOCKING for Phase 4 acceptance.

Why this file and not some other artifact: it is the ONLY data file a reader can
open from a clean clone with no database. Everything else worth classifying lives
in Postgres, behind `docker compose up` and a load. So it is simultaneously the
most likely artifact an outside reader inspects and the only one whose columns
nothing explains — a reader opening it sees `sim_denial_flag` next to
`billed_charge_amt` next to `overall_prior_denial_rate` with no statement of
which are real CMS values, which are computed, and which this project generated.

The register needs to say what it is, that `make features` regenerates it, its
grain (one row per claim), and that every `sim_`-prefixed column is SIMULATED.
"""

from __future__ import annotations

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MATRIX = _REPO_ROOT / "artifacts" / "features" / "model_a_training_matrix.parquet"

_DOCS = (
    _REPO_ROOT / "docs" / "provenance_register.md",
    _REPO_ROOT / "docs" / "data_dictionary.md",
)


@pytest.mark.parametrize("doc", _DOCS, ids=lambda path: path.name)
def test_the_committed_training_matrix_is_registered(doc: pathlib.Path) -> None:
    if not _MATRIX.exists():
        pytest.skip(f"{_MATRIX.relative_to(_REPO_ROOT)} is not committed on this tree")

    assert doc.exists(), f"{doc.relative_to(_REPO_ROOT)} is missing"
    text = doc.read_text()

    assert "model_a_training_matrix" in text, (
        f"{doc.relative_to(_REPO_ROOT)} never mentions model_a_training_matrix, but the parquet "
        "is COMMITTED and is the only data file a reader can open from a clean clone with no "
        "database. docs/project_rules.md §3.3 requires the register and the dictionary to cover it. State "
        "what it is, `make features` as the regeneration path, its grain (one row per claim, "
        "20,867), the per-column provenance, and explicitly that every sim_-prefixed column is "
        "SIMULATED."
    )


def test_the_registration_states_the_simulated_classification() -> None:
    """Naming the file is not classifying it.

    A one-line 'we also ship a parquet' entry would satisfy a substring check and
    none of §3.3. The register has to say the columns are SIMULATED, because that
    is the whole point of the register.
    """
    if not _MATRIX.exists():
        pytest.skip("training matrix is not committed on this tree")

    register = _DOCS[0]
    text = register.read_text()
    if "model_a_training_matrix" not in text:
        pytest.skip("covered by the registration test above; nothing to check yet")

    start = text.find("model_a_training_matrix")
    section = text[max(0, start - 2000) : start + 4000]

    assert "SIMULATED" in section, (
        "the training-matrix entry in docs/provenance_register.md does not classify its "
        "simulated columns as SIMULATED. Registering the file without the classification "
        "records that it exists and leaves unanswered the only question the register is for."
    )
    assert "make features" in section, (
        "the training-matrix entry does not name `make features` as the regeneration path. A "
        "committed derived artifact a reader cannot regenerate is a number they have to trust."
    )
