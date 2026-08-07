"""Static guard: the `sim_` prefix must survive the view boundary (docs/project_rules.md §3.2).

The simulated-linkage facility columns live in `rcm.sim_facility_crosswalk` and are
surfaced by `sql/views/vw_claim_enriched.sql`, which is the flattened one-row-per-claim
matrix the Phase 4 feature store consumes. The §4 leakage blacklist matches on COLUMN
NAMES, so aliasing the prefix away at the view boundary (`fx.sim_facility_ccn as
facility_ccn`) would delete the provenance marker exactly where §4 depends on it.

This test parses the shipped view SQL — no database required, so it runs in CI where
`tests/integration/test_crosswalk_prefix_postgres.py` (the live-catalog equivalent)
is skipped. It only inspects executable SQL: comments are stripped first, because the
header blocks legitimately discuss the historical bare names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VIEWS_DIR = Path(__file__).resolve().parents[2] / "sql" / "views"

# Crosswalk-sourced simulated-linkage columns, in their bare (pre-§3.2) spelling.
_BARE_LINKAGE = re.compile(
    r"(?<![\w])(?<!sim_)(?:display_)?facility_(?:ccn|name|state|type)\b",
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """Drop `-- ...` line comments and `/* ... */` blocks, keeping line structure."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _view_files() -> list[Path]:
    files = sorted(VIEWS_DIR.glob("vw_*.sql"))
    assert files, f"no view SQL found under {VIEWS_DIR}"
    return files


@pytest.mark.parametrize("path", _view_files(), ids=lambda p: p.name)
def test_view_sql_never_uses_a_bare_simulated_linkage_column(path: Path):
    """No view may read from, or alias to, an unprefixed simulated-linkage column."""
    body = _strip_sql_comments(path.read_text())
    offenders = sorted({m.group(0) for m in _BARE_LINKAGE.finditer(body)})
    assert not offenders, (
        f"{path.name} references simulated-linkage column(s) without the `sim_` "
        f"prefix: {offenders}. docs/project_rules.md §3.2 requires the prefix to survive the "
        f"view boundary — do not alias it away."
    )


def test_claim_enriched_exposes_the_prefixed_facility_columns():
    """The base view must actually emit the four prefixed columns (not just avoid
    the bare ones — a deletion would also satisfy the negative test above)."""
    body = _strip_sql_comments((VIEWS_DIR / "vw_claim_enriched.sql").read_text())
    for col in (
        "sim_facility_ccn",
        "sim_facility_name",
        "sim_facility_state",
        "sim_facility_type",
    ):
        assert re.search(rf"\bfx\.{col}\b", body), f"vw_claim_enriched no longer selects {col}"


def test_claim_enriched_drops_before_create_so_the_rename_is_applyable():
    """`create or replace view` cannot rename an output column. The base view must
    drop-cascade first or `make views` fails against a warehouse holding the old
    (bare-name) shape."""
    body = _strip_sql_comments((VIEWS_DIR / "vw_claim_enriched.sql").read_text()).lower()
    assert re.search(r"drop\s+view\s+if\s+exists\s+rcm\.vw_claim_enriched\s+cascade", body), (
        "vw_claim_enriched.sql must drop-cascade before create (output columns are renamed)"
    )
