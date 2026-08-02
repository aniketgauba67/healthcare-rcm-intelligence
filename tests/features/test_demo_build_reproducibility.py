from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any

import duckdb
import pandas as pd

from src.demo import build as demo_build
from src.demo import spec


def _stamp() -> dict[str, Any]:
    return {
        "git_commit": "a" * 40,
        "git_branch": "release/phase5-final-bundle",
        "git_tree_dirty": False,
        "built_at_utc": "2026-08-01T22:23:58+00:00",
    }


def test_build_stamp_uses_source_commit_time(monkeypatch) -> None:
    status_args = (
        "status",
        "--porcelain",
        "--",
        ".",
        *(f":(exclude){path}" for path in demo_build.BUILD_OUTPUT_PATHS),
    )
    values = {
        ("rev-parse", "HEAD"): "a" * 40,
        ("show", "-s", "--format=%cI", "HEAD"): "2026-08-01T18:23:58-04:00",
        status_args: "",
        ("rev-parse", "--abbrev-ref", "HEAD"): "release/phase5-final-bundle",
    }
    monkeypatch.setattr(demo_build, "_git", lambda *args: values[args])

    assert demo_build.build_stamp() == _stamp()


def test_build_stamp_detects_untracked_source_but_exempts_outputs(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Phase 5 QA"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "phase5-qa@example.invalid"], cwd=repo, check=True
    )
    (repo / "tracked.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)

    bundle = repo / "dashboard" / "demo_data" / "rcm_demo.duckdb"
    sidecar = bundle.parent / "README.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"build output")
    sidecar.write_text("build output\n")

    monkeypatch.setattr(demo_build, "REPO_ROOT", repo)
    monkeypatch.setattr(
        demo_build,
        "BUILD_OUTPUT_PATHS",
        (
            bundle.relative_to(repo).as_posix(),
            sidecar.relative_to(repo).as_posix(),
        ),
    )

    assert demo_build.build_stamp()["git_tree_dirty"] is False

    (repo / "untracked_source.py").write_text("VALUE = 2\n")

    assert demo_build.build_stamp()["git_tree_dirty"] is True


def test_equivalent_bundle_writes_are_byte_identical(tmp_path) -> None:
    frames = {"vw_executive_rcm_summary": pd.DataFrame({"claim_count": [20_867]})}
    expected = set(frames)
    output = tmp_path / "rcm_demo.duckdb"
    equivalent = tmp_path / "equivalent.duckdb"

    demo_build.write_bundle(frames, output, expected=expected, stamp=_stamp())
    first_hash = hashlib.sha256(output.read_bytes()).digest()
    connection = duckdb.connect(str(output), read_only=True)
    try:
        raw_inventory = connection.execute("select dataset_names from demo_build_info").fetchone()[
            0
        ]
        stamped = set(json.loads(raw_inventory))
    finally:
        connection.close()
    assert stamped == set(frames) | spec.SELF_DESCRIBING_TABLES
    demo_build.write_bundle(frames, equivalent, expected=expected, stamp=_stamp())
    assert demo_build._bundles_logically_equal(output, equivalent)
    demo_build.write_bundle(frames, output, expected=expected, stamp=_stamp())

    assert hashlib.sha256(output.read_bytes()).digest() == first_hash
    assert not output.with_name(f".{output.name}.candidate").exists()


def test_logical_comparison_rejects_changed_rows(tmp_path) -> None:
    expected = {"vw_executive_rcm_summary"}
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    demo_build.write_bundle(
        {"vw_executive_rcm_summary": pd.DataFrame({"claim_count": [20_867]})},
        first,
        expected=expected,
        stamp=_stamp(),
    )
    demo_build.write_bundle(
        {"vw_executive_rcm_summary": pd.DataFrame({"claim_count": [20_868]})},
        second,
        expected=expected,
        stamp=_stamp(),
    )

    assert not demo_build._bundles_logically_equal(first, second)


def test_build_reuses_one_stamp_for_bundle_and_note(monkeypatch, tmp_path) -> None:
    frames = {
        dataset.name: pd.DataFrame({"row_marker": [1]}) for dataset in spec.WAREHOUSE_DATASETS
    }
    captured: list[dict[str, Any]] = []
    output = tmp_path / "rcm_demo.duckdb"
    stamp = _stamp()

    monkeypatch.setattr("src.ingestion.load_postgres.database_url", lambda: "postgresql://test")
    engine = type("Engine", (), {"dispose": lambda self: None})()
    monkeypatch.setattr("sqlalchemy.create_engine", lambda _url: engine)
    monkeypatch.setattr(demo_build, "read_warehouse_datasets", lambda _engine: frames)
    monkeypatch.setattr(demo_build, "build_stamp", lambda: stamp)
    monkeypatch.setattr(
        demo_build,
        "write_bundle",
        lambda _frames, path, expected, stamp: (captured.append(stamp), path.write_bytes(b"x")),
    )
    monkeypatch.setattr(
        demo_build,
        "write_provenance_note",
        lambda path, _manifest, stamp: (captured.append(stamp), path.parent / "README.md")[1],
    )

    demo_build.build(output, skip_models=True)

    assert captured == [stamp, stamp]
