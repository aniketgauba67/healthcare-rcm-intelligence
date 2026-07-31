"""One-shot Compose gate for the complete tracked PostgreSQL object contract."""

from __future__ import annotations

import sys

from src.infra.postgres_contract import validate_postgres_contract


def main() -> int:
    try:
        report = validate_postgres_contract()
    except Exception as error:
        print(
            f"PostgreSQL initialization contract failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    print(f"PostgreSQL initialization contract satisfied: {report.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
