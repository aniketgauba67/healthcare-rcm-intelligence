"""Which commit produced these artifacts — stated, or explicitly not claimed.

`models_artifacts/` is gitignored. The files in it carry no commit of their own,
so a reader holding a `metrics.json` and a plot cannot tell which tree built
them, and this project has already paid for that twice: a §7 acceptance was
measured against the wrong tree, and a reviewer credited a plot fix to a commit
that did not contain it. The process fix — regenerate from the pinned tree before
judging the output — works, and it is expensive every single time. A stamp makes
it cheap.

**A bare SHA is a lie on a dirty tree, and that is the whole design problem.**
The actual error being fixed was reading artifacts built from another agent's
UNCOMMITTED work and attributing them to a commit. A stamp that printed that
commit would have turned a reviewer's mistake into a machine-attested falsehood —
strictly worse, because it survives challenge. So:

* clean tree: the commit, plainly.
* dirty tree: `git describe --always --dirty` (which suffixes `-dirty`), the
  count of differing files, and a line saying in words that these artifacts
  correspond to NO commit and must not be cited as evidence about one.
* no git at all: `"unknown"`, and the same refusal to claim a commit.

Refusing to write on a dirty tree would be too strong — that is how people
develop. An unlabelled SHA is too weak. The label is the fix.

The COMMITTED feature manifest deliberately gets none of this. It is
content-addressed on purpose so that any writer emits byte-identical bytes; a
commit stamp would churn it on every commit, and a committed artifact whose diff
reviewers learn to ignore is how a real content change slips through. That file
is committed, so its commit IS its stamp. See `src/features/store.py`.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_UNCOMMITTED_WARNING = (
    "UNCOMMITTED CHANGES — these artifacts were generated from a working tree that "
    "did not match any commit. They are NOT evidence about {commit}, and must not be "
    "cited as though they were. Commit the tree and regenerate before pinning a result."
)


def _git(repo_root: pathlib.Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def run_stamp(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, Any]:
    """The provenance block written into `metrics.json` and the artifact READMEs.

    `dirty` is always present and always a boolean, so a consumer never has to
    infer cleanliness from the absence of a warning — absence-as-signal is how
    the skipped leakage probe read like a passing one.
    """
    describe = _git(repo_root, "describe", "--always", "--dirty")
    if describe is None:
        return {
            "describe": "unknown",
            "commit": None,
            "dirty": None,
            "warning": "NO GIT INFORMATION — these artifacts cannot be pinned to a tree. "
            "Treat them as unattributed.",
        }

    commit = _git(repo_root, "rev-parse", "HEAD") or "unknown"
    status = _git(repo_root, "status", "--porcelain") or ""
    changed = [line for line in status.splitlines() if line.strip()]
    dirty = describe.endswith("-dirty") or bool(changed)

    stamp: dict[str, Any] = {
        "describe": describe,
        "commit": commit,
        "dirty": dirty,
        "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
    }
    if dirty:
        stamp["uncommitted_files"] = len(changed)
        stamp["warning"] = _UNCOMMITTED_WARNING.format(commit=commit[:7])
    return stamp


def stamp_lines(stamp: dict[str, Any]) -> str:
    """The same stamp as Markdown, for a README a person reads rather than parses."""
    if stamp.get("dirty"):
        return (
            f"Generated from **an uncommitted working tree** — `git describe: "
            f"{stamp['describe']}`, {stamp.get('uncommitted_files', 0)} file(s) differing "
            f"from `{str(stamp.get('commit') or 'unknown')[:7]}`.\n\n"
            f"> **{stamp['warning']}**"
        )
    if stamp.get("commit") is None:
        return f"Generated outside a git checkout.\n\n> **{stamp['warning']}**"
    return (
        f"Generated from commit `{stamp['commit']}` (`{stamp['describe']}`) on branch "
        f"`{stamp.get('branch') or 'unknown'}`, with a clean working tree."
    )
