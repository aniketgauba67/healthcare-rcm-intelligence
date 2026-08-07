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
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from dashboard.provenance import DASHBOARD_EMITTERS, DashboardEmitter
from tests.leakage import exposure

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG = REPO_ROOT / "config" / "model.yaml"
ARTIFACT_ROOTS = (REPO_ROOT / "models_artifacts", REPO_ROOT / "artifacts")
SWEPT_PACKAGES = (REPO_ROOT / "dashboard", REPO_ROOT / "src" / "api")

# Calls that put a table in front of a person. `st.metric` is deliberately absent:
# it renders one number, and the banner requirement in docs/project_rules.md §6 is what covers
# a page's scalar figures.
_EMITTER_CALLS = (
    "to_csv",
    "dataframe",
    "table",
    "data_editor",
    "to_dict",
    # A pydantic response object serialised by hand. `response_model=` below is the
    # declarative route; this is the same table reaching the same reader without it.
    "model_dump",
)
_EMITTER_KEYWORDS = ("response_model",)

# `st.write(df)` and `st.json(payload)` render a table as surely as `st.dataframe`
# does, but `st.write("some prose")` renders prose. Detected by ARGUMENT SHAPE: a
# lone string literal is text, anything else is data. Registering a text-only page
# would be a cost with no finding attached, and an exemption list that fills up
# with such pages is how a real one gets waved through.
_DATA_IF_NOT_LITERAL = ("write", "json")

# Every FastAPI route is a user-facing emitter by construction: a decorated
# handler exists to serve its return value to a caller. This is the shape a
# name-based scan misses entirely — a handler that returns
# `[{"recoverable_amt": ...}]` calls nothing on the emitter list, declares no
# `response_model`, and is exactly the Phase 5 API surface the human's
# instruction names. Measured with the probe below and found uncaught, which is
# why the rule is decorator-based rather than call-based.
_ROUTE_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}
)


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


def _dashboard_dataframe_surface() -> exposure.Surface:
    """The shared Streamlit table boundary used by every registered page.

    Pages route each table through ``dashboard.components.dataframe`` with their
    declared emitter. The component owns the last dataframe copy before Streamlit
    renders it, so this probe catches a marker stripped at that shared boundary.
    """
    from dashboard.components import prepare_dataframe
    from dashboard.provenance import emitter_for

    emitter = emitter_for("dashboard/components.py")
    return exposure.Surface(
        name="dashboard/components.py::prepare_dataframe",
        build=lambda frame: prepare_dataframe(frame, emitter=emitter),
        frame=_denial_frame(),
    )


def _surfaces() -> list[exposure.Surface]:
    """Every registered user-facing tabular surface.

    Phase 5 additions (dashboard pages, API responses) belong here, and
    `test_every_user_facing_emitter_is_registered` fails until they are.
    """
    return [
        _work_queue_surface(pd.Timestamp("2024-06-01")),
        _work_queue_surface(None),
        _dashboard_dataframe_surface(),
    ]


SURFACE_MODULES = frozenset({"dashboard/components.py", "src/models/work_queue.py"})

#: Modules that emit a table but COMPUTE none of its columns — every column
#: arrives already built by a curated view and is copied to the wire. The
#: perturbation probe cannot measure these: nothing moves when a simulated input
#: to the SURFACE moves, because the view already ran, so a probe registered here
#: would report a clean surface whatever the columns are made of. MEASURED
#: 2026-07-29 against `src/api/main.py::work_queue` — zero columns reported,
#: including `sim_dollars_at_stake`, the one the API re-marks BECAUSE it is
#: simulated money. Registering such a module in `SURFACE_MODULES` would turn this
#: gate green and prove nothing, so it is declared here instead, WITH the gate that
#: does cover it. Each entry names a real, running test.
PASSTHROUGH_MODULES: dict[str, str] = {
    "src/api/main.py": (
        "pure pass-through of curated view columns; provenance checked by declaration in "
        "tests/leakage/test_wire_provenance.py::"
        "test_no_simulated_derived_column_reaches_the_wire_unaccounted"
    ),
}


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
        "`sim_` marker anywhere in the name, so docs/project_rules.md §3.2's provenance is lost at the "
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


