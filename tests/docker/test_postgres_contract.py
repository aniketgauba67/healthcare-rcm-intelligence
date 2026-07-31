"""The Docker warehouse contract must match the tracked PostgreSQL DDL exactly."""

from __future__ import annotations

import re
from pathlib import Path

from src.infra.postgres_contract import (
    BASE_TABLES,
    REQUIRED_COLUMNS,
    VIEWS,
    validate_columns,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[2]


def _tracked(pattern: re.Pattern[str], root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.glob("*.sql"):
        names.update(pattern.findall(path.read_text()))
    return names


def test_object_manifest_matches_the_tracked_ddl() -> None:
    table_pattern = re.compile(
        r"^create table(?: if not exists)? rcm\.([a-z0-9_]+)", re.IGNORECASE | re.MULTILINE
    )
    view_pattern = re.compile(
        r"^create or replace view rcm\.([a-z0-9_]+)", re.IGNORECASE | re.MULTILINE
    )
    assert _tracked(table_pattern, ROOT / "sql" / "ddl") == set(BASE_TABLES)
    assert _tracked(view_pattern, ROOT / "sql" / "views") == set(VIEWS)
    assert len(BASE_TABLES) == 24
    assert len(VIEWS) == 9


def test_complete_inventory_and_required_columns_pass() -> None:
    inventory = [(name, "r") for name in BASE_TABLES] + [(name, "v") for name in VIEWS]
    columns = {relation: set(required) for relation, required in REQUIRED_COLUMNS.items()}
    assert validate_inventory(inventory) == []
    assert validate_columns(columns) == []


def test_missing_view_is_rejected() -> None:
    inventory = [(name, "r") for name in BASE_TABLES] + [
        (name, "v") for name in VIEWS if name != "vw_model_monitoring"
    ]
    assert validate_inventory(inventory) == ["missing view rcm.vw_model_monitoring"]


def test_wrong_relation_type_is_rejected() -> None:
    inventory = [(name, "r") for name in BASE_TABLES] + [
        (name, "r" if name == "vw_model_monitoring" else "v") for name in VIEWS
    ]
    assert validate_inventory(inventory) == [
        "rcm.vw_model_monitoring must be a view, found base table"
    ]


def test_missing_base_table_and_required_column_are_rejected() -> None:
    inventory = [(name, "r") for name in BASE_TABLES if name != "fact_inpatient_claim"] + [
        (name, "v") for name in VIEWS
    ]
    assert validate_inventory(inventory) == ["missing base table rcm.fact_inpatient_claim"]
    columns = {relation: set(required) for relation, required in REQUIRED_COLUMNS.items()}
    columns["vw_claim_enriched"].remove("sim_denial_flag")
    assert validate_columns(columns) == ["rcm.vw_claim_enriched missing columns: sim_denial_flag"]
