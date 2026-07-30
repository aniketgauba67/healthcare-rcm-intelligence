"""Container readiness probes for the Compose application stack."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any

import psycopg2


def _postgres_ready() -> None:
    connection = psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        connect_timeout=3,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select current_database(), to_regclass('rcm.vw_executive_rcm_summary') is not null"
            )
            database, warehouse_ready = cursor.fetchone()
    finally:
        connection.close()
    if database != os.environ["POSTGRES_DB"] or not warehouse_ready:
        raise RuntimeError("PostgreSQL is reachable but the expected warehouse schema is not ready")


def _read_url(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=4) as response:
        return response.status, response.read()


def _api_ready() -> None:
    status, body = _read_url(os.environ.get("RCM_API_HEALTH_URL", "http://127.0.0.1:8000/health"))
    payload: dict[str, Any] = json.loads(body)
    if status != 200 or payload.get("status") != "ok":
        raise RuntimeError(
            f"API health is not ready: HTTP {status}, status={payload.get('status')!r}"
        )
    source = payload.get("data_source", {})
    if source.get("kind") != "bundle":
        raise RuntimeError(f"API is using unexpected data source {source.get('kind')!r}")


def _dashboard_ready() -> None:
    status, body = _read_url(
        os.environ.get("RCM_DASHBOARD_HEALTH_URL", "http://127.0.0.1:8501/_stcore/health")
    )
    if status != 200 or body.strip().lower() != b"ok":
        raise RuntimeError(f"Streamlit health is not ready: HTTP {status}, body={body[:80]!r}")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"api", "dashboard"}:
        print("usage: healthcheck.py {api|dashboard}", file=sys.stderr)
        return 2

    try:
        _postgres_ready()
        _api_ready()
        if sys.argv[1] == "dashboard":
            _dashboard_ready()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
