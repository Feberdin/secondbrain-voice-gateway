"""
Purpose: Verify SecondBrain adapter normalization and common API error handling.
Input/Output: Tests use mocked HTTP responses instead of a live SecondBrain instance.
Invariants: Useful answers stay concise, while auth and timeout problems remain explicit.
Debugging: If these tests fail, inspect the upstream response shape and adapter normalization rules.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.adapters import secondbrain as secondbrain_module
from gateway.adapters.secondbrain import SecondBrainAdapter
from gateway.config import Settings
from gateway.models.domain import ResultStatus


@pytest.mark.asyncio
async def test_secondbrain_adapter_normalizes_answer(patch_async_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            status_code=200,
            json={
                "answer": "Two contracts expire in the next thirty days.",
                "sources": [{"title": "Lease A", "snippet": "Expires on 2026-05-01"}],
            },
        )

    patch_async_client(secondbrain_module, handler)
    settings = Settings(_env_file=None, secondbrain_bearer_token="test-token")
    adapter = SecondBrainAdapter(settings)

    result = await adapter.ask("which contracts expire in the next 30 days")

    assert result.status == ResultStatus.OK
    assert result.answer == "Two contracts expire in the next thirty days."
    assert result.evidence[0].title == "Lease A"


@pytest.mark.asyncio
async def test_secondbrain_adapter_handles_auth_error(patch_async_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, json={"detail": "unauthorized"})

    patch_async_client(secondbrain_module, handler)
    adapter = SecondBrainAdapter(Settings(_env_file=None))

    result = await adapter.ask("what is secondbrain")

    assert result.status == ResultStatus.ERROR
    assert "rejected" in result.answer.lower()

