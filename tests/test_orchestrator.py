"""
Purpose: Verify that the orchestrator executes the correct backend path for new high-level routes.
Input/Output: Tests use tiny in-memory fakes instead of real adapters or network calls.
Invariants: A `GENERAL_AI` routing decision must call the AI helper, not SecondBrain.
Debugging: If these tests fail, inspect the route-to-adapter mapping inside `orchestrator.py`.
"""

from __future__ import annotations

import pytest

from gateway.config import Settings
from gateway.models.domain import ResultStatus, RouteType, RoutingDecision, SourceType, StructuredAnswer
from gateway.services.orchestrator import VoiceGatewayOrchestrator
from gateway.services.response_composer import ComposedSpeech


class FakeAiHelper:
    """
    Purpose: Make the orchestrator test independent from HTTP and OpenAI payload details.
    Input/Output: Records incoming questions and returns one fixed structured answer.
    Invariants: The test can assert that this helper was actually used.
    Debugging: Check `questions` if the orchestrator seems to drop or rewrite the user query.
    """

    def __init__(self) -> None:
        self.questions: list[str] = []

    @property
    def enabled(self) -> bool:
        return True

    async def answer_general_question(self, question: str) -> StructuredAnswer:
        self.questions.append(question)
        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.GENERAL_AI,
            answer="Das ist eine allgemeine KI-Antwort.",
        )


class FakeRouter:
    """Return one preconfigured routing decision so the orchestrator path stays easy to verify."""

    def __init__(self, ai_helper: FakeAiHelper) -> None:
        self.ai_helper = ai_helper

    async def route(self, question: str) -> RoutingDecision:
        return RoutingDecision(route=RouteType.GENERAL_AI, reason="test-general-ai")


class FakeResponseComposer:
    """Mirror the real composer contract without pulling in unrelated formatting behavior."""

    async def compose(self, result: StructuredAnswer) -> ComposedSpeech:
        return ComposedSpeech(
            spoken_text=result.answer,
            reprompt_text="Du kannst direkt weitermachen.",
            continuation_chunks=[],
        )


class UnexpectedCall:
    """Raise loudly if the orchestrator accidentally calls the wrong adapter path."""

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected adapter call: {name}")


@pytest.mark.asyncio
async def test_orchestrator_routes_general_questions_to_ai_helper() -> None:
    ai_helper = FakeAiHelper()
    orchestrator = VoiceGatewayOrchestrator(
        settings=Settings(_env_file=None),
        router=FakeRouter(ai_helper),
        secondbrain_adapter=UnexpectedCall(),
        home_assistant_adapter=UnexpectedCall(),
        docker_adapter=UnexpectedCall(),
        troubleshooting_service=UnexpectedCall(),
        response_composer=FakeResponseComposer(),
    )

    result = await orchestrator.handle_question("Wer war Ada Lovelace?")

    assert ai_helper.questions == ["Wer war Ada Lovelace?"]
    assert result.routing.route == RouteType.GENERAL_AI
    assert result.result.source == SourceType.GENERAL_AI
    assert result.spoken_text == "Das ist eine allgemeine KI-Antwort."
