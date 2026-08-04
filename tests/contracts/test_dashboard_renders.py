"""Every dashboard page RENDERS, and the §6 banner is in what it rendered.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

WHY THIS EXISTS BESIDE test_dashboard_banner.py RATHER THAN INSTEAD OF IT
------------------------------------------------------------------------
That file parses each page and checks a banner CALL is present. It is a good
check and it stays. It is also, on its own, not enough — and qa-reviewer-p18
measured the exact gap on the first tree where the pages existed:

    dashboard/pages/ar_recovery.py:43 and work_queue.py:61 call
    `render_page_header(title, subtitle, banner_extra=...)`.
    `dashboard/components.py:83` defines `render_page_header(title, subtitle)`.

Both pages raised `TypeError: render_page_header() got an unexpected keyword
argument 'banner_extra'` on the FIRST statement that puts anything on screen, so
both rendered ZERO blocks — a blank page — while the other three rendered 16, 16
and 41. A static gate cannot see that, and the failure mode it opens is worse than
a missing call: a page can carry a perfectly good banner call on line 50 and crash
on line 43, and every name-matching check stays green over a page nobody can load.

So this gate asks the question the other one cannot: run the page and look at what
came out. It is the [PASSTHROUGH-BLIND] lesson applied to the screen — the
instrument that runs has to be as strong as the claim its green implies.

WHY A SUBPROCESS PER PAGE
-------------------------
Running all five pages through `AppTest` in one interpreter SEGFAULTS (exit 139)
after the first page: the pages pull duckdb, xgboost and shap into a process
`AppTest` is also driving. Each page therefore gets its own interpreter, which is
also the truthful arrangement — `streamlit run` executes a page as a script, and a
page that only works second is not a page that works.

WHAT THIS CANNOT SEE
--------------------
It renders against the committed bundle with `role=Analyst`, so a branch reachable
only under another role or another data source is not exercised, and it checks that
the banner TEXT is present rather than that it is visually prominent. It does not
check figures; `dashboard/reconcile.py` and
`tests/contracts/test_dashboard_reconciliation.py` do that.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "dashboard"
PAGES = DASHBOARD / "pages"

#: Rendered in one interpreter each, driven by streamlit's own AppTest harness.
#:
#: TIMEOUT, MEASURED — and a cautionary tale about measuring on a sick machine.
#: The FIRST page rendered costs more than the rest: 21.48s for ar_recovery.py
#: against ~1.2s for each page after it, the difference being one-time
#: interpreter, import and cache warm-up rather than anything about the page.
#: 240s is therefore ~11x margin and is correct. Do not raise it: a larger value
#: only delays how long a genuinely hung page takes to report.
#:
#: It was briefly raised to 450s on the strength of a 271.13s reading for that
#: same test. That reading was wrong by 12.6x. It came from a broken .venv whose
#: `ruff` binary would not start at all, a four-day-old orphaned `uv run
#: streamlit` server holding the uv environment lock, an iCloud sync daemon at
#: 88% CPU over the repo, and a Time Machine backup — all at once. The same
#: contamination made two of this file's negative controls appear to fail under
#: deterministic ordering, which was filed as an order-dependence defect and
#: later withdrawn: they pass in isolation and in suite context on a healthy
#: toolchain.
#: The lesson worth keeping is not the number. It is that a timing measurement
#: taken while the toolchain is degraded is not evidence, and a test suite is
#: exactly the instrument least able to tell you its own environment is sick.
_APPTEST_TIMEOUT_SECONDS = 240

_RUNNER = """
import json, sys, warnings
warnings.filterwarnings("ignore")
from streamlit.testing.v1 import AppTest

page, out, role = sys.argv[1], sys.argv[2], sys.argv[3]
result = {"page": page}
try:
    app = AppTest.from_file(page, default_timeout=__APPTEST_TIMEOUT__)
    app.session_state["role"] = role
    app.run()
    result["exceptions"] = [e.value.splitlines()[0][:200] for e in app.exception]
    result["errors"] = [e.value[:400] for e in app.error]
    result["markdown"] = [block.value for block in app.markdown]
    result["captions"] = [block.value for block in app.caption]
    result["blocks"] = len(app.markdown) + len(app.caption) + len(app.error)
except BaseException as exc:  # a page that cannot even be loaded is the finding
    result["exceptions"] = [f"{type(exc).__name__}: {exc}"[:200]]
    result["errors"] = []
    result["markdown"] = []
    result["captions"] = []
    result["blocks"] = 0
with open(out, "w") as fh:
    json.dump(result, fh)
