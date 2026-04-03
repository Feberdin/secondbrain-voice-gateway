"""
Purpose: Store request-scoped correlation IDs for logs and debugging.
Input/Output: Middleware sets the request ID; log formatters and services read it later.
Invariants: Every request can be traced across API, router, and adapter logs.
Debugging: Inspect the `request_id` field in JSON logs and propagate it to operator reports.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request ID or a fallback placeholder."""
    return _request_id_var.get()


def set_request_id(request_id: str | None = None) -> str:
    """Set and return a request ID for the current request context."""
    resolved = request_id or str(uuid4())
    _request_id_var.set(resolved)
    return resolved

