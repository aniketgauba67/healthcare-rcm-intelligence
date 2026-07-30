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
    committed_baseline,
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


# --- the guard must not disarm itself -------------------------------------
#
# QA-ADDED 2026-07-29 (qa-reviewer-p16), gating bfea020. RED at the time of
# writing; the fix is ml's, in src/features/store.py.
#
# `committed_manifest` collapses four different conditions to None — no git, path
# outside the repo, `git show` failed, and MANIFEST DID NOT PARSE — and
# `_refuse_or_report` treats None as "nothing to protect" and returns quietly.
# Three of those really are "nothing to protect". The fourth is not: a committed
# manifest that does not parse, or a manifest that stopped being tracked while the
# PARQUET is still committed, means there IS a good artifact at HEAD and the guard
# cannot read its baseline. It then writes the degraded matrix over it in silence.
#
# That is this module's own stated principle turned on itself. `manifest_deviations`
# already refuses to pass over a missing `null_rates` block — it reports
# "NULL RATES NOT COMPARED" — for exactly the reason that a check which did not run
# must not read like a check that passed. One level up, the same omission is silent.
#
# Measured, not reasoned: both cases below were reproduced writing a matrix with
# `diagnosis_count` entirely null over a committed parquet, and in both the parquet
# bytes changed with no exception raised.


def test_an_unparseable_committed_manifest_does_not_disarm_the_guard(
    scratch_repo: pathlib.Path, matrix
) -> None:
    """A committed manifest that does not parse is an unreadable baseline, not an absent one.

    The artifact at HEAD is real and good; only the evidence about it is
    unreadable. Writing over it silently is the one outcome this guard exists to
    prevent, and it is reachable by a truncated write or a botched conflict
    resolution — neither exotic.
    """
    parquet = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.parquet"
    manifest = parquet.parent / "model_a_training_matrix.json"
    manifest.write_text("{ not json\n")
    _git(scratch_repo, "add", "-A")
    _git(scratch_repo, "commit", "--quiet", "-m", "corrupt the manifest")

    before = parquet.read_bytes()
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    with pytest.raises(ArtifactRewriteRefused):
        _persist_into(scratch_repo, degraded)
    assert parquet.read_bytes() == before, (
        "the committed parquet was overwritten while the guard could not read its baseline"
    )


def test_an_untracked_manifest_beside_a_tracked_parquet_does_not_disarm_the_guard(
    scratch_repo: pathlib.Path, matrix
) -> None:
    """The guard tests for the MANIFEST at HEAD; the thing it protects is the PARQUET.

    Untrack the manifest and the parquet is still committed — there is still a good
    artifact to protect — but the guard reads None and returns quietly. Whether the
    right answer is to refuse or to say loudly that it could not check is ml's call;
    writing in silence is not one of the options.
    """
    parquet = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.parquet"
    manifest = parquet.parent / "model_a_training_matrix.json"
    _git(scratch_repo, "rm", "--quiet", "--cached", str(manifest.relative_to(scratch_repo)))
    _git(scratch_repo, "commit", "--quiet", "-m", "untrack the manifest")

    before = parquet.read_bytes()
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA
    with pytest.raises(ArtifactRewriteRefused):
        _persist_into(scratch_repo, degraded)
    assert parquet.read_bytes() == before, (
        "the committed parquet was overwritten while the guard had no readable baseline"
    )


def test_git_being_unrunnable_does_not_disarm_the_guard(
    monkeypatch: pytest.MonkeyPatch, scratch_repo: pathlib.Path, matrix
) -> None:
    """RED (qa-reviewer-p17, 2026-07-29). THE THIRD DOOR into the same failure.

    9bcc14e split the baseline lookup into absent / readable / unreadable and
    closed two of the four collapsed conditions. The remaining two still answer
    `absent`: `git could not be run`, and `_repo_root_for` returning None, which
    is the same condition seen one function earlier. The reason given is that in
    that state "nothing can be established, including whether the artifact is
    committed" — but that is precisely `unreadable`, and the module's own rule
    says a check that did not run must never read like a check that passed.

    MEASURED, end to end, in a scratch repo with a committed matrix:

        git unrunnable -> baseline `absent` -> guard returns quietly ->
        a matrix with `diagnosis_count` 100% null written straight over the
        committed parquet, 1,469,982 bytes -> 1,456,629, NO exception.

    Identical damage to the two cases already fixed, through a door that is not
    exotic: Phase 5 ships `docker compose up`, and a slim Python image has no git
    binary. Any `make features` / `make train` in such a container runs with this
    guard silently off.

    The evidence needed is available WITHOUT git: the artifact is sitting at the
    guarded path on disk. Comparing against the manifest BESIDE it would be wrong
    — that is the self-repairing check the shape ruling forbids — so the answer is
    not to compare, it is to refuse: something is there and nothing about it can
    be verified. `--allow-change` remains the way through, as it already is for
    the other unreadable cases, and a first write (no artifact yet, `tmp_path`)
    still sees no file and stays quiet.
    """
    parquet = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.parquet"
    before = parquet.read_bytes()
    degraded = matrix.copy()
    degraded["diagnosis_count"] = pd.NA

    real_run = subprocess.run

    def git_is_missing(command, *args, **kwargs):
        if command and str(command[0]) == "git":
            raise FileNotFoundError(2, "No such file or directory: 'git'")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", git_is_missing)

    with pytest.raises(ArtifactRewriteRefused):
        _persist_into(scratch_repo, degraded)
    assert parquet.read_bytes() == before, (
        "the committed parquet was overwritten because git could not be run. Standing down "
        "when the guard cannot consult its own reference is the [GUARD-DISARM] shape, and "
        "this is the third path into it."
    )


