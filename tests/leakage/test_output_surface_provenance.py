"""§3.2 at the LAST boundary: what a user actually reads.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). Do not delete it to go green.

STATUS AT THE TIME OF WRITING: **RED on `src/models/work_queue.py`**, which is
[QUEUE-PREFIX], assigned to ml-engineer-6 and directed by the human. Red is the
correct state until the rename lands; this file is the gate that says when it has.

WHY THIS EXISTS
---------------
The human's Phase 5 instruction was that the provenance/exposure check which
*would* have caught [QUEUE-PREFIX] be re-run and extended so it cannot recur in
Phase 5's outputs. It could not have caught it, because no such check covered this
boundary. The two prefix guards that existed stop earlier:

* `tests/contracts/test_view_sim_prefix.py` — view SQL, i.e. the warehouse boundary;
* `tests/leakage/test_feature_prefix_survival.py` — declared feature sources, i.e.
  `src/features/`.

`src/models/work_queue.py` is downstream of both, and the defect is invisible to a
static scan besides: the `sim_` name that gets stripped lives in a function
signature default (`recoverable_column: str = "sim_denied_amount"`), not at the
assignment. So this gate MEASURES instead of reading — see `tests/leakage/
exposure.py` for the perturbation probe and why it is shaped like the
truncation-invariance tests.

"EXTENDED TO PHASE 5'S OUTPUTS GENERALLY" MEANS SURFACES THAT DO NOT EXIST YET
------------------------------------------------------------------------------
The dashboard and the API are unbuilt. An audit cannot be run against them, so
what is built here is the thing that fails the day they appear without provenance,
in three places:

1. `test_every_user_facing_emitter_is_registered` — a module under `dashboard/` or
   `src/api/` that emits a table and is not in `SURFACES` fails the build. This is
   the shape `test_guard_is_wired_once_a_feature_store_exists` already uses in this
   directory, and it exists because Phase 4 proved that "nobody checked this
   boundary" is a state that persists silently for three phases.
2. `test_no_display_label_strips_the_simulated_marker` — relabelling
   `sim_denied_amount` to "Denied amount" on a dashboard is the same defect as
   renaming it in code, and is the more tempting one because it looks like
   presentation.
3. `test_no_generated_csv_header_carries_an_unmarked_simulated_column` — the
   measured offenders, checked against artifacts a reader can actually open. This
   is team-lead's exposure criterion from ruling A: a name matters once it reaches
   something someone opens.

THE JUDGEMENT IS DECLARED, NOT INFERRED
---------------------------------------
The probe measures dependence; it cannot know that `queue_position` is our
ordering rather than a simulated quantity. Each surface therefore declares its
process-metadata columns with a REASON, applying team-lead's QA RULING C boundary
to outputs: §3.2 governs simulated VALUES, and a rank, a tier, a recommendation or
a query parameter is metadata about our process, not a statement about the
simulated world. `test_no_exemption_is_speculative` requires every exemption to be
a column the probe would otherwise have reported, so the list cannot grow
defensively.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.leakage import exposure

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG = REPO_ROOT / "config" / "model.yaml"
ARTIFACT_ROOTS = (REPO_ROOT / "models_artifacts", REPO_ROOT / "artifacts")
SWEPT_PACKAGES = (REPO_ROOT / "dashboard", REPO_ROOT / "src" / "api")

# Calls that put a table in front of a person. `st.metric` is deliberately absent:
# it renders one number, and the banner requirement in CLAUDE.md §6 is what covers
# a page's scalar figures.
_EMITTER_CALLS = (
    "to_csv",
    "dataframe",
    "table",
    "data_editor",
    "to_dict",
)
_EMITTER_KEYWORDS = ("response_model",)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def _denial_frame(n: int = 240) -> pd.DataFrame:
    """A frame shaped like the denials the work queue is built from."""
    rng = np.random.default_rng(20260728)
    categories = list(
        yaml.safe_load(MODEL_CONFIG.read_text())["appeal_economics"]["mandatory_review_categories"]
    ) + ["TECHNICAL", "CLINICAL"]
    return pd.DataFrame(
        {
            "claim_sk": np.arange(1, n + 1),
            "sim_denied_amount": rng.uniform(80.0, 9000.0, n).round(2),
            "sim_denial_review_date": pd.to_datetime("2023-01-01")
            + pd.to_timedelta(rng.integers(0, 400, n), unit="D"),
            "sim_denial_category": rng.choice(categories, n),
        }
    )


# Statements about OUR process rather than about the simulated world, which is
# team-lead's QA RULING C boundary applied to outputs. Each carries its reason,
# and `test_no_exemption_is_speculative` refuses any that the probe would not
# otherwise have reported.
_QUEUE_PROCESS_METADATA = {
    "tier": "the triage policy OUR queue applies, not a measured quantity",
    "tier_rank": "the ordinal of that policy tier",
    "queue_position": "our ordering of the worklist",
    "recommended_action": "our recommendation to the analyst",
}


def _work_queue_surface(as_of: pd.Timestamp | None) -> exposure.Surface:
    """Both modes the queue actually ships in.

    `model_c.py` writes `work_queue_live_snapshot.csv` (an as-of date) and
    `work_queue_backtest.csv` (at arrival, `as_of=None`), and they are different
    computations, not a default and an override — the module docstring is explicit
    about that. Registering only one would leave a shipped CSV unprobed:
    `days_to_deadline` is a constant in the backtest and a measured quantity in the
    snapshot, so each mode exposes something the other does not.
    """
    from src.models.work_queue import AppealEconomics, build_work_queue

    frame = _denial_frame()
    economics = AppealEconomics.from_config(
        yaml.safe_load(MODEL_CONFIG.read_text()), recovery_ratio=0.81
    )
    probability = np.random.default_rng(7).uniform(0.05, 0.95, len(frame))

    def build(f: pd.DataFrame) -> pd.DataFrame:
        return build_work_queue(f, probability[: len(f)], economics, as_of=as_of)

    def with_perturbed_probability(f: pd.DataFrame) -> pd.DataFrame:
        return build_work_queue(f, 1.0 - probability[: len(f)], economics, as_of=as_of)

    metadata = dict(_QUEUE_PROCESS_METADATA)
    if as_of is None:
        # At arrival the as-of stamp is the latest denial date in the frame, so it
        # does move; it is still the queue's build parameter rather than a claim
        # attribute. Given an as-of date it is a constant and needs no exemption.
        metadata["as_of"] = (
            "the as-of moment the queue was built for — a parameter of the query, "
            "not an attribute of any claim"
        )

    return exposure.Surface(
        name=f"src/models/work_queue.py::build_work_queue({'at arrival' if as_of is None else 'as-of date'})",
        build=build,
        frame=frame,
        process_metadata=metadata,
        extra_simulated_inputs={
            # The overturn probability is a model score of a SIMULATED outcome and
            # is as simulated as anything in the frame, but it arrives as an
            # argument rather than a column, so perturbing the frame would never
            # reach it.
            "probability (Model C score of a simulated appeal outcome)": with_perturbed_probability,
        },
    )


def _surfaces() -> list[exposure.Surface]:
    """Every registered user-facing tabular surface.

    Phase 5 additions (dashboard pages, API responses) belong here, and
    `test_every_user_facing_emitter_is_registered` fails until they are.
    """
    return [
        _work_queue_surface(pd.Timestamp("2024-06-01")),
        _work_queue_surface(None),
    ]


SURFACE_MODULES = frozenset({"src/models/work_queue.py"})


@pytest.fixture(scope="module")
def measured() -> list[tuple[exposure.Surface, dict[str, set[str]]]]:
    return [(surface, exposure.simulated_dependence(surface)) for surface in _surfaces()]


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------


def test_every_emitted_simulated_column_carries_the_marker(measured) -> None:
    """[QUEUE-PREFIX]. RED until the work-queue columns are renamed."""
    failures: list[str] = []
    for surface, dependence in measured:
        offenders = exposure.unmarked_simulated_columns(surface, dependence)
        for column, inputs in sorted(offenders.items()):
            failures.append(f"{surface.name}: `{column}` <- {', '.join(sorted(inputs))}")
    assert not failures, (
        f"{len(failures)} emitted column(s) change when a SIMULATED input changes and carry no "
        "`sim_` marker anywhere in the name, so CLAUDE.md §3.2's provenance is lost at the "
        "boundary a user reads:\n  "
        + "\n  ".join(failures)
        + "\n\nThese names are column headers on the Phase 5 work-queue page and in "
        "models_artifacts/model_c/work_queue_*.csv, where a reader sees the name and nothing "
        "else. The inconsistency is inside one file — `sim_denial_category` keeps its marker "
        "while the dollar amount beside it loses one — so this is a defect against the code's "
        "own intent, not a style preference. Rename to carry the marker (e.g. "
        "`sim_recoverable_amt`) and update the model card, the artifact READMEs and any "
        "reader of these columns in the same commit."
    )


def test_the_probe_reports_nothing_on_a_clean_surface() -> None:
    """Negative control. A probe that cannot stay quiet gets switched off."""
    frame = _denial_frame(40)
    clean = exposure.Surface(
        name="control/clean",
        build=lambda f: pd.DataFrame(
            {
                "claim_sk": f["claim_sk"].to_numpy(),
                "sim_recoverable_amt": f["sim_denied_amount"].to_numpy(),
                "sim_denial_category": f["sim_denial_category"].to_numpy(),
            }
        ),
        frame=frame,
    )
    assert not exposure.unmarked_simulated_columns(clean)


def test_the_probe_catches_a_marker_stripped_by_a_rename() -> None:
    """Positive control, in the exact shape of the real defect.

    The `sim_` name is in a signature default, which is what defeats a static
    scan; if this ever passes, the instrument has stopped measuring.
    """
    frame = _denial_frame(40)

    def build(f: pd.DataFrame, amount_column: str = "sim_denied_amount") -> pd.DataFrame:
        return pd.DataFrame(
            {"claim_sk": f["claim_sk"].to_numpy(), "recoverable_amt": f[amount_column].to_numpy()}
        )

    poisoned = exposure.Surface(name="control/renamed", build=build, frame=frame)
    offenders = exposure.unmarked_simulated_columns(poisoned)
    assert set(offenders) == {"recoverable_amt"}, offenders


def test_the_probe_sees_through_a_transformation() -> None:
    """Logged, binned, divided — still the simulated quantity, still must say so."""
    frame = _denial_frame(40)

    def build(f: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "claim_sk": f["claim_sk"].to_numpy(),
                "value_band": pd.qcut(f["sim_denied_amount"], 4, labels=False),
                "log_amount": np.log1p(f["sim_denied_amount"].abs()).to_numpy(),
            }
        )

    offenders = exposure.unmarked_simulated_columns(
        exposure.Surface(name="control/derived", build=build, frame=frame)
    )
    assert set(offenders) == {"value_band", "log_amount"}, offenders


def test_no_exemption_is_speculative(measured) -> None:
    """Every declared process-metadata column must be one the probe would report.

    An exemption list that can hold columns the probe never touches is a place to
    pre-emptively excuse things, and nothing would ever show that it had happened.
    """
    stale: list[str] = []
    for surface, dependence in measured:
        for column, reason in surface.process_metadata.items():
            assert reason.strip(), f"{surface.name}: exemption for `{column}` states no reason"
            if not dependence.get(column):
                stale.append(f"{surface.name}: `{column}` ({reason})")
    assert not stale, (
        "these columns are declared process metadata but do not move when a simulated input "
        "moves, so the exemption is doing nothing and should be deleted:\n  " + "\n  ".join(stale)
    )


def test_the_key_column_is_not_simulated_derived(measured) -> None:
    """A sanity check on the probe itself: the join key must not move."""
    for surface, dependence in measured:
        assert not dependence.get("claim_sk"), (
            f"{surface.name}: claim_sk moves with a simulated input, which means either the "
            "surface is rewriting its key or the probe is measuring row ORDER rather than "
            "content"
        )


# --------------------------------------------------------------------------
# Phase 5 surfaces that do not exist yet
# --------------------------------------------------------------------------


def _emits_a_table(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _EMITTER_CALLS:
                return True
            if any(kw.arg in _EMITTER_KEYWORDS for kw in node.keywords):
                return True
    return False


def _swept_modules() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for package in SWEPT_PACKAGES:
        if not package.is_dir():
            continue
        found += [p for p in sorted(package.rglob("*.py")) if p.name != "__init__.py"]
    return found


def test_every_user_facing_emitter_is_registered() -> None:
    """The day a dashboard page or an API response ships, it gets probed.

    This is deliberately a build failure rather than a warning. The Phase 4
    finding was not that someone wrote a bad column name — it was that no check
    covered the boundary at all, and that state survived three phases because
    nothing was red.
    """
    unregistered: list[str] = []
    for path in _swept_modules():
        relative = str(path.relative_to(REPO_ROOT))
        if relative in SURFACE_MODULES:
            continue
        if _emits_a_table(ast.parse(path.read_text())):
            unregistered.append(relative)

    assert not unregistered, (
        "these modules put a table in front of a user and are not registered as surfaces in "
        "tests/leakage/test_output_surface_provenance.py, so nothing checks that their columns "
        "declare simulated provenance (CLAUDE.md §3.2, §3.5):\n  "
        + "\n  ".join(unregistered)
        + "\n\n"
        "To register: expose the frame-building step as a function taking the input frame and "
        "returning the emitted table (separate from the render call), then add an "
        "`exposure.Surface` for it in `_surfaces()`. The probe perturbs each simulated input "
        "and reports emitted columns that move without carrying a `sim_` marker. If a column "
        "is genuinely process metadata — a rank, a tier, our own recommendation — declare it "
        "in `process_metadata` WITH the reason; do not widen the probe."
    )


def test_no_display_label_strips_the_simulated_marker() -> None:
    """Relabelling for display is the same defect wearing presentation clothes.

    `{"sim_denied_amount": "Denied amount"}` in a rename map or a column-config
    puts an unmarked simulated dollar figure in front of a reader exactly as a
    rename in code does. A label that says so — "Denied amount (simulated)" — is
    fine, and is the fix.
    """
    offenders: list[str] = []
    roots = [*SWEPT_PACKAGES, REPO_ROOT / "src" / "models"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values, strict=False):
                    if not isinstance(key, ast.Constant) or not isinstance(value, ast.Constant):
                        continue
                    if not isinstance(key.value, str) or not isinstance(value.value, str):
                        continue
                    if "sim_" in key.value and "sim" not in value.value.lower():
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{key.lineno} "
                            f"{key.value!r} -> {value.value!r}"
                        )
    assert not offenders, (
        "a simulated column is mapped to a display label that does not say it is simulated:\n  "
        + "\n  ".join(offenders)
        + "\nSay so in the label (e.g. 'Denied amount (simulated)') rather than dropping the "
        "marker on the way to the screen."
    )


def test_no_generated_csv_header_carries_an_unmarked_simulated_column(measured) -> None:
    """Exposure, in team-lead's sense: the name has reached a file a reader opens.

    Skips when nothing has been generated — `models_artifacts/` is gitignored, so
    on a clean clone there is nothing to inspect and the measurement above is the
    live gate.
    """
    offenders = {
        column
        for surface, dependence in measured
        for column in exposure.unmarked_simulated_columns(surface, dependence)
    }
    if not offenders:
        return

    csvs = [
        path for root in ARTIFACT_ROOTS if root.is_dir() for path in sorted(root.rglob("*.csv"))
    ]
    if not csvs:
        # Said out loud rather than passing quietly: a green tick here would
        # otherwise mean "nothing was generated", which is not the same claim.
        pytest.skip(
            "no generated CSV artifacts on this tree (models_artifacts/ is gitignored); "
            "run `make train-appeal` to check exposure in the shipped files"
        )

    exposed: list[str] = []
    for path in csvs:
        header = pd.read_csv(path, nrows=0).columns
        hit = sorted(offenders.intersection(header))
        if hit:
            exposed.append(f"{path.relative_to(REPO_ROOT)}: {hit}")

    assert not exposed, (
        "generated artifacts ship column headers that are simulated quantities without a "
        "`sim_` marker. A reader opening these files sees the header and nothing else:\n  "
        + "\n  ".join(exposed)
        + "\nRegenerate after the rename (`make train-appeal`)."
    )
