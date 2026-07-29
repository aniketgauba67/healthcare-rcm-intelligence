"""[SPLIT-DISCOVERY] paid: role discovery follows the declaration, not the name.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). This file is the evidence that the debt qa-reviewer-p8 left is actually
closed, rather than moved.

The property under test, stated as team-lead's QA RULING C states it: **a guard
must never be the reason a correctness-improving rename cannot happen.** So the
tests below rename the fold column to things no name list would ever contain and
require the temporal check to keep working — and, just as importantly, to keep
FAILING on a non-temporal split under those same novel names. A discovery
mechanism that quietly stops checking is the failure this replaces; a rename that
silently disarms the §4.3 probe would be exactly as bad as the name list was.

The second half is the new attack surface that declaration-based discovery
creates and that name-based discovery did not have: if declaring a role excuses a
column from the probes, then a manifest becomes a place to write "this leaky
column is the label" and go green. `roles.validate()` is aimed at that, and
`test_a_declaration_cannot_excuse_*` are the proof it fires.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from tests.leakage import detectors, roles

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORT = REPO_ROOT / "artifacts" / "features" / "model_a_training_matrix.parquet"


def _matrix(split_name: str, split_values: list[str]) -> pd.DataFrame:
    """A tiny matrix with a fold column under an arbitrary name."""
    n = len(split_values)
    return pd.DataFrame(
        {
            "claim_sk": np.arange(n),
            "sim_denial_flag": np.arange(n) % 2 == 0,
            "sim_submission_date": pd.to_datetime("2020-01-01")
            + pd.to_timedelta(np.arange(n), unit="D"),
            "billed_charge_amt": np.linspace(100.0, 900.0, n),
            split_name: split_values,
        }
    )


def _declared(matrix: pd.DataFrame, payload: dict, tmp_path: pathlib.Path) -> roles.ColumnRoles:
    """Round-trip a declaration through the sidecar channel, as a real producer would."""
    path = tmp_path / "matrix.parquet"
    matrix.to_parquet(path, index=False)
    path.with_suffix(".json").write_text(json.dumps(payload))
    declared = roles.declare(matrix, path, str(path))
    assert declared is not None, "the sidecar declaration was not read"
    return declared


# --------------------------------------------------------------------------
# The canonical export declares what the guard needs
# --------------------------------------------------------------------------


def test_the_committed_export_declares_every_role_the_probes_need() -> None:
    """Without a declaration the temporal probe has nothing to follow and skips.

    A skip reads like a pass — the failure mode this whole directory exists to
    avoid — so the one matrix that ships in git must carry a full declaration.
    """
    if not EXPORT.exists():  # pragma: no cover - the export is committed
        pytest.fail(f"{EXPORT.relative_to(REPO_ROOT)} is missing; rebuild with `make features`")
    matrix = pd.read_parquet(EXPORT)
    declared = roles.declare(matrix, EXPORT, str(EXPORT))
    assert declared is not None, (
        f"{EXPORT.name} carries no role declaration. tests/leakage/roles.py reads the sidecar "
        f"manifest {EXPORT.with_suffix('.json').name}; it must name the label, the fold column "
        "and the time column."
    )
    roles.validate(declared, matrix)
    assert declared.label, "the export declares no label"
    assert declared.split, "the export declares no fold column"
    assert declared.time, "the export declares no time column"
    assert declared.keys, "the export declares no key column"


def test_the_time_column_is_still_probed_as_a_feature() -> None:
    """Declaring a `time` role must not excuse a date column from the date probe.

    Three of the four roles are exemptions; `time` deliberately is not. The
    firewall document permits several date columns as features, and the probe that
    rejects an unrecognised one is the only thing standing between the matrix and
    an adjudication date, so a role that switched it off would be a hole.
    """
    matrix = _matrix("split", ["train", "train", "test", "test"])
    declared = roles.ColumnRoles(
        origin="test",
        keys=frozenset({"claim_sk"}),
        label="sim_denial_flag",
        split="split",
        time="sim_submission_date",
    )
    assert "sim_submission_date" in declared.feature_columns(matrix)
    assert "sim_submission_date" not in declared.non_features


# --------------------------------------------------------------------------
# Rename freedom — the debt itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["split", "sim_fold", "partition_2024", "q", "assignment"])
def test_a_renamed_fold_column_is_still_temporally_checked(name: str, tmp_path) -> None:
    """Any name at all. The declaration says which column it is; the check follows."""
    matrix = _matrix(name, ["train"] * 6 + ["test"] * 6)
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": name,
            "passthrough": ["claim_sk"],
        },
        tmp_path,
    )
    roles.validate(declared, matrix)
    assert declared.split == name
    findings = detectors.temporal_findings(
        matrix[declared.time], roles.train_mask(matrix, declared)
    )
    assert not findings, f"a forward-in-time split under the name {name!r} was reported: {findings}"


@pytest.mark.parametrize("name", ["split", "sim_fold", "partition_2024", "q"])
def test_a_renamed_fold_column_still_catches_a_random_split(name: str, tmp_path) -> None:
    """The other half, and the half that matters.

    A rename must not disarm §4.3. If this passed while the test above passed, the
    check would be following the declaration to a column and then not looking at
    it.
    """
    matrix = _matrix(name, ["train", "test"] * 6)  # interleaved: a random split
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": name,
            "passthrough": ["claim_sk"],
        },
        tmp_path,
    )
    findings = detectors.temporal_findings(
        matrix[declared.time], roles.train_mask(matrix, declared)
    )
    assert findings, (
        f"a randomly interleaved split named {name!r} was NOT reported. The declaration is being "
        "read and then ignored, which is worse than the name list it replaced."
    )


def test_a_boolean_fold_column_works_too(tmp_path) -> None:
    """`is_train`-shaped declarations must not need the string spelling."""
    matrix = _matrix("sim_is_train", ["x"] * 12).drop(columns=["sim_is_train"])
    matrix["sim_is_train"] = [True] * 6 + [False] * 6
    declared = _declared(
        matrix,
        {
            "column_roles": {
                "claim_sk": "key",
                "sim_denial_flag": "label",
                "sim_is_train": "split",
                "sim_submission_date": "time",
            }
        },
        tmp_path,
    )
    mask = roles.train_mask(matrix, declared)
    assert mask.tolist() == [True] * 6 + [False] * 6
    assert not detectors.temporal_findings(matrix[declared.time], mask)


def test_a_column_named_split_confers_no_role_by_itself(tmp_path) -> None:
    """The name is no longer load-bearing in either direction.

    Under the old frozenset, a column called `split` was exempt from every probe
    because of its spelling. Now an undeclared column called `split` is a feature
    like any other, and a leaky column cannot be excused by choosing its name.
    """
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    declared = _declared(
        matrix,
        {"label": "sim_denial_flag", "time_column": "sim_submission_date"},
        tmp_path,
    )
    assert declared.split is None
    assert "split" in declared.feature_columns(matrix)


def test_an_undeclared_matrix_has_no_exempt_columns(tmp_path) -> None:
    """Forgetting to declare makes the guard STRICTER. That is the safe direction.

    Asserted through the guard's own `_feature_columns`, not through a
    reimplementation of it — the property only matters if the code that decides
    what gets probed behaves this way.
    """
    from tests.leakage.test_training_matrix_guard import _feature_columns

    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    path = tmp_path / "undeclared.parquet"
    matrix.to_parquet(path, index=False)  # no sidecar written

    assert roles.declare(matrix, path, str(path)) is None
    assert _feature_columns(matrix, None) == list(matrix.columns), (
        "an undeclared matrix exempted something. Nothing may be excused without a "
        "declaration, or the way to disarm the guard becomes writing no manifest."
    )


# --------------------------------------------------------------------------
# A declaration cannot be used as an escape hatch
# --------------------------------------------------------------------------


def test_a_declaration_cannot_excuse_a_column_by_calling_it_a_key(tmp_path) -> None:
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    matrix["sim_paid_amount"] = np.linspace(1.0, 12.0, 12)
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": "split",
            "key_columns": ["claim_sk", "sim_paid_amount"],
        },
        tmp_path,
    )
    with pytest.raises(roles.RoleDeclarationError, match="sim_paid_amount"):
        roles.validate(declared, matrix)


def test_a_declaration_cannot_excuse_a_column_by_calling_it_the_label(tmp_path) -> None:
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    matrix["sim_allowed_amount"] = np.linspace(1.0, 12.0, 12)
    declared = _declared(
        matrix,
        {
            "label": "sim_allowed_amount",
            "time_column": "sim_submission_date",
            "split_column": "split",
        },
        tmp_path,
    )
    with pytest.raises(roles.RoleDeclarationError, match="disagree"):
        roles.validate(declared, matrix)


def test_a_declaration_cannot_excuse_a_measurement_by_calling_it_the_fold(tmp_path) -> None:
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    matrix["sim_denied_amount"] = np.linspace(1.0, 12.0, 12)
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": "sim_denied_amount",
        },
        tmp_path,
    )
    with pytest.raises(roles.RoleDeclarationError, match="partition"):
        roles.validate(declared, matrix)


def test_a_high_cardinality_fold_declaration_is_refused(tmp_path) -> None:
    matrix = _matrix("split", [f"fold_{i}" for i in range(12)])
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": "split",
        },
        tmp_path,
    )
    with pytest.raises(roles.RoleDeclarationError, match="distinct values"):
        roles.validate(declared, matrix)


def test_a_stale_declaration_is_refused(tmp_path) -> None:
    """A manifest naming a column the file does not have describes some older file."""
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    declared = _declared(
        matrix,
        {
            "label": "sim_denial_flag",
            "time_column": "sim_submission_date",
            "split_column": "fold",
        },
        tmp_path,
    )
    with pytest.raises(roles.RoleDeclarationError, match="no such column"):
        roles.validate(declared, matrix)


def test_an_unknown_role_name_is_refused(tmp_path) -> None:
    matrix = _matrix("split", ["train"] * 6 + ["test"] * 6)
    with pytest.raises(roles.RoleDeclarationError, match="roles this guard understands"):
        _declared(matrix, {"column_roles": {"billed_charge_amt": "weight"}}, tmp_path)