def _is_text_literal(node: ast.expr) -> bool:
    """A bare string constant, i.e. prose rather than data."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_route(node: ast.AST) -> bool:
    """A FastAPI route handler: `@router.get(...)` / `@app.post(...)` and friends."""
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    for decorator in node.decorator_list:
        call = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(call, ast.Attribute) and call.attr in _ROUTE_DECORATORS:
            return True
    return False


def _imported_symbols(tree: ast.AST) -> dict[str, str]:
    """Resolve the imports needed to recognise a dashboard render boundary.

    This deliberately follows only module-level import spelling.  The gate is a
    source-boundary check, not a general Python interpreter, but it must not lose
    a user-facing table merely because a page calls the shared renderer `grid`.
    """
    symbols: dict[str, str] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", maxsplit=1)[0]
                symbols[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                symbols[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return symbols


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _resolved_call_target(call: ast.Call, symbols: dict[str, str]) -> str | None:
    """Return a call target after resolving its first imported name."""
    dotted = _dotted_name(call.func)
    if not dotted:
        return None
    root, *tail = dotted.split(".")
    resolved_root = symbols.get(root, root)
    return ".".join((resolved_root, *tail))


def _user_facing_emitter_calls(tree: ast.AST) -> list[ast.Call]:
    """Every AST call that the generic output-surface gate classifies as data."""
    symbols = _imported_symbols(tree)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolved_call_target(node, symbols)
        name = target.rsplit(".", maxsplit=1)[-1] if target else ""
        if name in _EMITTER_CALLS:
            calls.append(node)
        elif name in _DATA_IF_NOT_LITERAL and node.args and not _is_text_literal(node.args[0]):
            calls.append(node)
        elif any(keyword.arg in _EMITTER_KEYWORDS for keyword in node.keywords):
            calls.append(node)
    return calls


def _emits_a_table(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if _is_route(node):
            return True
    return bool(_user_facing_emitter_calls(tree))


def _swept_modules() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for package in SWEPT_PACKAGES:
        if not package.is_dir():
            continue
        found += [p for p in sorted(package.rglob("*.py")) if p.name != "__init__.py"]
    return found


def _page_binds_declared_emitter(tree: ast.AST, module: str) -> bool:
    """Whether a page names the registry entry that governs all of its output."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if not isinstance(node.targets[0], ast.Name) or node.targets[0].id != "PAGE_EMITTER":
            continue
        if not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        if not isinstance(function, ast.Name) or function.id != "emitter_for":
            continue
        if len(node.value.args) != 1:
            continue
        argument = node.value.args[0]
        if isinstance(argument, ast.Constant) and argument.value == module:
            return True
    return False


def _uses_page_emitter(call: ast.Call) -> bool:
    return any(
        keyword.arg == "emitter"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "PAGE_EMITTER"
        for keyword in call.keywords
    )


def _is_registered_dataframe_boundary(call: ast.Call, symbols: dict[str, str]) -> bool:
    """Whether a call reaches the one approved dataframe boundary for a page."""
    return _resolved_call_target(
        call, symbols
    ) == "dashboard.components.dataframe" and _uses_page_emitter(call)


