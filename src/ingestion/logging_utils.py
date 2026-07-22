"""Structured (JSON-line) logging for ingestion scripts.

Emitting one JSON object per log record keeps download runs greppable and
machine-parseable, which matters for reproducibility and for the
reconciliation reports the data-contract tests consume.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """Format each record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any structured fields attached via `extra={"context": {...}}`.
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes JSON lines to stderr exactly once."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonLineFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, msg: str, **context: Any) -> None:
    """Log an INFO event with structured context fields."""
    logger.info(msg, extra={"context": context})
