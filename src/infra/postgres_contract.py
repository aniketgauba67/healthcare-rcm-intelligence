"""Exact PostgreSQL object contract for the containerized warehouse."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2 import sql

SCHEMA = "rcm"

BASE_TABLES: tuple[str, ...] = (
    "dim_beneficiary",
    "dim_date",
    "dim_discharge_status",
    "dim_drg",
    "dim_provider",
    "dq_quarantine",
    "fact_claim_diagnosis",
    "fact_claim_revenue_line",
    "fact_inpatient_claim",
    "ref_carc",
    "ref_hcpcs",
    "ref_icd10cm",
    "ref_icd10pcs",
    "ref_msdrg",
    "sim_appeals",
    "sim_authorization_eligibility",
    "sim_claim_adjudication",
    "sim_documentation_coding",
    "sim_facility_crosswalk",
    "sim_operating_costs",
    "sim_payer",
    "sim_provider_crosswalk",
    "sim_service_line",
    "sim_workflow_events",
)

VIEWS: tuple[str, ...] = (
    "vw_ar_aging",
    "vw_claim_enriched",
    "vw_clean_claim_performance",
    "vw_data_quality_scorecard",
    "vw_denial_root_cause",
    "vw_executive_rcm_summary",
    "vw_model_monitoring",
    "vw_payer_performance",
    "vw_work_queue_priority",
)

REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "fact_inpatient_claim": ("claim_sk", "clm_id", "provider_key"),
    "sim_claim_adjudication": ("claim_sk", "sim_denial_flag", "sim_paid_amount"),
    "vw_claim_enriched": ("claim_sk", "sim_denial_flag", "sim_ar_open_flag"),
    "vw_executive_rcm_summary": ("sim_submission_year_month", "sim_claims_submitted"),
    "vw_model_monitoring": ("feature_name", "metric_kind", "sim_metric_value"),
    "vw_work_queue_priority": ("claim_sk", "sim_priority_tier"),
}

RELATION_KINDS: Mapping[str, str] = {"r": "base table", "p": "base table", "v": "view"}


class PostgresContractError(RuntimeError):
    """The initialized PostgreSQL warehouse does not match its tracked DDL."""


@dataclass(frozen=True)
class ContractReport:
    """Verified PostgreSQL warehouse inventory."""

    schema: str
    base_tables: tuple[str, ...]
    views: tuple[str, ...]

    @property
    def total_relations(self) -> int:
        return len(self.base_tables) + len(self.views)

    def summary(self) -> str:
        return (
            f"{self.schema}: {len(self.base_tables)} base tables + {len(self.views)} views "
            f"= {self.total_relations} tracked table-like relations"
        )


def expected_relations() -> dict[str, str]:
    """Every tracked PostgreSQL relation and its required type."""
    return {**dict.fromkeys(BASE_TABLES, "base table"), **dict.fromkeys(VIEWS, "view")}


def validate_inventory(rows: Iterable[tuple[str, str]]) -> list[str]:
    """Return exact-name/type inventory violations from pg_class rows."""
    actual = {
        name: RELATION_KINDS.get(kind, f"unsupported relation type {kind!r}") for name, kind in rows
    }
    expected = expected_relations()
    errors: list[str] = []

    for name, relation_type in expected.items():
        if name not in actual:
            errors.append(f"missing {relation_type} {SCHEMA}.{name}")
        elif actual[name] != relation_type:
            errors.append(f"{SCHEMA}.{name} must be a {relation_type}, found {actual[name]}")

    extras = sorted(set(actual) - set(expected))
    if extras:
        errors.append(
            f"unexpected tracked-schema relations: {', '.join(f'{SCHEMA}.{name}' for name in extras)}"
        )
    return errors


def validate_columns(actual: Mapping[str, set[str]]) -> list[str]:
    """Return required sentinel-column violations."""
    errors: list[str] = []
    for relation, required in REQUIRED_COLUMNS.items():
        missing = sorted(set(required) - actual.get(relation, set()))
        if missing:
            errors.append(f"{SCHEMA}.{relation} missing columns: {', '.join(missing)}")
    return errors


def connect() -> Any:
    """Connect using a hosted DSN or the Compose PostgreSQL environment."""
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return psycopg2.connect(database_url, connect_timeout=3)
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "rcm"),
        password=os.environ.get("POSTGRES_PASSWORD", "rcm_demo_only"),
        dbname=os.environ.get("POSTGRES_DB", "rcm_warehouse"),
        connect_timeout=3,
    )


def validate_postgres_contract(connection: Any | None = None) -> ContractReport:
    """Validate names, relation types, sentinel columns, and view queryability."""
    owned_connection = connection is None
    resolved = connection or connect()
    try:
        with resolved.cursor() as cursor:
            cursor.execute(
                "select exists(select 1 from pg_namespace where nspname = %s)", (SCHEMA,)
            )
            if not cursor.fetchone()[0]:
                raise PostgresContractError(f"missing application schema {SCHEMA}")

            cursor.execute(
                """
                select c.relname, c.relkind
                  from pg_class c
                  join pg_namespace n on n.oid = c.relnamespace
                 where n.nspname = %s
                   and c.relkind in ('r', 'p', 'v', 'm')
                 order by c.relname
                """,
                (SCHEMA,),
            )
            inventory = cursor.fetchall()
            errors = validate_inventory(inventory)

            cursor.execute(
                """
                select table_name, column_name
                  from information_schema.columns
                 where table_schema = %s
                """,
                (SCHEMA,),
            )
            columns: dict[str, set[str]] = {}
            for relation, column in cursor.fetchall():
                columns.setdefault(relation, set()).add(column)
            errors.extend(validate_columns(columns))

            if not errors:
                for view in VIEWS:
                    try:
                        cursor.execute(
                            sql.SQL("select * from {}.{} limit 0").format(
                                sql.Identifier(SCHEMA), sql.Identifier(view)
                            )
                        )
                    except Exception as error:
                        resolved.rollback()
                        errors.append(
                            f"{SCHEMA}.{view} is not queryable: {type(error).__name__}: {error}"
                        )
                        break

            if errors:
                raise PostgresContractError("; ".join(errors))
    finally:
        if owned_connection:
            resolved.close()

    return ContractReport(SCHEMA, BASE_TABLES, VIEWS)