def _dashboard_page_emitter_errors(
    module: str, tree: ast.AST, registry: dict[str, DashboardEmitter] = DASHBOARD_EMITTERS
) -> list[str]:
    """Return every unregistered output call in a registered dashboard page.

    A page declaration identifies the required PAGE_EMITTER; it cannot waive the
    generic AST scan for another output call in the same file.
    """
    errors: list[str] = []
    emitter = registry.get(module)
    if (
        emitter is None
        or emitter.module != module
        or not _page_binds_declared_emitter(tree, module)
    ):
        return ["missing or mismatched PAGE_EMITTER registration"]

    headers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (node.func.id if isinstance(node.func, ast.Name) else "") == "render_page_header"
    ]
    if not headers:
        errors.append("no render_page_header call")
    errors.extend(
        f"render_page_header at line {call.lineno} is not bound to PAGE_EMITTER"
        for call in headers
        if not _uses_page_emitter(call)
    )

    symbols = _imported_symbols(tree)
    calls = _user_facing_emitter_calls(tree)
    if not calls:
        errors.append("no user-facing output call")
    errors.extend(
        f"unregistered user-facing emitter at line {call.lineno}: "
        f"{_resolved_call_target(call, symbols) or '<dynamic call>'}"
        for call in calls
        if not _is_registered_dataframe_boundary(call, symbols)
    )
    return errors


def _registered_dashboard_page(
    module: str, tree: ast.AST, registry: dict[str, DashboardEmitter] = DASHBOARD_EMITTERS
) -> bool:
    """Whether every user-facing emitter call is bound to this page's declaration."""
    return not _dashboard_page_emitter_errors(module, tree, registry)


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
        tree = ast.parse(path.read_text())
        if relative in SURFACE_MODULES or relative in PASSTHROUGH_MODULES:
            continue
        if relative in DASHBOARD_EMITTERS:
            errors = _dashboard_page_emitter_errors(relative, tree)
            if errors:
                unregistered.append(f"{relative}: {'; '.join(errors)}")
            continue
        if _emits_a_table(tree):
            unregistered.append(relative)

    assert not unregistered, (
        "these modules put a table in front of a user and are not registered as surfaces in "
        "tests/leakage/test_output_surface_provenance.py, so nothing checks that their columns "
        "declare simulated provenance (docs/project_rules.md §3.2, §3.5):\n  "
        + "\n  ".join(unregistered)
        + "\n\n"
        "To register: expose the frame-building step as a function taking the input frame and "
        "returning the emitted table (separate from the render call), then add an "
        "`exposure.Surface` for it in `_surfaces()`. The probe perturbs each simulated input "
        "and reports emitted columns that move without carrying a `sim_` marker. If a column "
        "is genuinely process metadata — a rank, a tier, our own recommendation — declare it "
        "in `process_metadata` WITH the reason; do not widen the probe.\n\n"
        "IF THE MODULE COMPUTES NOTHING — it copies columns a curated view already built — "
        "the probe cannot see it and registering it here would be a green tick over an "
        "unmeasured surface. Declare it in `PASSTHROUGH_MODULES` instead, naming the "
        "declaration-based gate in tests/leakage/test_wire_provenance.py that covers it."
    )


def test_dashboard_emitters_have_complete_declarations_and_routed_tables() -> None:
    """A page-level registration records output, provenance, and the disclosure a reader sees."""
    expected = {
        "dashboard/components.py",
        "dashboard/pages/ar_recovery.py",
        "dashboard/pages/denial_prevention.py",
        "dashboard/pages/executive_overview.py",
        "dashboard/pages/model_data_quality.py",
        "dashboard/pages/work_queue.py",
    }
    assert set(DASHBOARD_EMITTERS) == expected

    for module, emitter in DASHBOARD_EMITTERS.items():
        assert emitter.module == module
        assert emitter.surface.strip() and emitter.provenance.strip() and emitter.outputs
        assert emitter.contains_simulated, f"{module}: simulated output must be declared"
        assert emitter.disclosure.strip(), f"{module}: required disclosure is missing"
        if module == "dashboard/components.py":
            continue
        tree = ast.parse((REPO_ROOT / module).read_text())
        assert _registered_dashboard_page(module, tree), (
            f"{module}: page output is not bound to its declared dashboard emitter"
        )


