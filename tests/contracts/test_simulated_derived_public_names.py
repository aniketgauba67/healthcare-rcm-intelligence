"""Public prose and SQL headers must follow the simulated-derived field contract."""

from __future__ import annotations

import pathlib
import re

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "obsolete"),
    [
        ("dashboard/pages/model_data_quality.py", ("ar_open_flag",)),
        ("docs/model_card.md", ("dollars_at_stake",)),
        (
            "sql/views/vw_claim_enriched.sql",
            ("clean_claim_flag", "first_pass_paid_flag", "ar_open_flag", "ar_balance_amt"),
        ),
        ("sql/views/vw_ar_aging.sql", ("ar_open_flag", "ar_balance_amt")),
        ("sql/views/vw_work_queue_priority.sql", ("heuristic_priority_score",)),
    ],
)
def test_public_contract_surfaces_do_not_describe_obsolete_unmarked_fields(
    relative_path: str, obsolete: tuple[str, ...]
) -> None:
    """Keep the review finding scoped to public field-name references, not historical prose."""
    text = (REPO_ROOT / relative_path).read_text()
    found = [name for name in obsolete if re.search(rf"(?<!sim_)\\b{re.escape(name)}\\b", text)]
    assert not found, f"{relative_path} still describes obsolete unmarked field(s): {found}"