"""


def _page_files() -> list[pathlib.Path]:
    if not PAGES.is_dir():
        return []
    return sorted(p for p in PAGES.glob("*.py") if p.name != "__init__.py")


def _render(page: pathlib.Path, tmp_path: pathlib.Path, role: str = "Analyst") -> dict:
    """Run one page in its own interpreter and return what it put on screen."""
    runner = tmp_path / "render_one_page.py"
    runner.write_text(_RUNNER.replace("__APPTEST_TIMEOUT__", str(_APPTEST_TIMEOUT_SECONDS)))
    out = tmp_path / f"{page.stem}.json"
    completed = subprocess.run(
        [sys.executable, str(runner), str(page.relative_to(REPO_ROOT)), str(out), role],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        # Strictly larger than _APPTEST_TIMEOUT_SECONDS so the inner AppTest
        # timeout is what reports; see the measurement note above.
        timeout=_APPTEST_TIMEOUT_SECONDS + 150,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    if not out.is_file():
        pytest.fail(
            f"rendering {page.name} killed the interpreter (exit {completed.returncode}), so it "
            "produced no result at all. A page that cannot be run cannot ship.\n"
            f"stderr tail:\n{completed.stderr[-1500:]}"
        )
    return json.loads(out.read_text())


def _ids() -> list[str]:
    return [p.name for p in _page_files()]


@pytest.fixture(params=_page_files(), ids=_ids())
def page(request) -> pathlib.Path:
    return request.param


def test_there_are_pages_to_render() -> None:
    """A skip that outlives the thing it waits for is how a gate stops being one."""
    if not DASHBOARD.is_dir():
        pytest.skip("dashboard/ does not exist yet — Phase 5 app work has not started")
    assert _page_files(), (
        f"dashboard/ exists but {PAGES.relative_to(REPO_ROOT)} holds no page modules, so every "
        "render check below is running against nothing. If pages live elsewhere, point "
        "_page_files() at them."
    )


def test_the_page_renders_without_raising(page: pathlib.Path, tmp_path: pathlib.Path) -> None:
    rendered = _render(page, tmp_path)
    assert not rendered["exceptions"], (
        f"{page.relative_to(REPO_ROOT)} raised while rendering, so a user gets a stack trace "
        f"instead of a page:\n  " + "\n  ".join(rendered["exceptions"]) + "\n\n"
        "CLAUDE.md §7 asks that the dashboard work from a clean clone. A page that throws on "
        "its first render is not covered by any name-matching or static check — "
        "tests/contracts/test_dashboard_banner.py stays green on a page that never loads."
    )


def test_the_page_puts_something_on_screen(page: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """A page that renders nothing is a blank page, whatever its source says."""
    rendered = _render(page, tmp_path)
    assert rendered["blocks"] > 0, (
        f"{page.relative_to(REPO_ROOT)} rendered zero markdown, caption and error blocks. It is "
        "a blank page. Both pages that failed this when it was written had raised on the first "
        "statement that renders anything."
    )


def test_the_rendered_page_carries_the_synthetic_data_banner(
    page: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """CLAUDE.md §6, checked in the OUTPUT rather than in the call graph.

    Anchored to `dashboard/disclosures.SYNTHETIC_DATA_BANNER`, the single source of
    truth the component renders, so rewording the banner does not make this red and
    dropping it does.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from dashboard import disclosures

    banner = disclosures.SYNTHETIC_DATA_BANNER
    anchor = max(banner.replace("*", "").split("."), key=len).strip()[:60]
    assert anchor, "SYNTHETIC_DATA_BANNER is empty; §6 has nothing to render"

    rendered = _render(page, tmp_path)
    everything = "\n".join(
        rendered["markdown"] + rendered["captions"] + rendered["errors"]
    ).replace("*", "")
    assert anchor.replace("*", "") in everything, (
        f"{page.relative_to(REPO_ROOT)} rendered without the §6 synthetic-data banner in its "
        "output. 'No page ships without it' is absolute, and §3.5 is why: Medicare FFS has one "
        "payer and our payer dimension is 100% simulated, so an unbannered payer page is a "
        "five-payer comparison of something that does not exist.\n"
        f"looked for: {anchor!r}\n"
        f"rendered {len(rendered['markdown'])} markdown / {len(rendered['captions'])} caption / "
        f"{len(rendered['errors'])} error blocks."
    )


# --------------------------------------------------------------------------
# Controls on the detector. Green here is a claim about the harness first.
# --------------------------------------------------------------------------

_A_PAGE_THAT_RAISES = """
import streamlit as st
st.title("Payer performance")
raise TypeError("unexpected keyword argument 'banner_extra'")
"""

_A_BLANK_PAGE = """
import streamlit as st
_total = 1 + 1
"""


def test_the_harness_reports_a_page_that_raises(tmp_path: pathlib.Path) -> None:
    page = PAGES / "_control_raises.py" if PAGES.is_dir() else tmp_path / "_control_raises.py"
    page.write_text(_A_PAGE_THAT_RAISES)
    try:
        rendered = _render(page, tmp_path)
    finally:
        page.unlink()
    assert rendered["exceptions"], (
        "the render harness reported no exception for a page whose last statement is `raise`. "
        "Every green above would then be evidence about nothing."
    )


def test_the_harness_reports_a_page_that_renders_nothing(tmp_path: pathlib.Path) -> None:
    page = PAGES / "_control_blank.py" if PAGES.is_dir() else tmp_path / "_control_blank.py"
    page.write_text(_A_BLANK_PAGE)
    try:
        rendered = _render(page, tmp_path)
    finally:
        page.unlink()
    assert rendered["blocks"] == 0 and not rendered["exceptions"], (
        "a page that renders nothing and raises nothing must be reported as blank, not as an "
        f"error: got blocks={rendered['blocks']} exceptions={rendered['exceptions']}"
    )