def _control_emitter(module: str) -> DashboardEmitter:
    return DashboardEmitter(
        module=module,
        surface="Control page",
        provenance="SIMULATED",
        contains_simulated=True,
        disclosure="Synthetic-data banner required.",
        outputs=("dataframe",),
    )


@pytest.mark.parametrize(
    ("name", "source", "registration", "passes"),
    [
        (
            "unregistered raw streamlit dataframe",
            "import streamlit as st\nst.dataframe(rows)\n",
            None,
            False,
        ),
        (
            "unregistered module-qualified streamlit dataframe",
            "import streamlit\nstreamlit.dataframe(rows)\n",
            None,
            False,
        ),
        (
            "correct registered shared dataframe",
            "from dashboard.components import dataframe, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "dataframe(rows, emitter=PAGE_EMITTER)\n",
            "dashboard/pages/control.py",
            True,
        ),
        (
            "registered module-qualified shared dataframe",
            "import dashboard.components\n"
            "from dashboard.components import render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "dashboard.components.dataframe(rows, emitter=PAGE_EMITTER)\n",
            "dashboard/pages/control.py",
            True,
        ),
        (
            "registered dataframe plus raw streamlit dataframe",
            "from dashboard.components import dataframe, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "dataframe(rows, emitter=PAGE_EMITTER)\n"
            "import streamlit as st\nst.dataframe(rows)\n",
            "dashboard/pages/control.py",
            False,
        ),
        (
            "registered dataframe plus unbound aliased shared dataframe",
            "from dashboard.components import dataframe as grid, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "grid(rows)\n",
            "dashboard/pages/control.py",
            False,
        ),
        (
            "registered aliased shared dataframe",
            "from dashboard.components import dataframe as grid, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "grid(rows, emitter=PAGE_EMITTER)\n",
            "dashboard/pages/control.py",
            True,
        ),
        (
            "missing page registration",
            "from dashboard.components import dataframe, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "dataframe(rows, emitter=PAGE_EMITTER)\n",
            None,
            False,
        ),
        (
            "wrong page registration",
            "from dashboard.components import dataframe, render_page_header\n"
            "from dashboard.provenance import emitter_for\n"
            'PAGE_EMITTER = emitter_for("dashboard/pages/control.py")\n'
            "render_page_header('Control', 'Registered output', emitter=PAGE_EMITTER)\n"
            "dataframe(rows, emitter=PAGE_EMITTER)\n",
            "dashboard/pages/other.py",
            False,
        ),
        ("internal helper", "def clamp(value):\n    return max(0, value)\n", None, True),
    ],
)
def test_dashboard_emitter_registration_controls(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    source: str,
    registration: str | None,
    passes: bool,
) -> None:
    """Run the real full gate against registered, raw, and aliased page controls."""
    module = "dashboard/pages/control.py"
    page = tmp_path / module
    page.parent.mkdir(parents=True)
    page.write_text(source)
    module_under_test = sys.modules[__name__]
    monkeypatch.setattr(module_under_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        module_under_test,
        "SWEPT_PACKAGES",
        (tmp_path / "dashboard", tmp_path / "src" / "api"),
    )
    if registration is not None:
        monkeypatch.setitem(DASHBOARD_EMITTERS, module, _control_emitter(registration))

    if passes:
        test_every_user_facing_emitter_is_registered()
    else:
        with pytest.raises(AssertionError, match="dashboard/pages/control.py"):
            test_every_user_facing_emitter_is_registered()


def test_a_registered_module_actually_has_a_probe_behind_it() -> None:
    """The registry may not be silenced by adding a string to it.

    `SURFACE_MODULES` is the set that switches the check above off, and until this
    test existed a module could be "registered" with a one-line edit and no probe
    attached — the failure this project already has a name for, a check that reads
    like it ran when it did not. A module belongs here only if `_surfaces()`
    actually builds a surface for it.
    """
    probed = {surface.name.split("::")[0] for surface in _surfaces()}
    unprobed = sorted(SURFACE_MODULES - probed)
    assert not unprobed, (
        "these modules are registered as probed surfaces but `_surfaces()` builds no "
        f"`exposure.Surface` for them, so nothing measures their columns: {unprobed}\n"
        "Either add the surface, or — if the module computes none of its columns — move it "
        "to `PASSTHROUGH_MODULES` and name the wire gate that covers it."
    )


