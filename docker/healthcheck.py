"""Container readiness probes for the Compose application stack."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any


def _read_url(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=4) as response:
        return response.status, response.read()


def _api_ready() -> None:
    status, body = _read_url(os.environ.get("RCM_API_READINESS_URL", "http://127.0.0.1:8000/ready"))
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
        os.environ.get("RCM_DASHBOARD_READINESS_URL", "http://127.0.0.1:8502/ready")
    )
    payload: dict[str, Any] = json.loads(body)
    if status != 200 or payload.get("status") != "ready":
        raise RuntimeError(
            f"Dashboard is not ready: HTTP {status}, status={payload.get('status')!r}"
        )


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"api", "dashboard"}:
        print("usage: healthcheck.py {api|dashboard}", file=sys.stderr)
        return 2

    try:
        if sys.argv[1] == "api":
            _api_ready()
        else:
            _dashboard_ready()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
