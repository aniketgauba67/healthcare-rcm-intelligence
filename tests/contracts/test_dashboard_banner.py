"""docs/project_rules.md §6: every dashboard page renders the synthetic-data banner. No page ships without it.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Written before the dashboard exists,
on purpose. "Not yet built" and "passing" have to look different, and on this
project they have not: the [QUEUE-PREFIX] boundary went unchecked for three phases
because nothing was red, and a skip that outlives the thing it waits for is how a
gate quietly stops being one.

So this file skips while `dashboard/` is absent — with the reason stated out loud
— and becomes a build failure the moment a page ships without a banner. §3.5
raises the stakes: the payer dimension is 100% simulated and Medicare FFS has one
payer, so a payer page without the banner is a five-payer comparison of a payer
dimension that does not exist.

THE DETECTION CONTRACT, AND WHAT IT CANNOT SEE
----------------------------------------------
A page satisfies this gate by CALLING a shared banner component — a function
defined under `dashboard/` whose name contains "banner", or one of the names in
`BANNER_CALLS`. Two honest limitations, stated rather than papered over:

* it checks that the call is made, not that the rendered pixels say the right
  thing. `tests/leakage/test_plot_provenance_banner.py` had to verify a banner IN
  THE PNG for exactly this reason, and the equivalent for Streamlit is a render
  test qa will add once there is an app to render.
* it matches the component by a spelling. That is the shape [SPLIT-DISCOVERY] just
  retired for column roles, and it is accepted here for a different reason: this
  guard constrains a call that must exist, not a name that must not change, so a
  rename makes it red rather than silently blind — and the fix is one line in
  `BANNER_CALLS`, in a file qa owns. If the dashboard grows a declaration of its
  own page/banner structure, this should read that instead.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "dashboard"

# Extend here when the component lands under a name this does not match.
BANNER_CALLS = frozenset(
    {"render_synthetic_data_banner", "synthetic_data_banner", "provenance_banner", "banner"}
)

# Streamlit page modules render; helpers and data-access modules do not.
_RENDER_CALLS = frozenset(
    {"title", "header", "subheader", "markdown", "dataframe", "table", "plotly_chart", "metric"}
)


def _calls(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _page_modules(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Modules that put something on screen.

    `root` is an argument so the controls at the bottom can exercise this against a
    directory they build, rather than monkeypatching a module global.
    """
    root = root or DASHBOARD
    if not root.is_dir():
        return []
    pages: list[pathlib.Path] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text()
        if "streamlit" not in source:
            continue
        if _calls(ast.parse(source)) & _RENDER_CALLS:
            pages.append(path)
    return pages


def _renders_a_banner(path: pathlib.Path) -> bool:
    called = _calls(ast.parse(path.read_text()))
    return bool(called & BANNER_CALLS) or any("banner" in name.lower() for name in called if name)


def test_the_dashboard_exists_before_phase_5_is_accepted() -> None:
    """A placeholder that fails loudly rather than a suite that passes silently."""
    if not DASHBOARD.is_dir():
        pytest.skip(
            "dashboard/ does not exist yet — Phase 5 app work has not started. This file "
            "becomes a build failure the moment a page ships without the §6 banner."
        )
    assert _page_modules(), (
        "dashboard/ exists but no module renders anything, so the §6 banner check is running "
        "against nothing. If pages live elsewhere, point _page_modules() at them."
    )


def test_every_page_renders_the_synthetic_data_banner() -> None:
    pages = _page_modules()
    if not pages:
        pytest.skip(
            "no dashboard pages yet (see test_the_dashboard_exists_before_phase_5_is_accepted)"
        )

    naked = [str(p.relative_to(REPO_ROOT)) for p in pages if not _renders_a_banner(p)]
    assert not naked, (
        "these dashboard pages render content without calling the synthetic-data banner "
        "component (docs/project_rules.md §6, 'no page ships without it'):\n  "
        + "\n  ".join(naked)
        + "\n\n"
        "§3.5 is why this is absolute rather than a nicety: Medicare FFS has ONE payer and our "
        "payer dimension is 100% simulated, so an unbannered payer page is a five-payer "
        "comparison of something that does not exist. Call the shared component at the top of "
        f"each page; if it is named outside {sorted(BANNER_CALLS)}, add its name there."
    )


# --------------------------------------------------------------------------
# Controls — the two tests above can only skip today, and a skip is not evidence
# --------------------------------------------------------------------------

_PAGE_WITHOUT = """
import streamlit as st
from dashboard.data import load_payer_kpis

st.title("Payer performance")
st.dataframe(load_payer_kpis())
"""

_PAGE_WITH = """
import streamlit as st
from dashboard.components import render_synthetic_data_banner
from dashboard.data import load_payer_kpis

render_synthetic_data_banner()
st.title("Payer performance")
st.dataframe(load_payer_kpis())
"""

_HELPER = """
import pandas as pd

def load_payer_kpis() -> pd.DataFrame:
    return pd.read_sql("select * from rcm.vw_payer_kpis", _engine())
"""


def _write(tmp_path: pathlib.Path, name: str, source: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(source)
    return path


def test_the_check_catches_a_page_with_no_banner(tmp_path) -> None:
    assert not _renders_a_banner(_write(tmp_path, "page.py", _PAGE_WITHOUT))


def test_the_check_accepts_a_page_that_calls_the_component(tmp_path) -> None:
    assert _renders_a_banner(_write(tmp_path, "page.py", _PAGE_WITH))


def test_a_render_free_helper_is_not_treated_as_a_page(tmp_path) -> None:
    """Otherwise the gate demands a banner from a module nobody looks at, and the
    fastest way to green becomes sprinkling banner calls through the data layer."""
    _write(tmp_path, "data.py", _HELPER)
    _write(tmp_path, "page.py", _PAGE_WITH)
    assert [p.name for p in _page_modules(tmp_path)] == ["page.py"]


def test_the_sweep_finds_a_page_that_would_be_reported(tmp_path) -> None:
    """End to end on a directory that looks like the dashboard about to be built."""
    _write(tmp_path, "app.py", _PAGE_WITHOUT)
    _write(tmp_path, "ok.py", _PAGE_WITH)
    naked = [p.name for p in _page_modules(tmp_path) if not _renders_a_banner(p)]
    assert naked == ["app.py"]