def test_every_passthrough_declaration_names_a_gate_that_exists() -> None:
    """A pass-through declaration is only worth the test it points at.

    Each entry must name a test function that is really defined in
    `tests/leakage/test_wire_provenance.py`; a stale pointer would leave the
    module exempted here and covered nowhere.
    """
    wire_gate = REPO_ROOT / "tests" / "leakage" / "test_wire_provenance.py"
    assert wire_gate.exists(), f"the declared wire gate is missing: {wire_gate}"
    defined = {
        node.name
        for node in ast.walk(ast.parse(wire_gate.read_text()))
        if isinstance(node, ast.FunctionDef)
    }
    dangling: list[str] = []
    for module, reason in PASSTHROUGH_MODULES.items():
        assert reason.strip(), f"{module}: pass-through declaration states no reason"
        named = [word.strip("`.,") for word in reason.split() if word.startswith("test_")]
        named += [part for part in reason.replace("::", " ").split() if part.startswith("test_")]
        if not any(name in defined for name in named):
            dangling.append(f"{module} -> {reason}")
    assert not dangling, (
        "these pass-through declarations do not name a test that exists in "
        "tests/leakage/test_wire_provenance.py, so the module is exempted here and covered "
        "nowhere:\n  " + "\n  ".join(dangling)
    )


_EMITTER_SHAPES = {
    "streamlit dataframe": "import streamlit as st\ndef page(df):\n    st.dataframe(df)\n",
    "streamlit table": "import streamlit as st\ndef page(df):\n    st.table(df)\n",
    "streamlit data_editor": "import streamlit as st\ndef page(df):\n    st.data_editor(df)\n",
    # st.write with data, which the first version of this detector did not list.
    "streamlit write of a frame": "import streamlit as st\ndef page(df):\n    st.write(df)\n",
    "download button csv": "import streamlit as st\ndef page(df):\n    st.download_button('x', df.to_csv())\n",
    # THE HOLE THIS TEST WAS ADDED FOR. Measured 2026-07-29 against the detector
    # inherited from 962e0eb: a route handler returning rows was NOT reported.
    # It calls nothing on the emitter list and declares no response_model, so the
    # only evidence it is a user-facing surface is the decorator.
    "fastapi route returning rows": (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/queue')\n"
        "def queue():\n"
        "    return [{'recoverable_amt': 1.0}]\n"
    ),
    "fastapi async route": (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.post('/score')\n"
        "async def score():\n"
        "    return {'sim_p_overturn': 0.5}\n"
    ),
    "declared response model": (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "def register(model):\n"
        "    router.add_api_route('/x', lambda: None, response_model=model)\n"
    ),
    "pydantic model_dump": "def payload(row):\n    return row.model_dump()\n",
    "frame to_dict": "def payload(df):\n    return df.to_dict(orient='records')\n",
}

_NON_EMITTER_SHAPES = {
    "prose only": "import streamlit as st\ndef page():\n    st.write('Synthetic data. No real claims.')\n",
    "a scalar metric": "import streamlit as st\ndef page(n):\n    st.metric('Claims', n)\n",
    "plain helper": "def clamp(x):\n    return max(0.0, min(1.0, x))\n",
}


