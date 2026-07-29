"""The committed training matrix must not be silently rewritten by a bad run.

A training run against a transiently degraded warehouse persisted a matrix whose
`diagnosis_count` was entirely null, straight over the committed artifact — and
the write landed BEFORE the run failed loudly. The file that exists so the §4.1
leakage probes can run without a warehouse was therefore rewritable by exactly
the condition it guards against. It was caught only because somebody hashed the
file before and after.

Two properties are under test, and the second is the one that is easy to get
wrong.

**It refuses, loudly, and writes nothing.** Not a warning, not a skip. A skip is
what let the original through.

**The baseline is git, never the copy on disk.** The on-disk manifest is written
by the same call that would write the bad matrix, so comparing against it
reproduces the self-repairing-check defect this project has hit three times: the
check repairs the condition it exists to detect, after which "was never bad" and
"was bad and got quietly rewritten" are indistinguishable. `test_the_baseline_is_
the_commit_not_the_working_tree` builds a throwaway repo where the two disagree
and asserts which one wins.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pandas as pd
import pytest

from src.features.store import (
    MANIFEST_PATH,
    MATRIX_PATH,
    ArtifactRewriteRefused,
    committed_manifest,
    manifest_deviations,
    persist_training_matrix,
    read_persisted_matrix,
)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def matrix() -> pd.DataFrame:
    frame = read_persisted_matrix()
    if frame is None:
        pytest.skip(f"no committed matrix at {MATRIX_PATH}")
    return frame.drop(columns=["split"])


@pytest.fixture
def scratch_repo(tmp_path: pathlib.Path, matrix: pd.DataFrame) -> pathlib.Path:
    """A real git repo holding a committed matrix, so the guard has a baseline.

    Built rather than mocked: the guard shells out to `git show HEAD:`, and a
    fake baseline would test the comparison while leaving the part that actually
    reads the commit unexercised.
    """
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    path = tmp_path / "artifacts" / "features" / "model_a_training_matrix.parquet"
    path.parent.mkdir(parents=True)
    persist_training_matrix(matrix, path=path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "--quiet", "-m", "baseline")
    return tmp_path


def _persist_into(repo: pathlib.Path, frame: pd.DataFrame, **kwargs) -> None:
    persist_training_matrix(
        frame, path=repo / "artifacts" / "features" / "model_a_training_matrix.parquet", **kwargs
    )


# --- it refuses, and nothing is written -----------------------------------


def test_the_original_failure_is_refused(scratch_repo: pathlib.Path, matrix) -> None:
    """The exact shape that got through: one column entirely null, everything else right.

    Row count unchanged, column set unchanged. Only the null rate moved, which is
    why neither of the other two checks would have caught it alone.
    """
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA

    with pytest.raises(ArtifactRewriteRefused) as raised:
        _persist_into(scratch_repo, degraded)
    assert "diagnosis_count" in str(raised.value)
    assert "0.0000 -> 1.0000" in str(raised.value)


def test_nothing_is_written_when_the_guard_refuses(scratch_repo: pathlib.Path, matrix) -> None:
    """Refusing after the write would be the original defect with a louder log."""
    parquet = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.parquet"
    manifest = parquet.parent / "model_a_training_matrix.json"
    before = (parquet.read_bytes(), manifest.read_bytes())

    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    with pytest.raises(ArtifactRewriteRefused):
        _persist_into(scratch_repo, degraded)

    assert (parquet.read_bytes(), manifest.read_bytes()) == before


def test_a_dropped_row_is_refused(scratch_repo: pathlib.Path, matrix) -> None:
    with pytest.raises(ArtifactRewriteRefused, match="row count"):
        _persist_into(scratch_repo, matrix.iloc[:-1])


def test_a_dropped_column_is_refused(scratch_repo: pathlib.Path, matrix) -> None:
    with pytest.raises(ArtifactRewriteRefused, match="columns removed"):
        _persist_into(scratch_repo, matrix.drop(columns=["patient_age_years"]))


def test_an_unchanged_matrix_writes_without_complaint(scratch_repo: pathlib.Path, matrix) -> None:
    """The guard must not cost anything on the normal path."""
    _persist_into(scratch_repo, matrix)


# --- the baseline is the commit ------------------------------------------


def test_the_baseline_is_the_commit_not_the_working_tree(
    scratch_repo: pathlib.Path, matrix
) -> None:
    """Corrupt the on-disk manifest to bless a bad matrix. The guard must not care.

    This is the self-repairing-check defect, staged: if the comparison ran
    against the working tree, a previous bad run would have already rewritten the
    baseline to match itself and the second bad run would sail through.
    """
    manifest_path = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.json"
    poisoned = json.loads(manifest_path.read_text())
    poisoned["null_rates"]["diagnosis_count"] = 1.0
    poisoned["rows"] = 1
    manifest_path.write_text(json.dumps(poisoned, indent=2) + "\n")

    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    with pytest.raises(ArtifactRewriteRefused, match="diagnosis_count"):
        _persist_into(scratch_repo, degraded)


def test_committed_manifest_reads_head_not_disk(scratch_repo: pathlib.Path) -> None:
    manifest_path = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.json"
    on_disk = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({"rows": -1}) + "\n")

    from_git = committed_manifest(manifest_path, repo_root=scratch_repo)
    assert from_git is not None
    assert from_git["rows"] == on_disk["rows"]


def test_no_committed_baseline_means_no_guard(tmp_path: pathlib.Path, matrix) -> None:
    """A path git has never seen has no good artifact to protect."""
    path = tmp_path / "model_a_training_matrix.parquet"
    persist_training_matrix(matrix, path=path)
    assert path.exists()
    assert committed_manifest(path.parent / "model_a_training_matrix.json") is None


# --- the override is deliberate and loud ----------------------------------


def test_the_override_writes_and_prints_every_deviation(
    scratch_repo: pathlib.Path, matrix, capsys
) -> None:
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    _persist_into(scratch_repo, degraded, allow_change=True)

    printed = capsys.readouterr().err
    assert "OVERRIDE" in printed
    assert "diagnosis_count" in printed


def test_the_override_is_off_unless_asked_for(monkeypatch, scratch_repo: pathlib.Path, matrix):
    """An env var set to nothing, 0 or false must not disarm the guard."""
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    for value in ("", "0", "false"):
        monkeypatch.setenv("RCM_ALLOW_MATRIX_CHANGE", value)
        with pytest.raises(ArtifactRewriteRefused):
            _persist_into(scratch_repo, degraded)


# --- the committed artifact itself ----------------------------------------


def test_the_committed_manifest_records_null_rates() -> None:
    """Without this block the guard reports NOT COMPARED, which is a real gap."""
    manifest = committed_manifest()
    if manifest is None:
        pytest.skip("manifest not tracked at HEAD in this checkout")
    assert manifest.get("null_rates"), (
        f"{MANIFEST_PATH} at HEAD carries no null_rates block, so the write guard cannot "
        "compare the property the degraded-matrix failure actually moved."
    )


def test_an_uncomparable_baseline_is_reported_not_passed_over() -> None:
    """A check that did not run must never read like a check that passed."""
    candidate = {"rows": 10, "null_rates": {"a": 0.0}}
    legacy = {"rows": 10, "features": ["a"], "passthrough": [], "split_column": "split"}
    problems = manifest_deviations(candidate, legacy)
    assert any("NULL RATES NOT COMPARED" in problem for problem in problems)
