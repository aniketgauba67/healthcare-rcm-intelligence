"""Run Streamlit with a small dependency-aware readiness endpoint."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Probe = Callable[[str], tuple[int, bytes]]


def _probe(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=4) as response:
        return response.status, response.read()


def evaluate_dashboard_readiness(probe: Probe = _probe) -> tuple[bool, dict[str, str]]:
    """Check the Streamlit process and the API's full dependency readiness."""
    dependencies: dict[str, str] = {}
    checks = {
        "streamlit": os.environ.get(
            "RCM_STREAMLIT_LIVENESS_URL", "http://127.0.0.1:8501/_stcore/health"
        ),
        "api": os.environ.get("RCM_API_READINESS_URL", "http://api:8000/ready"),
    }
    for name, url in checks.items():
        try:
            status, body = probe(url)
            if status != 200:
                dependencies[name] = f"unready (HTTP {status})"
            elif name == "streamlit" and body.strip().lower() != b"ok":
                dependencies[name] = "unready (unexpected liveness response)"
            else:
                dependencies[name] = "ready"
        except Exception as error:
            dependencies[name] = f"unready ({type(error).__name__})"
    return all(value == "ready" for value in dependencies.values()), dependencies


class ReadinessHandler(BaseHTTPRequestHandler):
    """Expose process liveness and dependency readiness without proxying the app."""

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Health clients can disconnect after reading the status line.
            return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/live":
            try:
                status, body = _probe(
                    os.environ.get(
                        "RCM_STREAMLIT_LIVENESS_URL",
                        "http://127.0.0.1:8501/_stcore/health",
                    )
                )
                live = status == 200 and body.strip().lower() == b"ok"
            except Exception:
                live = False
            self._write(200 if live else 503, {"status": "live" if live else "not_live"})
            return
        if self.path == "/ready":
            ready, dependencies = evaluate_dashboard_readiness()
            self._write(
                200 if ready else 503,
                {"status": "ready" if ready else "not_ready", "dependencies": dependencies},
            )
            return
        self._write(404, {"status": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: dashboard_server.py <streamlit command...>", file=sys.stderr)
        return 2

    port = int(os.environ.get("RCM_DASHBOARD_READINESS_PORT", "8502"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ReadinessHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    process = subprocess.Popen(sys.argv[1:])

    def stop(signum: int, frame: object) -> None:  # noqa: ARG001
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    thread.start()
    try:
        return process.wait()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