@pytest.mark.parametrize("shape", sorted(_EMITTER_SHAPES))
def test_the_emitter_detector_sees_each_user_facing_shape(shape: str) -> None:
    """Positive control on the DETECTOR, not on the repo.

    `test_every_user_facing_emitter_is_registered` is green on a tree with no
    dashboard and no API, and that green says nothing about whether the detector
    can see anything at all. It could not see a FastAPI route until 2026-07-29 —
    the gate was reporting a clean Phase 5 boundary while being blind to half of
    it. These are the shapes it must not go blind to again.
    """
    assert _emits_a_table(ast.parse(_EMITTER_SHAPES[shape])), (
        f"the emitter detector does not recognise `{shape}` as a user-facing surface, so a "
        "Phase 5 module written this way would never be registered and its columns would "
        "never be probed for simulated provenance"
    )


@pytest.mark.parametrize("shape", sorted(_NON_EMITTER_SHAPES))
def test_the_emitter_detector_leaves_non_tables_alone(shape: str) -> None:
    """Negative control. A detector that flags everything gets its list widened.

    The failure mode is indirect but real: over-firing forces text-only pages
    into the registry, the registry fills with entries nobody can write a
    meaningful probe for, and the next genuine finding is waved through with them.
    """
    assert not _emits_a_table(ast.parse(_NON_EMITTER_SHAPES[shape])), (
        f"`{shape}` puts no table in front of a user but the detector reports one"
    )


_DISPLAY_ONLY_LINKAGE_LABEL = "Facility name (DISPLAY ONLY)"


