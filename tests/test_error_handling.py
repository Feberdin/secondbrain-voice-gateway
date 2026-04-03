"""
Purpose: Verify that unexpected failures become clear API errors instead of silent crashes.
Input/Output: Tests trigger one controlled exception through the REST debug endpoint.
Invariants: Operators should receive a stable 500 payload and a request ID for log correlation.
Debugging: If this behavior changes, inspect the global exception handler in `gateway.main`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.config import Settings
from gateway.main import create_app


def test_internal_query_returns_500_payload_on_unhandled_error() -> None:
    app = create_app(Settings(_env_file=None))

    async def broken_handler(question: str):
        raise RuntimeError("boom")

    app.state.orchestrator.handle_question = broken_handler
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/query", json={"question": "is Jellyfin running"})

    assert response.status_code == 500
    assert response.json()["error"] == "internal_server_error"

