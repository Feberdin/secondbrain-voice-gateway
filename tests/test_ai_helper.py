"""
Purpose: Verify optional AI helper behavior for routing, general answers, and malformed payload handling.
Input/Output: Tests replace outbound OpenAI HTTP calls with deterministic mock transports.
Invariants: Invalid AI payloads must degrade gracefully instead of crashing the gateway.
Debugging: If these tests fail, inspect the JSON prompt contract in `ai_helper.py` first.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gateway.config import Settings
from gateway.models.domain import ResultStatus, RouteType, SourceType
from gateway.services import ai_helper as ai_helper_module
from gateway.services.ai_helper import OptionalAiHelper


def build_enabled_settings() -> Settings:
    """Return a minimal AI-enabled settings object for tests that mock the HTTP layer."""
    return Settings(
        _env_file=None,
        ai_enabled=True,
        ai_base_url="https://api.openai.example/v1",
        ai_api_key="test-key",
        ai_model="gpt-4o-mini",
    )


@pytest.mark.asyncio
async def test_classify_route_accepts_general_ai(patch_async_client) -> None:
    helper = OptionalAiHelper(build_enabled_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"route": "general_ai"})
                        }
                    }
                ]
            },
        )

    patch_async_client(ai_helper_module, handler)
    route = await helper.classify_route("Wer war Ada Lovelace?")
    assert route == RouteType.GENERAL_AI


@pytest.mark.asyncio
async def test_answer_general_question_returns_structured_answer(patch_async_client) -> None:
    helper = OptionalAiHelper(build_enabled_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "Ada Lovelace war eine britische Mathematikerin und gilt als fruehe Pionierin des Programmierens.",
                                    "uncertainty": None,
                                    "next_step": "Wenn du magst, erklaere ich auch ihren Bezug zu Charles Babbage.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    patch_async_client(ai_helper_module, handler)
    answer = await helper.answer_general_question("Wer war Ada Lovelace?")

    assert answer.status == ResultStatus.OK
    assert answer.source == SourceType.GENERAL_AI
    assert "Ada Lovelace" in answer.answer
    assert answer.next_step == "Wenn du magst, erklaere ich auch ihren Bezug zu Charles Babbage."


@pytest.mark.asyncio
async def test_answer_general_question_handles_invalid_payload(patch_async_client) -> None:
    helper = OptionalAiHelper(build_enabled_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"summary": "missing answer field"})
                        }
                    }
                ]
            },
        )

    patch_async_client(ai_helper_module, handler)
    answer = await helper.answer_general_question("Wer war Ada Lovelace?")

    assert answer.status == ResultStatus.UNCERTAIN
    assert answer.source == SourceType.GENERAL_AI
    assert "keine verlaessliche allgemeine Antwort" in answer.answer
