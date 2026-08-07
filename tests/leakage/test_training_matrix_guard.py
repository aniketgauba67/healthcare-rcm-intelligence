"""GATE 2, second assertion: no forbidden column, and nothing derived from one,
may enter a training matrix (docs/project_rules.md §4.1).

DISCOVERY CONTRACT — how a matrix gets checked
----------------------------------------------
The guard finds matrices without needing to know how they were built:

1. the path in the `RCM_FEATURE_MATRIX` environment variable, if set; or
2. any `*.parquet` / `*.csv` written under `artifacts/features/` or `data/features/`; or
3. `src.features.build_training_matrix()`, if `src/features/` exposes a callable of
   that name taking no required arguments.

Any one of those is enough.

WHICH COLUMNS ARE FEATURES — DECLARED, NOT GUESSED
--------------------------------------------------
A matrix says which of its columns are the key, the label, the fold assignment and
the time axis; `tests/leakage/roles.py` reads that declaration and everything else is
a feature that must survive every probe. This replaces the name sets this module used
to carry (`{"is_train", "split", "fold"}`, and five spellings of "label"), which is
[SPLIT-DISCOVERY] — the debt qa-reviewer-p8 left and team-lead scheduled into Phase 5.

The reason it had to change is team-lead's QA RULING C: name-based discovery made a
correct rename look dangerous, and *a guard must never be the reason a
correctness-improving rename cannot happen*. The fold column may now be called
anything; the temporal check follows the declaration. An UNdeclared column is treated
as a feature, so forgetting to declare makes this guard stricter, never laxer — and
`roles.validate()` polices the only remaining way out, which is declaring a leaky
column into a non-feature role.

WHY THE NAME CHECK IS NOT THE POINT
-----------------------------------
A forbidden column that has been renamed, logged, binned, divided by the billed amount,
or aggregated over the claim's whole history is still the answer key, and none of that
shows in a column name. The probes that matter compare feature *values* against the
generator's own post-submission columns, which is why the strongest of them are marked
`integration`: they need the real feature matrix alongside the real warehouse. See
`tests/leakage/detectors.py` for how each one works and what it was calibrated against,
and `tests/leakage/test_detectors.py` for the proof that each fires.

NOT-YET-BUILT IS NOT THE SAME AS PASSING
----------------------------------------
Phase 4 has no feature store at the time of writing, so these tests skip. A skip that
outlives the thing it was waiting for is how a gate quietly stops being one, so
`test_guard_is_wired_once_a_feature_store_exists` fails the build the moment
`src/features/` contains a module and no matrix can be found — the guard cannot be
bypassed by writing the feature store somewhere it does not look.
"""

from __future__ import annotations

import os
import pathlib

import pandas as pd
import pytest

from tests.leakage import detectors, roles

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FEATURES_PACKAGE = REPO_ROOT / "src" / "features"
MATRIX_DIRECTORIES = (REPO_ROOT / "artifacts" / "features", REPO_ROOT / "data" / "features")


