"""
Purpose: Read live Home Assistant entity state and execute a small allowlisted set of safe service calls.
Input/Output: Uses the Home Assistant REST API and returns normalized speech-friendly answers.
Invariants: Only configured aliases and actions are allowed; arbitrary service execution is never exposed.
Debugging: Check the long-lived token, alias YAML file, and Home Assistant API reachability when requests fail.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from gateway.config import Settings
from gateway.models.domain import (
    HealthReport,
    HomeAssistantActionAlias,
    HomeAssistantAliasConfig,
    HomeAssistantStateAlias,
    ResultStatus,
    SourceType,
    StructuredAnswer,
)

logger = logging.getLogger(__name__)


class HomeAssistantAdapter:
    """
    Purpose: Isolate Home Assistant-specific HTTP behavior and state formatting.
    Input/Output: Accepts a natural language question or an allowlisted action key.
    Invariants: Entity aliases and action aliases are the only routing surface accepted from voice input.
    Debugging: Use the internal REST endpoint or Home Assistant Developer Tools to compare entity states.
    """

    def __init__(self, settings: Settings, alias_config: HomeAssistantAliasConfig) -> None:
        self.settings = settings
        self.alias_config = alias_config

    async def health_check(self) -> HealthReport:
        """Perform a lightweight Home Assistant API readiness check."""
        if not self.settings.home_assistant_enabled:
            return HealthReport(
                component="home_assistant",
                ok=True,
                detail="Home Assistant integration is disabled by configuration.",
                source=SourceType.HOME_ASSISTANT,
            )

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(
                    f"{self.settings.home_assistant_base_url.rstrip('/')}/api/",
                    headers=self._headers(),
                )
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - readiness needs one clear status.
            return HealthReport(
                component="home_assistant",
                ok=False,
                detail=f"Health check failed: {exc}",
                source=SourceType.HOME_ASSISTANT,
            )

        return HealthReport(
            component="home_assistant",
            ok=True,
            detail="Home Assistant API responded successfully.",
            source=SourceType.HOME_ASSISTANT,
        )

    async def answer_state_question(self, question: str, matched_key: str | None = None) -> StructuredAnswer:
        """Resolve an entity alias and read its current state from Home Assistant."""
        if not self.settings.home_assistant_enabled:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer="Home Assistant integration is disabled.",
                next_step="Enable Home Assistant settings in the gateway configuration.",
            )

        alias = self._find_state_alias(question, matched_key)
        if not alias:
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.HOME_ASSISTANT,
                answer="I am not sure which Home Assistant entity you mean.",
                next_step="Add an alias in `configs/home_assistant_aliases.yml` for that device or sensor.",
            )

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(
                    f"{self.settings.home_assistant_base_url.rstrip('/')}/api/states/{alias.entity_id}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer=f"I could not read {alias.friendly_name}.",
                next_step="Check that the entity ID exists and that the Home Assistant token is valid.",
                raw={"status_code": exc.response.status_code, "entity_id": alias.entity_id},
            )
        except httpx.TimeoutException:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer=f"Home Assistant did not answer in time for {alias.friendly_name}.",
                next_step="Check Home Assistant load, token validity, and network latency.",
                raw={"entity_id": alias.entity_id},
            )
        except httpx.HTTPError as exc:
            logger.exception("Home Assistant state request failed.")
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer="I could not reach Home Assistant.",
                next_step="Check the base URL, reverse proxy, and container networking.",
                raw={"error": str(exc), "entity_id": alias.entity_id},
            )

        return self._normalize_state(alias, payload)

    async def execute_action(self, question: str, matched_key: str | None = None) -> StructuredAnswer:
        """Run one explicitly allowlisted Home Assistant service call."""
        if not self.settings.home_assistant_enabled:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer="Home Assistant integration is disabled.",
                next_step="Enable Home Assistant settings in the gateway configuration.",
            )

        action = self._find_action_alias(question, matched_key)
        if not action:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer="That Home Assistant action is not allowed.",
                next_step="Add the action to `configs/home_assistant_aliases.yml` if it is safe to expose by voice.",
            )

        url = (
            f"{self.settings.home_assistant_base_url.rstrip('/')}/api/services/"
            f"{action.domain}/{action.service}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.post(url, headers=self._headers(), json=action.service_data)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer=f"I could not complete {action.friendly_name}.",
                next_step="Check the service domain, entity ID, and Home Assistant permissions.",
                raw={"status_code": exc.response.status_code, "action_key": action.key},
            )
        except httpx.HTTPError as exc:
            logger.exception("Home Assistant service call failed.")
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.HOME_ASSISTANT,
                answer=f"I could not reach Home Assistant to run {action.friendly_name}.",
                next_step="Check the base URL, token, and network path to Home Assistant.",
                raw={"error": str(exc), "action_key": action.key},
            )

        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.HOME_ASSISTANT,
            answer=action.confirmation_speech,
            details=f"Home Assistant accepted action {action.domain}.{action.service}.",
            next_step=action.safety_note,
            raw={"action": action.model_dump(), "service_response": payload},
        )

    def state_aliases(self) -> list[HomeAssistantStateAlias]:
        return self.alias_config.entities

    def action_aliases(self) -> list[HomeAssistantActionAlias]:
        return self.alias_config.actions

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.settings.home_assistant_token:
            headers["Authorization"] = f"Bearer {self.settings.home_assistant_token}"
        return headers

    def _find_state_alias(self, question: str, matched_key: str | None = None) -> HomeAssistantStateAlias | None:
        if matched_key:
            for alias in self.alias_config.entities:
                if alias.key == matched_key:
                    return alias

        normalized = question.lower()
        best_match: tuple[int, HomeAssistantStateAlias] | None = None
        for alias in self.alias_config.entities:
            for phrase in {alias.friendly_name.lower(), *[item.lower() for item in alias.aliases]}:
                if phrase and phrase in normalized:
                    score = len(phrase)
                    if best_match is None or score > best_match[0]:
                        best_match = (score, alias)
        return best_match[1] if best_match else None

    def _find_action_alias(self, question: str, matched_key: str | None = None) -> HomeAssistantActionAlias | None:
        if matched_key:
            for alias in self.alias_config.actions:
                if alias.key == matched_key:
                    return alias

        normalized = question.lower()
        best_match: tuple[int, HomeAssistantActionAlias] | None = None
        for alias in self.alias_config.actions:
            for phrase in {alias.friendly_name.lower(), *[item.lower() for item in alias.aliases]}:
                if phrase and phrase in normalized:
                    score = len(phrase)
                    if best_match is None or score > best_match[0]:
                        best_match = (score, alias)
        return best_match[1] if best_match else None

    def _normalize_state(self, alias: HomeAssistantStateAlias, payload: dict[str, Any]) -> StructuredAnswer:
        """
        Why this exists: Alexa needs concise speech, while Home Assistant returns generic raw state payloads.
        What happens here: We map raw states, format units, and keep the raw payload for debugging.
        Example input/output:
        - Input: {"state": "78", "attributes": {"unit_of_measurement": "%"}}
        - Output: "EcoFlow battery is 78 percent."
        """

        raw_state = str(payload.get("state", "unknown"))
        attributes = payload.get("attributes", {}) if isinstance(payload.get("attributes"), dict) else {}
        mapped_state = alias.state_map.get(raw_state.lower(), raw_state)
        unit = alias.unit_label or attributes.get("unit_of_measurement")
        value = self._format_value(mapped_state, unit)
        template = alias.response_template or "{friendly_name} is {value}."
        answer = template.format(
            friendly_name=alias.friendly_name,
            value=value,
            state=mapped_state,
            raw_state=raw_state,
            unit=unit or "",
        )

        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.HOME_ASSISTANT,
            answer=answer,
            details=f"Entity {alias.entity_id} returned raw state `{raw_state}`.",
            raw={"entity_id": alias.entity_id, "state_payload": payload},
        )

    @staticmethod
    def _format_value(value: str, unit: str | None) -> str:
        if not unit:
            return value

        display_unit = unit
        if unit == "%":
            display_unit = "percent"
        return f"{value} {display_unit}".strip()

