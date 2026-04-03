"""
Purpose: Deterministically classify incoming voice questions before any backend call is made.
Input/Output: Accepts free-form text and returns a `RoutingDecision`.
Invariants: Deterministic rules run first; optional AI fallback only handles ambiguous leftovers.
Debugging: Enable DEBUG logs and inspect the `reason` field to understand why a route was chosen.
"""

from __future__ import annotations

import logging
import re

from gateway.models.domain import (
    DockerMonitorConfig,
    HomeAssistantActionAlias,
    HomeAssistantStateAlias,
    RouteType,
    RoutingDecision,
    TroubleshootingEntry,
)
from gateway.services.ai_helper import OptionalAiHelper

logger = logging.getLogger(__name__)


class QuestionRouter:
    """
    Purpose: Turn a raw spoken question into one internal route with a short audit reason.
    Input/Output: Uses configured aliases plus keyword rules; may consult optional AI fallback.
    Invariants: Known actions and monitored integrations always win over fuzzy classification.
    Debugging: Review alias YAML files first when a question routes somewhere unexpected.
    """

    ACTION_VERBS = (
        "turn on",
        "turn off",
        "start",
        "stop",
        "enable",
        "disable",
        "activate",
        "deactivate",
        "schalte",
        "mach an",
        "mach aus",
        "starte",
        "stoppe",
        "aktiviere",
        "deaktiviere",
    )
    TROUBLESHOOTING_HINTS = (
        "debug",
        "troubleshoot",
        "why",
        "not working",
        "failing",
        "reset",
        "connectivity",
        "test",
        "warum",
        "wieso",
        "weshalb",
        "fehler",
        "problem",
        "geht nicht",
        "funktioniert nicht",
        "laeuft nicht",
        "läuft nicht",
    )
    DOCKER_HINTS = (
        "docker",
        "container",
        "service",
        "running",
        "unhealthy",
        "restart",
        "logs",
        "dienst",
        "dienste",
        "status",
        "laeuft",
        "läuft",
        "neustart",
        "protokoll",
    )
    HOME_ASSISTANT_HINTS = (
        "battery",
        "solar",
        "power",
        "consumption",
        "sensor",
        "entity",
        "home assistant",
        "akku",
        "batterie",
        "batterien",
        "ladestand",
        "ladezustand",
        "speicher",
        "hausakku",
        "hausbatterie",
        "verbrauch",
        "leistung",
        "entitaet",
        "entität",
    )
    SECOND_BRAIN_HINTS = (
        "contract",
        "contracts",
        "document",
        "documents",
        "facts",
        "timeline",
        "paperless",
        "mail",
        "vertrag",
        "vertraege",
        "verträge",
        "dokument",
        "dokumente",
        "rechnung",
        "rechnungen",
        "angebot",
        "angebote",
        "e mail",
        "email",
        "posteingang",
        "archiv",
    )
    GENERAL_AI_HINTS = (
        "chatgpt",
        "allgemeine frage",
        "ohne secondbrain",
        "unabhaengig von secondbrain",
        "wer ist",
        "wer war",
        "was bedeutet",
        "erklaer mir",
        "erklär mir",
        "erzaehl mir",
        "erzähl mir",
        "wie funktioniert",
        "warum ist",
        "wieso ist",
        "wann war",
        "wann wurde",
        "wo ist",
        "wo liegt",
    )
    EXPLANATION_PATTERNS = (
        "what is secondbrain",
        "what secondbrain is",
        "how does secondbrain work",
        "what can this system do",
        "what can my system do",
        "was ist secondbrain",
        "wie funktioniert secondbrain",
        "was kannst du",
        "was kann dieses system",
        "was kann mein system",
    )

    def __init__(
        self,
        state_aliases: list[HomeAssistantStateAlias],
        action_aliases: list[HomeAssistantActionAlias],
        docker_monitors: list[DockerMonitorConfig],
        troubleshooting_entries: list[TroubleshootingEntry],
        ai_helper: OptionalAiHelper,
    ) -> None:
        self.state_aliases = state_aliases
        self.action_aliases = action_aliases
        self.docker_monitors = docker_monitors
        self.troubleshooting_entries = troubleshooting_entries
        self.ai_helper = ai_helper

    async def route(self, question: str) -> RoutingDecision:
        normalized = self._normalize(question)

        action_match = self._match_alias(normalized, self.action_aliases)
        if action_match and any(verb in normalized for verb in self.ACTION_VERBS):
            return RoutingDecision(
                route=RouteType.HOME_ASSISTANT_ACTION,
                confidence=1.0,
                reason=f"Matched Home Assistant action alias `{action_match.key}`.",
                matched_key=action_match.key,
            )

        troubleshooting_match = self._match_troubleshooting(normalized)
        if troubleshooting_match:
            return RoutingDecision(
                route=RouteType.TROUBLESHOOTING,
                confidence=0.95,
                reason=f"Matched troubleshooting pattern `{troubleshooting_match.key}`.",
                matched_key=troubleshooting_match.key,
            )

        if any(pattern in normalized for pattern in self.EXPLANATION_PATTERNS):
            return RoutingDecision(
                route=RouteType.SYSTEM_EXPLANATION,
                confidence=0.95,
                reason="Matched built-in system explanation pattern.",
            )

        docker_match = self._match_alias(normalized, self.docker_monitors)
        if docker_match or any(hint in normalized for hint in self.DOCKER_HINTS):
            return RoutingDecision(
                route=RouteType.DOCKER_STATUS,
                confidence=0.9 if docker_match else 0.75,
                reason="Matched Docker alias or status keyword.",
                matched_key=docker_match.key if docker_match else None,
            )

        home_assistant_match = self._match_alias(normalized, self.state_aliases)
        if home_assistant_match or any(hint in normalized for hint in self.HOME_ASSISTANT_HINTS):
            return RoutingDecision(
                route=RouteType.HOME_ASSISTANT_STATE,
                confidence=0.9 if home_assistant_match else 0.7,
                reason="Matched Home Assistant entity alias or live-state keyword.",
                matched_key=home_assistant_match.key if home_assistant_match else None,
            )

        if "secondbrain" in normalized and re.search(r"\bwhat\b|\bhow\b", normalized):
            return RoutingDecision(
                route=RouteType.SYSTEM_EXPLANATION,
                confidence=0.8,
                reason="SecondBrain explanation keyword matched.",
            )

        if any(hint in normalized for hint in self.SECOND_BRAIN_HINTS):
            return RoutingDecision(
                route=RouteType.SECOND_BRAIN,
                confidence=0.8,
                reason="Matched SecondBrain document knowledge keywords.",
            )

        if self.ai_helper.enabled and any(hint in normalized for hint in self.GENERAL_AI_HINTS):
            return RoutingDecision(
                route=RouteType.GENERAL_AI,
                confidence=0.85,
                reason="Matched explicit general AI phrasing.",
            )

        if any(hint in normalized for hint in self.TROUBLESHOOTING_HINTS) and any(
            term in normalized for term in ("secondbrain", "mail", "chat", "paperless")
        ):
            return RoutingDecision(
                route=RouteType.TROUBLESHOOTING,
                confidence=0.8,
                reason="Matched generic troubleshooting phrasing for a known system topic.",
            )

        ai_route = await self.ai_helper.classify_route(question)
        if ai_route:
            return RoutingDecision(
                route=ai_route,
                confidence=0.6,
                reason="AI fallback classified an otherwise ambiguous question.",
                used_ai_fallback=True,
            )

        return RoutingDecision(
            route=RouteType.SECOND_BRAIN,
            confidence=0.55,
            reason="Default fallback route is SecondBrain for general knowledge questions.",
        )

    @staticmethod
    def _normalize(question: str) -> str:
        return re.sub(r"\s+", " ", question.strip().lower())

    def _match_troubleshooting(self, normalized: str) -> TroubleshootingEntry | None:
        best: tuple[int, TroubleshootingEntry] | None = None
        for entry in self.troubleshooting_entries:
            for pattern in entry.patterns:
                phrase = pattern.lower()
                if phrase and phrase in normalized:
                    score = len(phrase)
                    if best is None or score > best[0]:
                        best = (score, entry)
        return best[1] if best else None

    @staticmethod
    def _match_alias(normalized: str, items: list[object]) -> object | None:
        best: tuple[int, object] | None = None
        for item in items:
            phrases = set()
            friendly_name = getattr(item, "friendly_name", None)
            if friendly_name:
                phrases.add(str(friendly_name).lower())
            container_name = getattr(item, "container_name", None)
            if container_name:
                phrases.add(str(container_name).lower())
            for alias in getattr(item, "aliases", []):
                phrases.add(str(alias).lower())

            for phrase in phrases:
                if phrase and phrase in normalized:
                    score = len(phrase)
                    if best is None or score > best[0]:
                        best = (score, item)
        return best[1] if best else None
