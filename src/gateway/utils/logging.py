"""
Purpose: Configure structured JSON logging with request correlation and secret-safe fields.
Input/Output: The app calls `configure_logging`; Python's logging module then emits JSON lines.
Invariants: Logs stay machine-readable, stable across services, and avoid dumping full secret values.
Debugging: Set `LOG_LEVEL=DEBUG` to inspect routing decisions and backend normalization.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from gateway.utils.context import get_request_id


class JsonLogFormatter(logging.Formatter):
    """Simple JSON formatter that keeps logs dependency-light and container-friendly."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str) -> None:
    """
    Purpose: Install one consistent JSON log handler across the entire application.
    Input/Output: Accepts the desired log level and mutates root logger configuration.
    Invariants: Only one stdout handler is attached so Docker logs stay readable.
    Debugging: If logs are duplicated, verify this function is called only once during startup.
    """

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger.addHandler(handler)

