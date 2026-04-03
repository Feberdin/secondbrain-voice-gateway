"""
Purpose: Coordinate routing, adapter calls, troubleshooting logic, and speech composition.
Input/Output: Accepts one user question and returns a full `VoiceQueryResult`.
Invariants: Each answer has exactly one chosen route and one normalized source of truth.
Debugging: Inspect the returned routing decision first when an otherwise healthy adapter seems wrong.
"""

from __future__ import annotations

from typing import Any

from gateway.config import Settings
from gateway.models.domain import HealthReport, VoiceQueryResult
from gateway.routing.classifier import QuestionRouter
from gateway.services.response_composer import ResponseComposer
from gateway.services.troubleshooting import TroubleshootingService


class VoiceGatewayOrchestrator:
    """
    Purpose: Keep API handlers thin by centralizing all high-level control flow here.
    Input/Output: Uses the router to pick one backend path, then composes the final voice response.
    Invariants: The same orchestration path serves both Alexa and internal debug API calls.
    Debugging: Run `/api/v1/query` or tests around this class when adding a new adapter or route.
    """

    def __init__(
        self,
        settings: Settings,
        router: QuestionRouter,
        secondbrain_adapter: Any,
        home_assistant_adapter: Any,
        docker_adapter: Any,
        troubleshooting_service: TroubleshootingService,
        response_composer: ResponseComposer,
    ) -> None:
        self.settings = settings
        self.router = router
        self.secondbrain_adapter = secondbrain_adapter
        self.home_assistant_adapter = home_assistant_adapter
        self.docker_adapter = docker_adapter
        self.troubleshooting_service = troubleshooting_service
        self.response_composer = response_composer

    async def handle_question(self, question: str) -> VoiceQueryResult:
        decision = await self.router.route(question)

        if decision.route.value == "secondbrain_query":
            result = await self.secondbrain_adapter.ask(question)
        elif decision.route.value == "home_assistant_state":
            result = await self.home_assistant_adapter.answer_state_question(question, decision.matched_key)
        elif decision.route.value == "home_assistant_action":
            result = await self.home_assistant_adapter.execute_action(question, decision.matched_key)
        elif decision.route.value == "docker_status":
            result = await self.docker_adapter.answer_status_question(question, decision.matched_key)
        elif decision.route.value == "troubleshooting":
            result = await self.troubleshooting_service.answer(question, decision.matched_key)
        else:
            result = self.troubleshooting_service.explain_system()

        spoken_text, reprompt_text = await self.response_composer.compose(result)
        return VoiceQueryResult(
            question=question,
            routing=decision,
            result=result,
            spoken_text=spoken_text,
            reprompt_text=reprompt_text,
        )

    async def readiness(self) -> list[HealthReport]:
        """Collect readiness information from each configured integration."""
        reports = [
            await self.secondbrain_adapter.health_check(),
            await self.home_assistant_adapter.health_check(),
            await self.docker_adapter.health_check(),
        ]
        return reports

    def debug_snapshot(self) -> dict[str, Any]:
        """Return a safe summary for debug endpoints without exposing tokens."""
        return {
            "settings": self.settings.safe_debug_snapshot(),
            "home_assistant_entities": [alias.model_dump() for alias in self.home_assistant_adapter.state_aliases()],
            "home_assistant_actions": [alias.model_dump() for alias in self.home_assistant_adapter.action_aliases()],
            "docker_monitors": [monitor.model_dump() for monitor in self.docker_adapter.monitors()],
            "troubleshooting_entries": [entry.model_dump() for entry in self.troubleshooting_service.entries()],
        }

