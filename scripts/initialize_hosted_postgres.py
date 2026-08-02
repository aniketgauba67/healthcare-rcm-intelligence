"""Initialize and validate the tracked warehouse contract in a dedicated database.

The tracked warehouse DDL intentionally drops and recreates its own objects. This
hosted entrypoint therefore applies it only when the ``rcm`` schema is absent. A
valid schema is verified and reused; an empty, partial, or unexpected ``rcm``
schema fails without mutation.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.infra.postgres_contract import (  # noqa: E402
    SCHEMA,
    ContractReport,
    PostgresContractError,
    connect,
    validate_inventory,
    validate_postgres_contract,
)

DDL_FILES = tuple(sorted((REPO_ROOT / "sql" / "ddl").glob("*.sql")))
VIEW_FILES = (
    REPO_ROOT / "sql" / "views" / "vw_claim_enriched.sql",
    *tuple(
        path
        for path in sorted((REPO_ROOT / "sql" / "views").glob("vw_*.sql"))
        if path.name != "vw_claim_enriched.sql"
    ),
)


@dataclass(frozen=True)
class HostedInitializationReport:
    """Non-secret evidence returned by one initialization run."""

    contract: ContractReport
    postgres_version: str
    initialized: bool


def _schema_state(connection: Any) -> tuple[bool, tuple[tuple[str, str], ...]]:
    with connection.cursor() as cursor:
        cursor.execute("select exists(select 1 from pg_namespace where nspname = %s)", (SCHEMA,))
        exists = bool(cursor.fetchone()[0])
        if not exists:
            return False, ()
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
        return True, tuple(cursor.fetchall())


def _apply_fresh_schema(connection: Any) -> None:
    with connection.cursor() as cursor:
        for path in (*DDL_FILES, *VIEW_FILES):
            cursor.execute(path.read_text())


def initialize_hosted_postgres(
    connection: Any | None = None,
    *,
    validate_only: bool = False,
) -> HostedInitializationReport:
    """Create an absent schema or validate a complete one without repairing partial state."""
    owned_connection = connection is None
    resolved = connection or connect()
    initialized = False
    try:
        with resolved:
            with resolved.cursor() as cursor:
                cursor.execute(
                    "select pg_advisory_xact_lock(hashtext(%s))",
                    ("healthcare-rcm-intelligence:hosted-initialization",),
                )
                cursor.execute("set local lock_timeout = '10s'")
                cursor.execute("set local statement_timeout = '120s'")

            schema_exists, relations = _schema_state(resolved)
            if not schema_exists:
                if validate_only:
                    raise PostgresContractError(f"missing application schema {SCHEMA}")
                _apply_fresh_schema(resolved)
                initialized = True
            elif not relations:
                raise PostgresContractError(
                    f"application schema {SCHEMA} exists but contains no tracked relations; "
                    "refusing destructive initialization"
                )
            else:
                inventory_errors = validate_inventory(relations)
                if inventory_errors:
                    raise PostgresContractError("; ".join(inventory_errors))

            contract = validate_postgres_contract(resolved)
            with resolved.cursor() as cursor:
                cursor.execute("show server_version")
                postgres_version = str(cursor.fetchone()[0])
    finally:
        if owned_connection:
            resolved.close()

    return HostedInitializationReport(contract, postgres_version, initialized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize an absent hosted rcm schema and validate its exact contract."
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate an existing complete schema; never initialize",
    )
    args = parser.parse_args(argv)
    try:
        report = initialize_hosted_postgres(validate_only=args.validate_only)
    except Exception as error:
        print(
            f"Hosted PostgreSQL contract failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1

    action = "initialized and validated" if report.initialized else "validated and reused"
    print(f"Hosted PostgreSQL {action}: {report.contract.summary()}")
    print(f"PostgreSQL version: {report.postgres_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