def _read(path: pathlib.Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def _discover() -> list[tuple[str, pd.DataFrame, roles.ColumnRoles | None]]:
    """Every training matrix the contract above can find, with the roles it declares."""
    found: list[tuple[str, pd.DataFrame, roles.ColumnRoles | None]] = []

    def add(source: str, matrix: pd.DataFrame, path: pathlib.Path | None) -> None:
        declared = roles.declare(matrix, path, source)
        if declared is not None:
            roles.validate(declared, matrix)
        found.append((source, matrix, declared))

    override = os.environ.get("RCM_FEATURE_MATRIX")
    if override:
        path = pathlib.Path(override)
        assert path.exists(), f"RCM_FEATURE_MATRIX points at a missing file: {path}"
        add(str(path), _read(path), path)

    for directory in MATRIX_DIRECTORIES:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix in {".parquet", ".csv"}:
                add(str(path.relative_to(REPO_ROOT)), _read(path), path)

    try:  # pragma: no cover - exercised once ml-engineer lands the feature store
        import src.features as features_package

        builder = getattr(features_package, "build_training_matrix", None)
        if callable(builder):
            add("src.features.build_training_matrix()", builder(), None)
    except Exception as exc:  # noqa: BLE001 - a broken builder is reported, not hidden
        pytest.fail(f"src.features.build_training_matrix() could not be called: {exc!r}")

    return found


def _feature_columns(matrix: pd.DataFrame, declared: roles.ColumnRoles | None) -> list[str]:
    """Every column with no declared non-feature role.

    An undeclared matrix has no non-feature columns at all: nothing is excused,
    which is the strict end of the failure direction and not the lax one.
    """
    if declared is None:
        return list(matrix.columns)
    return declared.feature_columns(matrix)


@pytest.fixture(scope="module")
def matrices() -> list[tuple[str, pd.DataFrame, roles.ColumnRoles | None]]:
    return _discover()


@pytest.fixture(scope="module")
def require_matrices(matrices):
    if not matrices:
        pytest.skip(
            "no training matrix found — see the discovery contract in this module's "
            "docstring; test_guard_is_wired_once_a_feature_store_exists fails if this "
            "skip outlives the feature store"
        )
    return matrices


def test_guard_is_wired_once_a_feature_store_exists(matrices):
    """The skip above must not become permanent.

    `src/features/` holding only its `__init__.py` means Phase 4 has not started and
    there is nothing to check. A module in there with no discoverable matrix means the
    feature store is being built somewhere this guard cannot see it, which is
    indistinguishable from no guard at all.
    """
    modules = [p.name for p in FEATURES_PACKAGE.glob("*.py") if p.name != "__init__.py"]
    if not modules:
        pytest.skip("src/features/ is empty — Phase 4 feature work has not started")
    assert matrices, (
        f"src/features/ contains {modules} but no training matrix could be discovered, "
        "so the docs/project_rules.md §4.1 leakage guard is checking nothing. Expose the matrix by "
        "any route in this module's discovery contract: write it to artifacts/features/, "
        "set RCM_FEATURE_MATRIX, or add a no-argument build_training_matrix() to "
        "src/features/__init__.py."
    )


def test_no_forbidden_column_enters_a_matrix_by_name(require_matrices, forbidden_columns):
    """The cheap pass. Necessary, and nowhere near sufficient."""
    failures: list[str] = []
    for source, matrix, declared in require_matrices:
        findings = detectors.name_findings(_feature_columns(matrix, declared), forbidden_columns)
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "forbidden columns present in a training matrix:\n" + "\n".join(failures)


def test_no_surrogate_key_is_smuggled_in_as_a_feature(require_matrices):
    """§7: claim_sk encodes source-file order, and therefore time."""
    failures: list[str] = []
    for source, matrix, declared in require_matrices:
        if "claim_sk" not in matrix.columns:
            continue
        findings = detectors.identifier_findings(
            matrix[_feature_columns(matrix, declared)], matrix["claim_sk"]
        )
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "surrogate keys used as features:\n" + "\n".join(failures)


def test_no_unrecognised_date_enters_a_matrix(require_matrices, permitted_columns):
    """Any date-typed feature the firewall document does not name is rejected.

    This carries the weight the dependency probe gives up on dates; see the carve-out
    note in `detectors.dependency_findings`.
    """
    failures: list[str] = []
    for source, matrix, declared in require_matrices:
        findings = detectors.unrecognised_date_findings(
            matrix[_feature_columns(matrix, declared)], permitted_columns
        )
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "unrecognised date columns in a training matrix:\n" + "\n".join(failures)


def test_split_is_temporal_where_the_matrix_declares_one(require_matrices):
    """docs/project_rules.md §4.3. A random split leaves training rows past the earliest test row.

    The fold column and the time axis are whatever the matrix DECLARES them to be,
    so this check survives any rename of either — see this module's docstring and
    `tests/leakage/roles.py`.
    """
    failures: list[str] = []
    for source, matrix, declared in require_matrices:
        if declared is None or declared.split is None or declared.time is None:
            continue
        findings = detectors.temporal_findings(
            matrix[declared.time], roles.train_mask(matrix, declared)
        )
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "non-temporal split:\n" + "\n".join(failures)
