"""A plot travels without its prose. It has to carry the banner itself.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). Expected RED until the SHAP plot carries a banner.

docs/project_rules.md §1: "No simulated value may ever be presented as real." §6 makes the
same demand of the dashboard — "every page renders the synthetic-data banner
component, no page ships without it". A PNG is the most detachable artifact this
project produces: it gets dragged into a slide, pasted into a chat, screenshotted
into a portfolio. By the time anyone looks at it, `docs/model_card.md` and its
excellent warning are nowhere nearby.

`models_artifacts/model_a/calibration_curve.png` already does this correctly —
it carries "SIMULATED adjudication layer on CMS synthetic claims — not real
denials." along the bottom. So the standard is established in this repo and the
mechanism exists; `_shap_plot` in `src/models/train.py` just does not use it, and
it is the MORE quotable of the two. A bar chart headed by
`sim_payer_prior_denial_rate` and `sim_auth_missing` reads, to anyone who does not
know the naming convention, as a finding about what drives real denials.

Checked structurally rather than by rendering: a function that writes a figure
must also write the banner onto that figure. That keeps the guard cheap and makes
it fire on a NEW plot the day it is added, which is the case that matters — the
existing plots can be checked by eye once, the next one cannot.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Modules that write figures. Add to this list when a new one appears.
_PLOTTING_MODULES = (
    _REPO_ROOT / "src" / "models" / "train.py",
    _REPO_ROOT / "src" / "models" / "model_c.py",
)

_BANNER_MARKER = "SIMULATED"


def _calls_named(node: ast.AST, attribute: str) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == attribute
        for child in ast.walk(node)
    )


def _string_constants(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _figure_writing_functions(module: pathlib.Path) -> list[ast.FunctionDef]:
    tree = ast.parse(module.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _calls_named(node, "savefig")
    ]


@pytest.mark.parametrize("module", _PLOTTING_MODULES, ids=lambda path: path.name)
def test_every_saved_figure_carries_the_synthetic_data_banner(module: pathlib.Path) -> None:
    if not module.exists():
        pytest.skip(f"{module.name} does not exist on this tree")

    offenders = [
        function.name
        for function in _figure_writing_functions(module)
        if not any(_BANNER_MARKER in text for text in _string_constants(function))
    ]

    assert not offenders, (
        f"{module.relative_to(_REPO_ROOT)}: these functions save a figure without writing a "
        f"synthetic-data banner onto it: {offenders}. `_calibration_plot` in the same file "
        "shows the pattern — a `fig.text(...)` footer reading 'SIMULATED adjudication layer on "
        "CMS synthetic claims — not real denials.' A PNG is the most detachable artifact here; "
        "it is read in a slide deck long after the model card is out of sight, and docs/project_rules.md §1 "
        "does not have an exception for charts."
    )


def test_the_banner_pattern_is_actually_present_somewhere() -> None:
    """Control: if no function carries a banner, the test above is silent for the wrong reason."""
    banner_carrying = [
        function.name
        for module in _PLOTTING_MODULES
        if module.exists()
        for function in _figure_writing_functions(module)
        if any(_BANNER_MARKER in text for text in _string_constants(function))
    ]
    assert banner_carrying, (
        "no figure-writing function in the ML surface carries a synthetic-data banner, so the "
        "convention this file enforces does not exist yet and the guard above proves nothing."
    )