def _literal_text(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _column_config_label(value: ast.AST, symbols: dict[str, str]) -> tuple[str, ast.Call] | None:
    """Return a Streamlit column-config label expressed as a call argument.

    Mapping values are not always bare strings: Streamlit's actual column labels
    are normally the first argument to ``st.column_config.*Column(...)``.  Resolve
    import aliases so ``from streamlit import column_config as columns`` receives
    the same inspection as the ordinary ``st.column_config`` spelling.
    """
    if not isinstance(value, ast.Call):
        return None
    target = _resolved_call_target(value, symbols)
    if not target or ".column_config." not in target or not value.args:
        return None
    label = _literal_text(value.args[0])
    return (label, value) if label is not None else None


def _call_has_required_disclosures(tree: ast.AST, symbols: dict[str, str]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and _resolved_call_target(node, symbols) == "dashboard.components.required_disclosures"
        for node in ast.walk(tree)
    )


def _approved_display_only_linkage(
    column: str, label: str, config: ast.Call, tree: ast.AST, symbols: dict[str, str]
) -> bool:
    """The real-name crosswalk is a disclosed linkage, not a simulated claim value."""
    if column != "sim_display_facility_name" or label != _DISPLAY_ONLY_LINKAGE_LABEL:
        return False
    help_text = next(
        (_literal_text(keyword.value) for keyword in config.keywords if keyword.arg == "help"),
        None,
    )
    required_help = (
        "real cms facility name",
        "seeded random crosswalk",
        "not a key",
        "synthetic providers",
    )
    if help_text is None or not all(fragment in help_text.lower() for fragment in required_help):
        return False
    if not _call_has_required_disclosures(tree, symbols):
        return False

    from dashboard import disclosures
    from src.features.leakage import LeakageError, assert_no_forbidden_columns

    if "forbidden as a feature" not in disclosures.CROSSWALK_COLLISION.lower():
        return False
    for model in ("A", "C"):
        try:
            assert_no_forbidden_columns([column], model=model)
        except LeakageError:
            continue
        return False
    return True


def _display_label_offenders() -> list[str]:
    """Find simulated columns whose rendered label drops their provenance."""
    offenders: list[str] = []
    roots = [*SWEPT_PACKAGES, REPO_ROOT / "src" / "models"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            symbols = _imported_symbols(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values, strict=False):
                    column = _literal_text(key)
                    if column is None or "sim_" not in column:
                        continue
                    label = _literal_text(value)
                    config: ast.Call | None = None
                    call_label = _column_config_label(value, symbols)
                    if call_label is not None:
                        label, config = call_label
                    if label is None or "sim" in label.lower():
                        continue
                    if config is not None and _approved_display_only_linkage(
                        column, label, config, tree, symbols
                    ):
                        continue
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{key.lineno} {column!r} -> {label!r}"
                    )
    return offenders


def test_no_display_label_strips_the_simulated_marker() -> None:
    """Relabelling for display is the same defect wearing presentation clothes.

    `{"sim_denied_amount": "Denied amount"}` in a rename map or a column-config
    puts an unmarked simulated dollar figure in front of a reader exactly as a
    rename in code does. A label that says so — "Denied amount (simulated)" — is
    fine, and is the fix.
    """
    offenders = _display_label_offenders()
    assert not offenders, (
        "a simulated column is mapped to a display label that does not say it is simulated:\n  "
        + "\n  ".join(offenders)
        + "\nSay so in the label (e.g. 'Denied amount (simulated)') rather than dropping the "
        "marker on the way to the screen."
    )


@pytest.mark.parametrize(
    ("name", "source", "passes"),
    [
        (
            "rework cost without simulated marker",
            'import streamlit as st\nCONFIG = {"sim_rework_cost": st.column_config.NumberColumn("Rework $")}\n',
            False,
        ),
        (
            "expected net recovery abbreviated away",
            'import streamlit as st\nCONFIG = {"sim_expected_net_recovery": st.column_config.NumberColumn("ENR")}\n',
            False,
        ),
        (
            "deadline without simulated marker",
            'import streamlit as st\nCONFIG = {"sim_days_to_deadline": st.column_config.NumberColumn("Days left")}\n',
            False,
        ),
        (
            "correct simulated labels",
            "import streamlit as st\n"
            "CONFIG = {\n"
            '    "sim_rework_cost": st.column_config.NumberColumn("Simulated rework cost"),\n'
            '    "sim_expected_net_recovery": st.column_config.NumberColumn("Simulated expected net recovery"),\n'
            '    "sim_days_to_deadline": st.column_config.NumberColumn("Simulated days to deadline"),\n'
            "}\n",
            True,
        ),
        (
            "ordinary source label",
            'import streamlit as st\nCONFIG = {"billed_charge": st.column_config.NumberColumn("Billed charge")}\n',
            True,
        ),
        (
            "approved display-only crosswalk name",
            "from dashboard.components import required_disclosures\n"
            "from streamlit import column_config as columns\n"
            "required_disclosures()\n"
            "CONFIG = {\n"
            '    "sim_display_facility_name": columns.TextColumn(\n'
            '        "Facility name (DISPLAY ONLY)",\n'
            '        help="A real CMS facility name attached by a seeded random crosswalk. NOT a key. 2,816 names carry 4,876 synthetic providers, worst case 15:1.",\n'
            "    )\n"
            "}\n",
            True,
        ),
        (
            "display-only crosswalk name without the shared disclosure",
            "import streamlit as st\n"
            "CONFIG = {\n"
            '    "sim_display_facility_name": st.column_config.TextColumn(\n'
            '        "Facility name (DISPLAY ONLY)",\n'
            '        help="A real CMS facility name attached by a seeded random crosswalk. NOT a key. 2,816 names carry 4,876 synthetic providers, worst case 15:1.",\n'
            "    )\n"
            "}\n",
            False,
        ),
    ],
)
def test_display_label_detector_controls(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    source: str,
    passes: bool,
) -> None:
    """Run call-expression label controls through the complete display-label gate."""
    page = tmp_path / "dashboard" / "pages" / "control.py"
    page.parent.mkdir(parents=True)
    page.write_text(source)
    module_under_test = sys.modules[__name__]
    monkeypatch.setattr(module_under_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        module_under_test,
        "SWEPT_PACKAGES",
        (tmp_path / "dashboard", tmp_path / "src" / "api"),
    )

    if passes:
        test_no_display_label_strips_the_simulated_marker()
    else:
        with pytest.raises(AssertionError, match="dashboard/pages/control.py"):
            test_no_display_label_strips_the_simulated_marker()


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