def test_a_first_write_is_still_quiet_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, matrix
) -> None:
    """Control for the test above: refusing must not swallow the legitimate case.

    With no artifact at the target path there is nothing to protect, whether or
    not git can be run, and the write must go through in silence. A fix for
    [GUARD-DISARM]-3 that refuses here would make every fresh build require an
    override, and an override that becomes routine protects nothing.
    """
    real_run = subprocess.run

    def git_is_missing(command, *args, **kwargs):
        if command and str(command[0]) == "git":
            raise FileNotFoundError(2, "No such file or directory: 'git'")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", git_is_missing)

    path = tmp_path / "artifacts" / "features" / "model_a_training_matrix.parquet"
    path.parent.mkdir(parents=True)
    persist_training_matrix(matrix, path=path)
    assert path.exists()


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


# --- the three states, named -----------------------------------------------
#
# ML-ADDED 2026-07-29 (ml-engineer-8), closing [GUARD-DISARM]. qa's two tests
# above fix the BEHAVIOUR — the parquet survives — and these fix the DISTINCTION
# underneath it, because the behaviour is satisfiable by a guard that has stopped
# telling the three cases apart. A `committed_baseline` hardwired to "unreadable"
# passes both of qa's tests and refuses every legitimate write; one hardwired to
# "readable" would pass neither. The states are therefore asserted by name, and
# `test_the_real_committed_baseline_is_readable` is the control that keeps the
# quiet path reachable.


def test_a_corrupt_committed_manifest_is_unreadable_not_absent(
    scratch_repo: pathlib.Path,
) -> None:
    """The state, not just the refusal: "cannot tell" must not be spelled "nothing here"."""
    manifest = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.json"
    manifest.write_text("{ not json\n")
    _git(scratch_repo, "add", "-A")
    _git(scratch_repo, "commit", "--quiet", "-m", "corrupt the manifest")

    baseline = committed_baseline(manifest, repo_root=scratch_repo)
    assert baseline.state == "unreadable"
    assert baseline.manifest is None
    assert "does not parse" in baseline.reason
    # And the convenience view still answers None — which is exactly why callers
    # deciding whether to guard must not use it.
    assert committed_manifest(manifest, repo_root=scratch_repo) is None


def test_an_untracked_manifest_beside_a_tracked_parquet_is_unreadable(
    scratch_repo: pathlib.Path,
) -> None:
    manifest = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.json"
    _git(scratch_repo, "rm", "--quiet", "--cached", str(manifest.relative_to(scratch_repo)))
    _git(scratch_repo, "commit", "--quiet", "-m", "untrack the manifest")

    baseline = committed_baseline(manifest, repo_root=scratch_repo)
    assert baseline.state == "unreadable"
    assert "IS committed at HEAD" in baseline.reason


def test_nothing_committed_at_all_is_absent(scratch_repo: pathlib.Path) -> None:
    """Both files gone from HEAD: genuinely nothing to protect, and the guard stands down."""
    features = scratch_repo / "artifacts" / "features"
    _git(scratch_repo, "rm", "--quiet", "--cached", "-r", str(features.relative_to(scratch_repo)))
    _git(scratch_repo, "commit", "--quiet", "-m", "untrack the artifact")

    baseline = committed_baseline(features / "model_a_training_matrix.json", repo_root=scratch_repo)
    assert baseline.state == "absent"


def test_a_path_outside_any_repository_is_absent(tmp_path: pathlib.Path) -> None:
    baseline = committed_baseline(tmp_path / "model_a_training_matrix.json")
    assert baseline.state == "absent"
    assert baseline.manifest is None


def test_the_real_committed_baseline_is_readable() -> None:
    """The control on the other three: the normal path must still be the quiet one.

    Without this, a guard that answered "unreadable" to everything would satisfy
    every test above while refusing every honest `make features`.
    """
    baseline = committed_baseline()
    if baseline.state == "absent":
        pytest.skip("manifest not tracked at HEAD in this checkout")
    assert baseline.state == "readable", baseline.reason
    assert baseline.manifest is not None


def test_the_override_covers_an_unreadable_baseline_too(
    scratch_repo: pathlib.Path, matrix, capsys
) -> None:
    """A refusal with no way out would strand the operator with a corrupt HEAD.

    The remedy in that state is to commit a good manifest, which needs a write.
    So `--allow-change` must reach this case as well — loudly, naming the reason,
    and never inferred.
    """
    parquet = scratch_repo / "artifacts" / "features" / "model_a_training_matrix.parquet"
    manifest = parquet.parent / "model_a_training_matrix.json"
    manifest.write_text("{ not json\n")
    _git(scratch_repo, "add", "-A")
    _git(scratch_repo, "commit", "--quiet", "-m", "corrupt the manifest")

    _persist_into(scratch_repo, matrix, allow_change=True)

    printed = capsys.readouterr().err
    assert "OVERRIDE" in printed
    assert "NO baseline check" in printed
    assert "does not parse" in printed
    assert json.loads(manifest.read_text())["rows"] == len(matrix)
