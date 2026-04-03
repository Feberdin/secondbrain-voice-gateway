"""
Purpose: Keep short-lived Alexa follow-up state on the server as a fallback to session attributes.
Input/Output: Route handlers store and load small JSON-serializable dictionaries keyed by Alexa session ID.
Invariants: Only short-lived follow-up state is stored, expired entries are purged automatically, and missing state is safe.
Debugging: If `ja/nein` follow-ups seem flaky, inspect whether the same Alexa `sessionId` reaches the gateway and whether the stored state is refreshed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any


class AlexaSessionStateStore:
    """
    Purpose: Provide a tiny in-memory safety net for Alexa follow-ups like continuation and feedback.
    Input/Output: `set()` stores one state dictionary, `get()` returns a copy, and `clear()` removes it.
    Invariants: State is short-lived, scoped to one Alexa session ID, and never written to disk here.
    Debugging: If follow-ups fail after a container restart, remember that this in-memory store is intentionally transient.
    """

    SESSION_TTL_SECONDS = 30 * 60

    def __init__(self) -> None:
        self._entries: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str | None) -> dict[str, Any]:
        """Return one stored state copy or an empty dictionary when nothing valid is left."""
        if not session_id:
            return {}

        async with self._lock:
            self._purge_expired_locked()
            entry = self._entries.get(session_id)
            if not entry:
                return {}
            return dict(entry[1])

    async def set(self, session_id: str | None, state: dict[str, Any]) -> None:
        """Store one normalized follow-up state for the given Alexa session."""
        if not session_id:
            return

        normalized_state = dict(state)
        async with self._lock:
            self._purge_expired_locked()
            if not normalized_state:
                self._entries.pop(session_id, None)
                return

            expires_at = datetime.now(UTC) + timedelta(seconds=self.SESSION_TTL_SECONDS)
            self._entries[session_id] = (expires_at, normalized_state)

    async def clear(self, session_id: str | None) -> None:
        """Remove any stored follow-up state for one Alexa session."""
        if not session_id:
            return

        async with self._lock:
            self._entries.pop(session_id, None)

    def _purge_expired_locked(self) -> None:
        """Remove stale session entries before reading or writing new ones."""
        now = datetime.now(UTC)
        expired_session_ids = [
            session_id
            for session_id, (expires_at, _state) in self._entries.items()
            if expires_at <= now
        ]
        for session_id in expired_session_ids:
            self._entries.pop(session_id, None)
