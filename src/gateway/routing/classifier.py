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
    CHATGPT_PREFIX_PATTERNS = (
        r"^(?:frage|frag)\s+chatgpt[\s,:-]+(?P<question>.+)$",
    )
    PAPERLESS_PREFIX_PATTERNS = (
        r"^(?:frage|frag)\s+(?:paperless|secondbrain)[\s,:-]+(?P<question>.+)$",
        r"^suche\s+in\s+(?:paperless|secondbrain)\s+nach[\s,:-]+(?P<question>.+)$",
    )
    HOME_ASSISTANT_PREFIX_PATTERNS = (
        r"^(?:frage|frag)\s+(?:home\s*assistant|homeassistant)[\s,:-]+(?P<question>.+)$",
    )
    DOCKER_PREFIX_PATTERNS = (
        r"^(?:frage|frag)\s+docker[\s,:-]+(?P<question>.+)$",
    )
    LAST_MAIL_PATTERNS = (
        r"\blies\s+mir\s+(?:den\s+inhalt\s+)?(?:meiner\s+)?letzten\s+(?:e[\s-]?mail|mail)\s+vor\b",
        r"\blies\s+(?:mir\s+)?die\s+letzte\s+(?:e[\s-]?mail|mail)\s+vor\b",
        r"\bwas\s+steht\s+in\s+(?:meiner\s+)?letzten\s+(?:e[\s-]?mail|mail)\b",
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

        explicit_decision = self._match_explicit_route(question)
        if explicit_decision:
            return explicit_decision

        action_match = self._match_alias(normalized, self.action_aliases)
        if action_match and any(verb in normalized for verb in self.ACTION_VERBS):
            return RoutingDecision(
                route=RouteType.HOME_ASSISTANT_ACTION,
                confidence=1.0,
                reason=f"Matched Home Assistant action alias `{action_match.key}`.",
                matched_key=action_match.key,
                matched_rule="home_assistant_action_alias",
            )

        troubleshooting_match = self._match_troubleshooting(normalized)
        if troubleshooting_match:
            return RoutingDecision(
                route=RouteType.TROUBLESHOOTING,
                confidence=0.95,
                reason=f"Matched troubleshooting pattern `{troubleshooting_match.key}`.",
                matched_key=troubleshooting_match.key,
                matched_rule="troubleshooting_pattern",
            )

        if any(pattern in normalized for pattern in self.EXPLANATION_PATTERNS):
            return RoutingDecision(
                route=RouteType.SYSTEM_EXPLANATION,
                confidence=0.95,
                reason="Matched built-in system explanation pattern.",
                matched_rule="system_explanation_pattern",
            )

        docker_match = self._match_alias(normalized, self.docker_monitors)
        if docker_match or any(hint in normalized for hint in self.DOCKER_HINTS):
            return RoutingDecision(
                route=RouteType.DOCKER_STATUS,
                confidence=0.9 if docker_match else 0.75,
                reason="Matched Docker alias or status keyword.",
                matched_key=docker_match.key if docker_match else None,
                matched_rule="docker_alias_or_keyword",
            )

        home_assistant_match = self._match_alias(normalized, self.state_aliases)
        if home_assistant_match or any(hint in normalized for hint in self.HOME_ASSISTANT_HINTS):
            return RoutingDecision(
                route=RouteType.HOME_ASSISTANT_STATE,
                confidence=0.9 if home_assistant_match else 0.7,
                reason="Matched Home Assistant entity alias or live-state keyword.",
                matched_key=home_assistant_match.key if home_assistant_match else None,
                matched_rule="home_assistant_alias_or_keyword",
            )

        if "secondbrain" in normalized and re.search(r"\bwhat\b|\bhow\b", normalized):
            return RoutingDecision(
                route=RouteType.SYSTEM_EXPLANATION,
                confidence=0.8,
                reason="SecondBrain explanation keyword matched.",
                matched_rule="secondbrain_explanation_keyword",
            )

        if any(hint in normalized for hint in self.SECOND_BRAIN_HINTS):
            return RoutingDecision(
                route=RouteType.SECOND_BRAIN,
                confidence=0.8,
                reason="Matched SecondBrain document knowledge keywords.",
                matched_rule="secondbrain_keyword",
            )

        if self.ai_helper.enabled and any(hint in normalized for hint in self.GENERAL_AI_HINTS):
            return RoutingDecision(
                route=RouteType.GENERAL_AI,
                confidence=0.85,
                reason="Matched explicit general AI phrasing.",
                matched_rule="general_ai_keyword",
            )

        if any(hint in normalized for hint in self.TROUBLESHOOTING_HINTS) and any(
            term in normalized for term in ("secondbrain", "mail", "chat", "paperless")
        ):
            return RoutingDecision(
                route=RouteType.TROUBLESHOOTING,
                confidence=0.8,
                reason="Matched generic troubleshooting phrasing for a known system topic.",
                matched_rule="generic_troubleshooting_keyword",
            )

        ai_route = await self.ai_helper.classify_route(question)
        if ai_route:
            return RoutingDecision(
                route=ai_route,
                confidence=0.6,
                reason="AI fallback classified an otherwise ambiguous question.",
                matched_rule="ai_fallback_classification",
                used_ai_fallback=True,
            )

        return RoutingDecision(
            route=RouteType.SECOND_BRAIN,
            confidence=0.55,
            reason="Default fallback route is SecondBrain for general knowledge questions.",
            matched_rule="default_secondbrain_fallback",
        )

    def _match_explicit_route(self, question: str) -> RoutingDecision | None:
        """
        Why this exists: Alexa becomes far more predictable when users can force a backend with short spoken prefixes.
        What happens here: We strip one explicit prefix, then choose the matching backend without waiting for AI.
        Example input/output:
        - Input: "frage chatgpt wer war ada lovelace"
        - Output: route=GENERAL_AI, prepared_question="wer war ada lovelace"
        """

        stripped_chatgpt = self._strip_prefix(question, self.CHATGPT_PREFIX_PATTERNS)
        if stripped_chatgpt:
            return RoutingDecision(
                route=RouteType.GENERAL_AI,
                confidence=1.0,
                reason="Matched explicit ChatGPT prefix.",
                matched_rule="explicit_chatgpt_prefix",
                prepared_question=stripped_chatgpt,
            )

        stripped_paperless = self._strip_prefix(question, self.PAPERLESS_PREFIX_PATTERNS)
        if stripped_paperless:
            return RoutingDecision(
                route=RouteType.SECOND_BRAIN,
                confidence=1.0,
                reason="Matched explicit Paperless or SecondBrain prefix.",
                matched_rule="explicit_paperless_prefix",
                prepared_question=stripped_paperless,
            )

        stripped_home_assistant = self._strip_prefix(question, self.HOME_ASSISTANT_PREFIX_PATTERNS)
        if stripped_home_assistant:
            return self._route_explicit_home_assistant_question(stripped_home_assistant)

        stripped_docker = self._strip_prefix(question, self.DOCKER_PREFIX_PATTERNS)
        if stripped_docker:
            docker_match = self._match_alias(self._normalize(stripped_docker), self.docker_monitors)
            return RoutingDecision(
                route=RouteType.DOCKER_STATUS,
                confidence=1.0 if docker_match else 0.9,
                reason="Matched explicit Docker prefix.",
                matched_key=docker_match.key if docker_match else None,
                matched_rule="explicit_docker_prefix",
                prepared_question=stripped_docker,
            )

        if self._matches_any_pattern(question, self.LAST_MAIL_PATTERNS):
            return RoutingDecision(
                route=RouteType.SECOND_BRAIN,
                confidence=1.0,
                reason="Matched explicit latest-mail readout phrase.",
                matched_rule="explicit_last_mail_readout",
                prepared_question=(
                    "Lies mir den Inhalt meiner letzten E-Mail aus dem Archiv kurz vor "
                    "und nenne Absender, Datum und die wichtigste Aussage."
                ),
            )

        return None

    def _route_explicit_home_assistant_question(self, question: str) -> RoutingDecision:
        """Route explicit Home Assistant prefixes either to an allowlisted action or a live-state lookup."""
        normalized = self._normalize(question)
        action_match = self._match_alias(normalized, self.action_aliases)
        if action_match or any(verb in normalized for verb in self.ACTION_VERBS):
            return RoutingDecision(
                route=RouteType.HOME_ASSISTANT_ACTION,
                confidence=1.0 if action_match else 0.85,
                reason="Matched explicit Home Assistant prefix for an action-style request.",
                matched_key=action_match.key if action_match else None,
                matched_rule="explicit_home_assistant_prefix",
                prepared_question=question,
            )

        state_match = self._match_alias(normalized, self.state_aliases)
        return RoutingDecision(
            route=RouteType.HOME_ASSISTANT_STATE,
            confidence=1.0 if state_match else 0.85,
            reason="Matched explicit Home Assistant prefix for a state lookup.",
            matched_key=state_match.key if state_match else None,
            matched_rule="explicit_home_assistant_prefix",
            prepared_question=question,
        )

    @staticmethod
    def _normalize(question: str) -> str:
        return re.sub(r"\s+", " ", question.strip().lower())

    @staticmethod
    def _strip_prefix(question: str, patterns: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            match = re.match(pattern, question.strip(), flags=re.IGNORECASE)
            if not match:
                continue
            stripped = match.group("question").strip(" \t\n\r:;,.!?")
            return re.sub(r"\s+", " ", stripped).strip() or None
        return None

    @staticmethod
    def _matches_any_pattern(question: str, patterns: tuple[str, ...]) -> bool:
        normalized = question.strip()
        return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)

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
