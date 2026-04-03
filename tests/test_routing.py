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


def build_router() -> QuestionRouter:
    settings = Settings(_env_file=None)
    ha_config = load_home_assistant_alias_config(settings)
    docker_config = load_docker_monitor_config(settings)
    troubleshooting = load_troubleshooting_config(settings)
    return QuestionRouter(
        state_aliases=ha_config.entities,
        action_aliases=ha_config.actions,
        docker_monitors=docker_config.containers,
        troubleshooting_entries=troubleshooting.entries,
        ai_helper=OptionalAiHelper(settings),
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
    decision = await build_router().route("which contracts expire in the next 30 days")
    assert decision.route == RouteType.SECOND_BRAIN

