"""The committed training matrix must be discoverable, current, and clean.

`tests/leakage/test_training_matrix_guard.py` can only check a matrix it can
find, and the discovery contract it publishes is satisfied here by
`artifacts/features/model_a_training_matrix.parquet` plus a no-argument
`src.features.build_training_matrix()`. Two ways that protection can rot:

* the routes stop working — the export is dropped, or the builder grows a
  required argument. Then the guard skips, and a skip reads like a pass.
* the file goes stale — the feature store changes and nobody re-runs
  `make features`. Then the guard checks a matrix no model was fitted on.

The first is checked here without a database. The second needs the warehouse and
is marked `integration`.

OWNERSHIP: written by ml-engineer in cd3e30c, ADOPTED by qa-reviewer-p10 under
team-lead's 2026-07-27 ruling that tests/leakage/ is qa's — a guard authored by
the party it constrains is not a guard. It stays here rather than moving to
tests/features/ because its subject is the discovery contract THIS directory
publishes, not the behaviour of src/features/store.py. Adopting it meant
reviewing it, which turned up the defect below.

DEFECT FOUND AND FIXED ON ADOPTION (qa-reviewer-p10). The staleness check called
`store.build_training_matrix(refresh=True)`, and that function PERSISTS what it
builds. So the guard rewrote the committed artifact as a side effect of checking
it, with two consequences. `uv run pytest` left `git status` dirty on any machine
with a warehouse — qa hit exactly that churn and reverted it by hand before it
could be committed. Worse, the guard repaired the condition it existed to detect:
a genuinely stale file failed the first run and then passed every run afterwards,
because the first run had overwritten it. A guard whose act of measuring fixes
its subject reports the warehouse-restore failure mode in miniature — from
outside, "was never stale" and "was stale and got quietly rewritten" look
identical. The check now builds through the same code path WITHOUT persisting,
and asserts the artifact's digest is byte-identical before and after itself.
"""

from __future__ import annotations

import inspect
import pathlib

import pandas as pd
import pytest

import src.features as features_package
from src.features import store
from src.features.build import MODEL_A_FEATURES
from src.features.leakage import forbidden_columns, load_model_config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def matrix() -> pd.DataFrame:
    if not store.MATRIX_PATH.exists():
        pytest.fail(
            f"{store.MATRIX_PATH.relative_to(REPO_ROOT)} is missing. It is committed on "
            "purpose so the §4.1 probes run on a clean clone; rebuild it with `make features`."
        )
    return pd.read_parquet(store.MATRIX_PATH)


@pytest.fixture(scope="module")
def manifest() -> dict:
    found = store.read_manifest()
    assert found is not None, "the persisted matrix has no manifest beside it"
    return found


def test_the_package_exposes_a_no_argument_builder() -> None:
    """Discovery route 3. A required argument here disarms the guard silently."""
    builder = getattr(features_package, "build_training_matrix", None)
    assert callable(builder), "src.features.build_training_matrix() is the guard's fallback route"
    required = [
        name
        for name, parameter in inspect.signature(builder).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    assert not required, (
        f"build_training_matrix() must take no required arguments; needs {required}"
    )


def test_the_builder_returns_the_persisted_matrix(matrix: pd.DataFrame) -> None:
    built = features_package.build_training_matrix()
    assert list(built.columns) == list(matrix.columns)
    assert len(built) == len(matrix)


def test_the_matrix_is_exported_where_the_guard_looks() -> None:
    assert store.MATRIX_PATH.parent == REPO_ROOT / "artifacts" / "features"
    assert store.MATRIX_PATH.suffix == ".parquet"


def test_only_model_a_matrices_live_in_the_guarded_directory() -> None:
    """The guard's blacklist is Model A's, and Model C may legitimately see the denial.

    A Model C matrix written next to this one would fail the guard correctly and
    for entirely the wrong reason, and the fix someone would reach for is
    loosening the guard.
    """
    exported = sorted(p.name for p in store.MATRIX_PATH.parent.glob("*.parquet"))
    assert exported == [store.MATRIX_PATH.name], (
        f"unexpected matrices under artifacts/features/: {exported}. Model C writes to "
        "models_artifacts/model_c/, which the Model A guard does not scan."
    )


def test_the_matrix_carries_a_label_a_date_and_a_split(matrix: pd.DataFrame) -> None:
    """Without these the temporal probe has nothing to check and skips."""
    for column in ("sim_denial_flag", "sim_submission_date", store.SPLIT_COLUMN):
        assert column in matrix.columns
    assert set(matrix[store.SPLIT_COLUMN].unique()) == {"train", "test"}


def test_the_split_column_is_unambiguous(matrix: pd.DataFrame) -> None:
    """The guard picks one of {is_train, split, fold} out of a frozenset — order undefined."""
    present = {c for c in ("is_train", "split", "fold") if c in matrix.columns}
    assert present == {store.SPLIT_COLUMN}, f"ambiguous split columns: {sorted(present)}"


def test_the_split_is_forward_in_time(matrix: pd.DataFrame) -> None:
    dates = pd.to_datetime(matrix["sim_submission_date"])
    is_train = matrix[store.SPLIT_COLUMN].eq("train")
    assert dates[is_train].max() < dates[~is_train].min()


def test_no_forbidden_column_is_in_the_exported_matrix(matrix: pd.DataFrame) -> None:
    """The name check, on the file rather than on an in-memory frame.

    The passthrough columns are exported deliberately — the guard needs the key,
    the label and the date — so they are excluded here the same way the guard
    excludes them, and everything else must be a declared feature.
    """
    carried = {
        "claim_sk",
        "sim_denial_flag",
        "sim_submission_date",
        "prvdr_num",
        store.SPLIT_COLUMN,
    }
    features = [c for c in matrix.columns if c not in carried]
    assert set(features) == set(MODEL_A_FEATURES.names)
    offenders = sorted(set(features) & forbidden_columns("A"))
    assert not offenders, f"forbidden columns in the exported matrix: {offenders}"


def test_the_manifest_describes_the_file_beside_it(matrix: pd.DataFrame, manifest: dict) -> None:
    assert manifest["rows"] == len(matrix)
    assert manifest["features"] == list(MODEL_A_FEATURES.names)
    assert manifest["parquet_sha256_16"] == store._file_digest(store.MATRIX_PATH)


def test_the_manifest_matches_the_current_leakage_config(manifest: dict) -> None:
    """A widened blacklist with no rebuild means the guard is checking old columns."""
    assert manifest["leakage_config_digest"] == store.leakage_config_digest(load_model_config())


@pytest.mark.integration
def test_the_exported_matrix_matches_a_fresh_build(matrix: pd.DataFrame) -> None:
    """Staleness check. Needs the warehouse; skips without it.

    Builds through the same path as `build_training_matrix(refresh=True)` but
    stops short of `persist_training_matrix`, so checking the artifact cannot
    rewrite it. See the module docstring: the persisting version healed the
    staleness it was there to find, and left the working tree dirty besides.
    """
    from sqlalchemy import create_engine

    from src.features.build import build_model_a_frame
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    before = store._file_digest(store.MATRIX_PATH)
    cfg = load_model_config()
    fresh = store.label_and_split(build_model_a_frame(create_engine(url), cfg), cfg)
    pd.testing.assert_frame_equal(
        fresh.reset_index(drop=True), matrix.reset_index(drop=True), check_dtype=False
    )
    assert store._file_digest(store.MATRIX_PATH) == before, (
        "checking the persisted matrix rewrote it. A staleness guard that repairs its own "
        "subject can only ever fail once, and it dirties the working tree on every run."
    )
