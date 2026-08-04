"""Every column in the shipped demo bundle carries an honest provenance marker.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

The bundle is the most exposed artifact in this repository: it ships to a public
hosted demo, and a reader can open it with no database, no environment and no
code. CLAUDE.md §1 makes "no simulated value presented as real" the property the
project rests on, and §3.2 requires the `sim_` marker on simulated columns.

The specification lives in `bundle_column_provenance.yaml` beside this file, not
in this module. That is deliberate: the classification is the reviewable artifact
and a human signed off on it, while this module only enforces it. A guard whose
specification is buried in its own assertions cannot be reviewed without reading
the test.

WHY DECLARATION-DRIVEN RATHER THAN A PATTERN. The same defect has been found four
times, one layer further out each time -- work-queue CSVs, view layer, API wire,
and this bundle -- and each was fixed as an individual rename. A name pattern
cannot catch the fifth, because the failure is always a column nobody thought
about. Requiring every bare column to be classified means a NEW column fails the
build until a human decides what it is.

WHAT THIS GUARD CANNOT DO, stated so green is not mistaken for safe: it checks
NAMES. It cannot express MEMBERSHIP. `vw_work_queue_priority` selects
denied-or-open-AR claims in its WHERE clause, so that row set already encodes the
outcome even when every column name is honest. That is disclosed on the surface.
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "dashboard" / "demo_data" / "rcm_demo.duckdb"
DECLARATION = pathlib.Path(__file__).with_name("bundle_column_provenance.yaml")

MARKER = "sim_"
REQUIRES_MARKER = {"SIMULATED"}
FORBIDS_MARKER = {"SOURCE", "DERIVED", "REFERENCE", "PROCESS"}
VALID_CLASSES = REQUIRES_MARKER | FORBIDS_MARKER
MIN_REASON_WORDS = 4


def _declaration() -> dict:
    return yaml.safe_load(DECLARATION.read_text())


def _bundle_columns() -> dict[str, list[str]]:
    """Table -> column names, read from the committed artifact itself."""
    if not BUNDLE.is_file():
        pytest.fail(
            f"the committed demo bundle is missing at {BUNDLE.relative_to(REPO_ROOT)}. "
            "This guard covers the artifact that ships to the public demo, so a missing "
            "bundle is a finding, not a reason to skip."
        )
    con = duckdb.connect(str(BUNDLE), read_only=True)
    try:
        tables = [r[0] for r in con.execute("show tables").fetchall()]
        return {t: [r[0] for r in con.execute(f'describe "{t}"').fetchall()] for t in tables}
    finally:
        con.close()


# --------------------------------------------------------------------------
# The declaration itself must be well formed before it can be trusted.
# --------------------------------------------------------------------------


def test_every_declared_class_is_valid_and_carries_a_real_reason() -> None:
    offenders: list[str] = []
    for table, columns in _declaration()["tables"].items():
        for column, entry in columns.items():
            where = f"{table}.{column}"
            if entry.get("class") not in VALID_CLASSES:
                offenders.append(
                    f"{where}: class {entry.get('class')!r} is not one of {sorted(VALID_CLASSES)}"
                )
            if len(str(entry.get("why", "")).split()) < MIN_REASON_WORDS:
                offenders.append(f"{where}: reason is too thin to review -- {entry.get('why')!r}")
    assert not offenders, "the classification is not reviewable:\n  " + "\n  ".join(offenders)


# --------------------------------------------------------------------------
# The three properties that matter.
# --------------------------------------------------------------------------


def test_every_bare_bundle_column_is_classified() -> None:
    """A new unmarked column fails the build until a human says what it is."""
    declared = _declaration()["tables"]
    missing: list[str] = []
    for table, columns in _bundle_columns().items():
        for column in columns:
            if column.startswith(MARKER):
                continue  # self-declaring: the marker IS the classification
            if column not in declared.get(table, {}):
                missing.append(f"{table}.{column}")
    assert not missing, (
        "these bundle columns carry no `sim_` marker and no classification, so nobody has "
        "decided whether they are simulated:\n  "
        + "\n  ".join(sorted(missing))
        + f"\nClassify each in {DECLARATION.name} before shipping the bundle."
    )


def test_every_simulated_column_carries_the_marker() -> None:
    """The finding this file exists for, in its most direct form."""
    unmarked: list[str] = []
    bundle = _bundle_columns()
    for table, columns in _declaration()["tables"].items():
        for column, entry in columns.items():
            if entry["class"] not in REQUIRES_MARKER:
                continue
            if column in bundle.get(table, []) and not column.startswith(MARKER):
                unmarked.append(f"{table}.{column} -- {entry['why']}")
    assert not unmarked, (
        "these columns are classified SIMULATED but ship WITHOUT the `sim_` marker, so a "
        "reader opening the public bundle sees them as real:\n  " + "\n  ".join(sorted(unmarked))
    )


def test_nothing_unsimulated_is_falsely_marked() -> None:
    """Over-marking is a defect too: if `sim_` is everywhere it means nothing."""
    bundle = _bundle_columns()
    falsely: list[str] = []
    for table, columns in _declaration()["tables"].items():
        for column, entry in columns.items():
            if entry["class"] not in FORBIDS_MARKER:
                continue
            if f"{MARKER}{column}" in bundle.get(table, []):
                falsely.append(
                    f"{table}.{MARKER}{column} is classified {entry['class']}: {entry['why']}"
                )
    assert not falsely, (
        "these columns carry the simulated marker but are not simulated:\n  "
        + "\n  ".join(sorted(falsely))
    )


# --------------------------------------------------------------------------
# Controls. A guard never shown to fail is not evidence.
# --------------------------------------------------------------------------


def test_the_classification_check_rejects_an_unclassified_column() -> None:
    declared = {"vw_ar_aging": {"aging_bucket": {"class": "SIMULATED", "why": "a b c d"}}}
    bundle = {"vw_ar_aging": ["aging_bucket", "smuggled_in_later"]}
    missing = [
        f"{t}.{c}"
        for t, cols in bundle.items()
        for c in cols
        if not c.startswith(MARKER) and c not in declared.get(t, {})
    ]
    assert missing == ["vw_ar_aging.smuggled_in_later"]


def test_the_marker_check_rejects_an_unmarked_simulated_column() -> None:
    declared = {
        "vw_x": {"denial_rate": {"class": "SIMULATED", "why": "rate over the simulated flag"}}
    }
    bundle = {"vw_x": ["denial_rate"]}
    unmarked = [
        f"{t}.{c}"
        for t, cols in declared.items()
        for c, e in cols.items()
        if e["class"] in REQUIRES_MARKER and c in bundle.get(t, []) and not c.startswith(MARKER)
    ]
    assert unmarked == ["vw_x.denial_rate"]


def test_the_over_marking_check_rejects_a_falsely_marked_source_column() -> None:
    declared = {"vw_x": {"billed_charge_amt": {"class": "SOURCE", "why": "billed charge from CMS"}}}
    bundle = {"vw_x": ["sim_billed_charge_amt"]}
    falsely = [
        f"{t}.{MARKER}{c}"
        for t, cols in declared.items()
        for c, e in cols.items()
        if e["class"] in FORBIDS_MARKER and f"{MARKER}{c}" in bundle.get(t, [])
    ]
    assert falsely == ["vw_x.sim_billed_charge_amt"]


def test_the_reason_check_rejects_a_thin_reason() -> None:
    assert len("simulated".split()) < MIN_REASON_WORDS
    assert len("rate over the simulated denial flag".split()) >= MIN_REASON_WORDS
