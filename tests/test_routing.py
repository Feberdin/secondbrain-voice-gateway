"""
Purpose: Verify the deterministic question router for the main supported question classes.
Input/Output: Tests feed sample spoken questions into the router and inspect the chosen route.
Invariants: Known aliases and troubleshooting phrases should map consistently and explainably.
Debugging: If these tests fail, review alias YAML files and the rule order in `classifier.py`.
"""

from __future__ import annotations

import pytest

from gateway.config import Settings, load_docker_monitor_config, load_home_assistant_alias_config, load_troubleshooting_config
from gateway.models.domain import RouteType
from gateway.routing.classifier import QuestionRouter
from gateway.services.ai_helper import OptionalAiHelper


class StubAiHelper:
    """
    Purpose: Keep router tests deterministic without real network calls.
    Input/Output: Returns a fixed route or `None` and exposes whether AI mode should appear enabled.
    Invariants: The router under test stays focused on rule order rather than on OpenAI HTTP behavior.
    Debugging: Set `route_to_return` to inspect how the router reacts to AI fallback choices.
    """

    def __init__(self, *, enabled: bool, route_to_return: RouteType | None = None) -> None:
        self._enabled = enabled
        self.route_to_return = route_to_return

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def classify_route(self, question: str) -> RouteType | None:
        return self.route_to_return


def build_router(ai_helper: OptionalAiHelper | StubAiHelper | None = None) -> QuestionRouter:
    settings = Settings(_env_file=None)
    ha_config = load_home_assistant_alias_config(settings)
    docker_config = load_docker_monitor_config(settings)
    troubleshooting = load_troubleshooting_config(settings)
    return QuestionRouter(
        state_aliases=ha_config.entities,
        action_aliases=ha_config.actions,
        docker_monitors=docker_config.containers,
        troubleshooting_entries=troubleshooting.entries,
        ai_helper=ai_helper or OptionalAiHelper(settings),
    )


@pytest.mark.asyncio
async def test_router_matches_home_assistant_state() -> None:
    decision = await build_router().route("how full my EcoFlow batteries are")
    assert decision.route == RouteType.HOME_ASSISTANT_STATE
    assert decision.matched_key == "ecoflow_battery_soc"


@pytest.mark.asyncio
async def test_router_matches_home_assistant_action() -> None:
    decision = await build_router().route("turn on EV charging")
    assert decision.route == RouteType.HOME_ASSISTANT_ACTION
    assert decision.matched_key == "ev_charging_on"


@pytest.mark.asyncio
async def test_router_matches_docker_status() -> None:
    decision = await build_router().route("is Jellyfin running")
    assert decision.route == RouteType.DOCKER_STATUS
    assert decision.matched_key == "jellyfin"


@pytest.mark.asyncio
async def test_router_matches_troubleshooting() -> None:
    decision = await build_router().route("how do I debug SecondBrain")
    assert decision.route == RouteType.TROUBLESHOOTING
    assert decision.matched_key == "secondbrain_debug"


@pytest.mark.asyncio
async def test_router_matches_system_explanation() -> None:
    decision = await build_router().route("what is SecondBrain")
    assert decision.route == RouteType.SYSTEM_EXPLANATION


@pytest.mark.asyncio
async def test_router_falls_back_to_secondbrain_for_contracts() -> None:
    decision = await build_router().route("welche vertraege enden in den naechsten dreissig tagen")
    assert decision.route == RouteType.SECOND_BRAIN


@pytest.mark.asyncio
async def test_router_uses_general_ai_for_explicit_general_question_patterns_when_ai_is_enabled() -> None:
    decision = await build_router(StubAiHelper(enabled=True, route_to_return=None)).route("wer war ada lovelace")
    assert decision.route == RouteType.GENERAL_AI


@pytest.mark.asyncio
async def test_router_matches_explicit_chatgpt_phrase() -> None:
    decision = await build_router(StubAiHelper(enabled=True, route_to_return=None)).route("frage chatgpt wer marie curie war")
    assert decision.route == RouteType.GENERAL_AI
    assert decision.matched_rule == "explicit_chatgpt_prefix"
    assert decision.prepared_question == "wer marie curie war"


@pytest.mark.asyncio
async def test_router_matches_explicit_paperless_phrase() -> None:
    decision = await build_router().route("frage paperless welche vertraege enden bald")
    assert decision.route == RouteType.SECOND_BRAIN
    assert decision.matched_rule == "explicit_paperless_prefix"
    assert decision.prepared_question == "welche vertraege enden bald"


@pytest.mark.asyncio
async def test_router_matches_explicit_home_assistant_phrase() -> None:
    decision = await build_router().route("frage home assistant wie voll sind meine ecoflow batterien")
    assert decision.route == RouteType.HOME_ASSISTANT_STATE
    assert decision.matched_rule == "explicit_home_assistant_prefix"
    assert decision.prepared_question == "wie voll sind meine ecoflow batterien"


@pytest.mark.asyncio
async def test_router_matches_explicit_last_mail_phrase() -> None:
    decision = await build_router().route("lies mir den inhalt meiner letzten mail vor")
    assert decision.route == RouteType.SECOND_BRAIN
    assert decision.matched_rule == "explicit_last_mail_readout"
    assert "letzten E-Mail" in decision.prepared_question


@pytest.mark.asyncio
async def test_router_keeps_docker_questions_on_docker_route_even_with_ai_enabled() -> None:
    decision = await build_router(StubAiHelper(enabled=True, route_to_return=None)).route("warum laeuft jellyfin nicht")
    assert decision.route == RouteType.DOCKER_STATUS


@pytest.mark.asyncio
async def test_router_uses_ai_classification_for_neutral_general_question() -> None:
    decision = await build_router(StubAiHelper(enabled=True, route_to_return=RouteType.GENERAL_AI)).route(
        "nenn mir drei gruende fuer regenboegen"
    )
    assert decision.route == RouteType.GENERAL_AI
    assert decision.used_ai_fallback is True
