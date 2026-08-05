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
    recreate: bool = False,
) -> HostedInitializationReport:
    """Create an absent schema, rebuild it on `recreate`, or validate an existing one.

    WITHOUT `recreate` THIS APPLIES NO SQL TO AN EXISTING SCHEMA. That is
    deliberate — it must never destructively rewrite a populated warehouse
    because someone ran it twice — but it means a DDL or view change made after
    the schema exists will NOT be picked up, and the command will still exit 0.
    `recreate` is the only path that applies a change to an existing database,
    and the caller has to ask for it explicitly because it drops the schema.
    """
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
            if recreate:
                if validate_only:
                    raise PostgresContractError("cannot recreate under validate_only")
                with resolved.cursor() as cursor:
                    cursor.execute(f"drop schema if exists {SCHEMA} cascade")
                _apply_fresh_schema(resolved)
                initialized = True
            elif not schema_exists:
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
    parser.add_argument(
        "--recreate",
        action="store_true",
        help=(
            "DROP the existing rcm schema and rebuild it from the tracked DDL. Required to "
            "apply a schema change to a database that already holds an older one; without "
            "it this command validates and changes nothing."
        ),
    )
    args = parser.parse_args(argv)
    if args.validate_only and args.recreate:
        print("--validate-only and --recreate are mutually exclusive", file=sys.stderr)
        return 2
    try:
        report = initialize_hosted_postgres(
            validate_only=args.validate_only, recreate=args.recreate
        )
    except Exception as error:
        print(
            f"Hosted PostgreSQL contract failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1

    if report.initialized:
        print(f"Hosted PostgreSQL initialized and validated: {report.contract.summary()}")
    else:
        # NOT a success message. This path applied NO SQL, and it used to print
        # "validated and reused", which reads like the schema was brought up to
        # date. It cost a deploy cycle: a schema change was made, this was run,
        # it reported something that looked like success, and nothing had
        # changed. Say plainly that nothing was written.
        print(
            "Hosted PostgreSQL VALIDATED ONLY — NOTHING WAS WRITTEN. "
            f"The existing schema already matches the contract: {report.contract.summary()}"
        )
        print(
            "If you changed the tracked DDL or views and expected them applied, they were "
            "NOT. Re-run with --recreate."
        )
    print(f"PostgreSQL version: {report.postgres_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
