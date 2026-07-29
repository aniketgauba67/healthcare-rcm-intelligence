"""Artifacts must say which tree built them — or say that they cannot.

`models_artifacts/` is gitignored, so its files carry no commit and cannot be
pinned by inspection. A §7 acceptance was once measured against the wrong tree
and a reviewer credited a plot fix to a commit that did not contain it.

The interesting case is not the clean one. The error being fixed was reading
artifacts built from another agent's UNCOMMITTED work and attributing them to a
commit; a stamp that printed that commit anyway would have converted a human
mistake into a machine-attested falsehood, which is worse, because it survives
challenge. So the dirty case gets most of the tests below: `-dirty` in the
describe, `dirty: true` as an explicit boolean rather than an absence, a file
count, and prose that refuses the attribution in words.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from src.models.run_stamp import run_stamp, stamp_lines


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def clean_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "--quiet", "-m", "first")
    return tmp_path


def test_a_clean_tree_names_its_commit(clean_repo: pathlib.Path) -> None:
    stamp = run_stamp(clean_repo)
    assert stamp["dirty"] is False
    assert len(stamp["commit"]) == 40
    assert "warning" not in stamp
    assert stamp["commit"] in stamp_lines(stamp)


def test_a_dirty_tree_refuses_to_name_a_commit_as_its_source(clean_repo: pathlib.Path) -> None:
    """The failure mode this exists for: a bare SHA on a tree that is not that SHA."""
    (clean_repo / "a.txt").write_text("two\n")

    stamp = run_stamp(clean_repo)
    assert stamp["dirty"] is True
    assert stamp["describe"].endswith("-dirty")
    assert stamp["uncommitted_files"] == 1
    assert "UNCOMMITTED CHANGES" in stamp["warning"]

    prose = stamp_lines(stamp)
    assert "uncommitted working tree" in prose
    assert "1 file(s) differing" in prose
    # The full 40-character SHA must not appear as a plain attribution: quoting it
    # is what turned a reviewer's mistake into an attested one.
    assert stamp["commit"] not in prose


def test_an_untracked_file_counts_as_dirty(clean_repo: pathlib.Path) -> None:
    """`git describe --dirty` ignores untracked files. A stamp must not.

    Artifacts built from a new, uncommitted module are exactly as unattributable
    as artifacts built from a modified one, and `describe` alone would call that
    tree clean.
    """
    (clean_repo / "new_module.py").write_text("x = 1\n")
    stamp = run_stamp(clean_repo)
    assert stamp["dirty"] is True
    assert stamp["uncommitted_files"] == 1


def test_no_git_claims_nothing(tmp_path: pathlib.Path) -> None:
    stamp = run_stamp(tmp_path)
    assert stamp["describe"] == "unknown"
    assert stamp["commit"] is None
    assert "cannot be pinned" in stamp["warning"]
    assert "unattributed" in stamp_lines(stamp).lower()


def test_dirty_is_always_an_explicit_value(clean_repo: pathlib.Path, tmp_path) -> None:
    """Never inferred from the absence of a warning — absence-as-signal is the
    same shape as a skipped leakage probe reading like a passing one."""
    for stamp in (run_stamp(clean_repo), run_stamp(tmp_path / "not_a_repo")):
        assert "dirty" in stamp


def test_the_artifact_readme_carries_the_stamp(tmp_path: pathlib.Path) -> None:
    """The README is where a reader who is not parsing JSON will look."""
    from src.models.train import write_provenance_readme

    path = write_provenance_readme(
        tmp_path, model="A", make_target="make train", description="test artifacts"
    )
    text = path.read_text()
    assert "## Which tree built these" in text
    assert "gitignored" in text
    # Whichever state this checkout is in, the README must commit to one of them.
    assert ("clean working tree" in text) or ("uncommitted working tree" in text)
