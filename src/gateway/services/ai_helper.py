"""
Purpose: Optional helper for OpenAI-compatible routing fallback, general answers, and answer compression.
Input/Output: Accepts plain text prompts and returns routing decisions, grounded general answers, or shorter summaries.
Invariants: The gateway works fully without AI mode; failures always fall back to deterministic behavior.
Debugging: If AI mode seems ignored, inspect `AI_ENABLED`, base URL, model, and gateway logs for fallback messages.
"""

from __future__ import annotations

import json
import logging

import httpx

from gateway.config import Settings
from gateway.models.domain import ResultStatus, RouteType, SourceType, StructuredAnswer

logger = logging.getLogger(__name__)


class OptionalAiHelper:
    """
    Purpose: Keep all optional AI behavior in one place so it can be disabled safely.
    Input/Output: `classify_route()` returns a route or `None`; `answer_general_question()` returns a normalized
    `StructuredAnswer`; `compress_text()` returns a shorter variant or the input.
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
            "Classify the user question into exactly one internal route. "
            "Allowed values: secondbrain_query, general_ai, home_assistant_state, home_assistant_action, "
            "docker_status, system_explanation, troubleshooting. "
            "Use `general_ai` for broad general-knowledge questions that are not primarily about "
            "SecondBrain, Home Assistant, Docker, or troubleshooting this system.\n"
            f"Question: {question}\n"
            "Reply with JSON like {\"route\": \"general_ai\"}."
        )

        try:
            data = await self._chat_completion(prompt)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("AI route classification failed and will be ignored: %s", exc)
            return None

        try:
            route_name = data["route"]
            return RouteType(route_name)
        except (KeyError, TypeError, ValueError):
            logger.warning("AI route classification returned an invalid payload.")
            return None

    async def answer_general_question(self, question: str) -> StructuredAnswer:
        """
        Why this exists: General voice questions should not be forced through SecondBrain when the user simply wants
        a short ChatGPT-style answer.
        What happens here: We request one compact German Alexa-ready answer plus optional uncertainty and next step.
        Example input/output:
        - Input: "Wer war Ada Lovelace?"
        - Output: answer="Ada Lovelace war eine Mathematikerin ...", source=GENERAL_AI
        """

        if not self.enabled:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.GENERAL_AI,
                answer="Die allgemeine KI-Antwort ist gerade nicht aktiviert.",
                next_step="Setze AI_ENABLED, AI_BASE_URL, AI_MODEL und einen gueltigen API-Schluessel.",
            )

        prompt = (
            "Beantworte die folgende allgemeine Nutzerfrage fuer Alexa auf Deutsch. "
            "Antworte kurz, klar und ohne Markdown. Nutze hoechstens drei kurze Saetze. "
            "Wenn du unsicher bist, nenne die Unsicherheit knapp. "
            "Falls ein sinnvoller naechster Schritt hilft, gib ihn kurz an, sonst gib `null` zurueck.\n"
            f"Frage: {question}\n"
            "Reply with JSON like "
            "{\"answer\": \"...\", \"uncertainty\": null, \"next_step\": null}."
        )

        try:
            data = await self._chat_completion(prompt)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("AI general answer failed and will fall back to an operator-friendly message: %s", exc)
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.GENERAL_AI,
                answer="Ich konnte die allgemeine KI-Antwort gerade nicht abrufen.",
                next_step="Versuch es bitte gleich noch einmal oder formuliere die Frage etwas einfacher.",
                raw={"error": str(exc)},
            )

        answer = self._optional_text(data.get("answer"))
        uncertainty = self._optional_text(data.get("uncertainty"))
        next_step = self._optional_text(data.get("next_step"))

        if not answer:
            logger.warning("AI general answer returned an invalid payload.")
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.GENERAL_AI,
                answer="Ich habe gerade keine verlaessliche allgemeine Antwort vorbereitet.",
                next_step="Frag die Frage bitte noch einmal etwas konkreter.",
                raw={"ai_payload": data},
            )

        return StructuredAnswer(
            status=ResultStatus.OK if not uncertainty else ResultStatus.UNCERTAIN,
            source=SourceType.GENERAL_AI,
            answer=answer,
            uncertainty=uncertainty,
            next_step=next_step,
            raw={"ai_payload": data},
        )

    async def compress_text(self, text: str) -> str:
        if not self.enabled:
            return text

        prompt = (
            "Compress the following answer for Alexa. Keep only the direct answer, "
            "one short explanation, and one short next step if helpful. Use plain text.\n"
            f"Answer: {text}"
        )
        try:
            data = await self._chat_completion(prompt)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("AI compression failed; returning original speech text: %s", exc)
            return text
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
                    "content": (
                        "You are a reliable assistant for a self-hosted Alexa voice gateway. "
                        "Return valid JSON only."
                    ),
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

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """Normalize optional text fields from JSON and drop empty or explicit null-like values."""
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"null", "none", "n/a"}:
            return None
        return cleaned
