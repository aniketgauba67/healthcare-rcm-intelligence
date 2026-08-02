"""Every documented PostgreSQL dashboard page degrades honestly when DDL is unloaded."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = REPO_ROOT / "dashboard" / "pages"
PAGE_FILES = tuple(sorted(path for path in PAGES.glob("*.py") if path.name != "__init__.py"))

_RUNNER = r"""
import json, os, pathlib, sys, warnings
warnings.filterwarnings("ignore")
os.environ["RCM_DATA_SOURCE"] = "postgres"

from streamlit.testing.v1 import AppTest
from dashboard import data
from src.demo import spec
from src.demo.bundle import open_bundle

bundle = open_bundle()
frames = {
    dataset.name: bundle.query(f'select * from "{dataset.name}" limit 0')
    for dataset in spec.WAREHOUSE_DATASETS
}

class EmptyPostgresSource:
    kind = "postgres"

    def available(self):
        return set(frames)

    def frame(self, dataset):
        return frames[dataset].copy()

    def describe(self):
        return {
            "kind": "postgres",
            "path": "postgresql://rcm:***@postgres:5432/rcm_warehouse",
            "git_commit": "n/a (live warehouse)",
            "git_tree_dirty": False,
            "built_at_utc": "n/a (live warehouse)",
            "source_vintages": {},
        }

source = EmptyPostgresSource()
data.get_source = lambda: source
for cached in (
    data._source,
    data.load,
    data.source_description,
    data.manifest,
    data.model_metrics,
    data.executive_totals,
    data.model_a_test_fold,
    data.work_queue,
):
    cached.clear()

page, output = sys.argv[1], sys.argv[2]
app = AppTest.from_file(page, default_timeout=240)
app.session_state["role"] = "Analyst"
app.run()

result = {
    "exceptions": [element.value for element in app.exception],
    "warnings": [element.value for element in app.warning],
    "infos": [element.value for element in app.info],
    "errors": [element.value for element in app.error],
    "success": [element.value for element in app.success],
    "metrics": [
        {"label": element.label, "value": element.value}
        for element in app.metric
    ],
    "expanders": [element.label for element in app.get("expander")],
    "markdown": [element.value for element in app.get("markdown")],
    "subheaders": [element.value for element in app.subheader],
}
pathlib.Path(output).write_text(json.dumps(result))
"""


def _render(page: pathlib.Path, tmp_path: pathlib.Path) -> dict:
    runner = tmp_path / "render_empty_postgres.py"
    runner.write_text(_RUNNER)
    output = tmp_path / f"{page.stem}.json"
    completed = subprocess.run(
        [sys.executable, str(runner), str(page), str(output)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    if not output.is_file():
        pytest.fail(
            f"{page.name} produced no PostgreSQL-mode result (exit {completed.returncode}):\n"
            f"{completed.stderr[-1500:]}"
        )
    return json.loads(output.read_text())


@pytest.fixture(params=PAGE_FILES, ids=lambda path: path.name)
def postgres_page(request, tmp_path) -> tuple[pathlib.Path, dict]:
    page = request.param
    return page, _render(page, tmp_path)


def test_every_empty_postgres_page_renders_without_exception(postgres_page) -> None:
    page, rendered = postgres_page
    assert not rendered["exceptions"], f"{page.name}: {rendered['exceptions']}"


def test_every_empty_postgres_page_discloses_unavailable_or_incomplete_data(
    postgres_page,
) -> None:
    page, rendered = postgres_page
    text = "\n".join(rendered["warnings"] + rendered["infos"] + rendered["errors"]).lower()
    assert "unavailable" in text or "incomplete" in text, f"{page.name}: {text}"
    assert not any("all 17/17" in message.lower() for message in rendered["success"])


def test_empty_postgres_pages_do_not_fabricate_zero_operational_metrics(postgres_page) -> None:
    page, rendered = postgres_page
    operational = {
        "Claims submitted",
        "Denial rate",
        "Open claims",
        "A/R balance",
        "Denied dollars",
        "Checks",
        "Passing",
        "Critical failures",
    }
    fabricated = [metric for metric in rendered["metrics"] if metric["label"] in operational]
    assert not fabricated, f"{page.name} rendered operational metrics from no rows: {fabricated}"


def test_every_empty_postgres_page_keeps_required_disclosures(postgres_page) -> None:
    page, rendered = postgres_page
    output = "\n".join(rendered["expanders"] + rendered["markdown"]).replace("*", "")
    assert "claims are vintage" in output, page.name
    assert "4,876 synthetic billing providers" in output, page.name


def test_ar_recovery_names_each_unavailable_section(tmp_path) -> None:
    rendered = _render(PAGES / "ar_recovery.py", tmp_path)
    text = "\n".join(rendered["warnings"])
    assert "A/R aging data is unavailable" in text
    assert "Payer-performance data is unavailable" in text
    assert "Appeal-recovery data is unavailable" in text
    assert {
        "Accounts receivable aging",
        "Payer performance — every payer on this chart is invented",
        "Appeal recovery",
    } <= set(rendered["subheaders"])
