"""
Purpose: Optional helper for OpenAI-compatible routing fallback and answer compression.
Input/Output: Accepts plain text prompts and returns compact text decisions or summaries.
Invariants: The gateway works fully without AI mode; failures always fall back to deterministic behavior.
Debugging: If AI mode seems ignored, inspect `AI_ENABLED`, base URL, model, and gateway logs for fallback messages.
"""

from __future__ import annotations

import json
import logging

import httpx

from gateway.config import Settings
from gateway.models.domain import RouteType

logger = logging.getLogger(__name__)


class OptionalAiHelper:
    """
    Purpose: Keep all optional AI behavior in one place so it can be disabled safely.
    Input/Output: `classify_route()` returns a route or `None`; `compress_text()` returns a shorter variant or the input.
    Invariants: AI must never be the only working path for the gateway.
    Debugging: Logs state clearly when AI responses are malformed or unavailable.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.ai_enabled
            and self.settings.ai_base_url
            and self.settings.ai_model
            and self.settings.ai_api_key
        )

    async def classify_route(self, question: str) -> RouteType | None:
        if not self.enabled:
            return None

        prompt = (
            "Classify the user question into exactly one route. "
            "Allowed values: secondbrain_query, home_assistant_state, home_assistant_action, "
            "docker_status, system_explanation, troubleshooting.\n"
            f"Question: {question}\n"
            "Reply with JSON like {\"route\": \"secondbrain_query\"}."
        )

        data = await self._chat_completion(prompt)
        try:
            route_name = data["route"]
            return RouteType(route_name)
        except (KeyError, TypeError, ValueError):
            logger.warning("AI route classification returned an invalid payload.")
            return None

    async def compress_text(self, text: str) -> str:
        if not self.enabled:
            return text

        prompt = (
            "Compress the following answer for Alexa. Keep only the direct answer, "
            "one short explanation, and one short next step if helpful. Use plain text.\n"
            f"Answer: {text}"
        )
        data = await self._chat_completion(prompt)
        compressed = data.get("text")
        return compressed.strip() if isinstance(compressed, str) and compressed.strip() else text

    async def _chat_completion(self, prompt: str) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.ai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a reliable infrastructure assistant. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.ai_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)

