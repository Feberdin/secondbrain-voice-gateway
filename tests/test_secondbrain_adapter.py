"""
Purpose: Verify SecondBrain adapter normalization and common API error handling.
Input/Output: Tests use mocked HTTP responses instead of a live SecondBrain instance.
Invariants: Useful answers stay concise, while auth and timeout problems remain explicit.
Debugging: If these tests fail, inspect the upstream response shape and adapter normalization rules.
"""

from __future__ import annotations

import json

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


@pytest.mark.asyncio
async def test_secondbrain_adapter_summarizes_contract_results(patch_async_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "contracts": [
                    {
                        "counterparty": "ERGO Versicherung AG",
                        "end_date": "2024-10-23",
                        "status": "expired",
                    },
                    {
                        "counterparty": "VGH Versicherungen",
                        "end_date": "2023-10-12",
                        "status": "expired",
                    },
                ],
                "citations": [
                    {
                        "document_title": "ERGO Hausratversicherung Unterlagen",
                        "url": "https://paperless.example/documents/1930/details",
                    }
                ],
            },
        )

    patch_async_client(secondbrain_module, handler)
    adapter = SecondBrainAdapter(Settings(_env_file=None))

    result = await adapter.ask("which contracts expire in the next 30 days")

    assert result.status == ResultStatus.OK
    assert "ERGO Versicherung AG" in result.answer
    assert "2024-10-23" in result.answer
    assert result.evidence[0].title == "ERGO Hausratversicherung Unterlagen"


@pytest.mark.asyncio
async def test_secondbrain_adapter_retries_with_hinted_query_field(patch_async_client) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append(payload)
        if "question" in payload:
            return httpx.Response(
                status_code=422,
                json={
                    "detail": [
                        {
                            "type": "missing",
                            "loc": ["body", "query"],
                            "msg": "Field required",
                            "input": {"question": "which contracts expire soon"},
                        }
                    ]
                },
            )
        return httpx.Response(
            status_code=200,
            json={"answer": "One contract expires soon."},
        )

    patch_async_client(secondbrain_module, handler)
    adapter = SecondBrainAdapter(Settings(_env_file=None, secondbrain_query_field_name="question"))

    result = await adapter.ask("which contracts expire soon")

    assert result.status == ResultStatus.OK
    assert result.answer == "One contract expires soon."
    assert len(calls) == 2
    assert calls[0] == {"question": "which contracts expire soon"}
    assert calls[1] == {"query": "which contracts expire soon"}
