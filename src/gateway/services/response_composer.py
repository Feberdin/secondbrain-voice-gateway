"""
Purpose: Convert normalized backend results into short speech optimized for Alexa.
Input/Output: Accepts a `StructuredAnswer` and returns spoken text plus an optional reprompt.
Invariants: Spoken output stays concise, grounded, and clear about uncertainty and origin.
Debugging: Compare `answer`, `details`, and final `spoken_text` when important context seems to disappear.
"""

from __future__ import annotations

import re

from gateway.config import Settings
from gateway.models.domain import ResultStatus, SourceType, StructuredAnswer
from gateway.services.ai_helper import OptionalAiHelper


class ResponseComposer:
    """
    Purpose: Keep speech formatting rules separate from backend logic.
    Input/Output: Produces Alexa-ready speech text and optional reprompts.
    Invariants: We favor direct answers first, then one short explanation, then one next step if useful.
    Debugging: Lower the max length locally if Alexa responses still feel too long in practice.
    """

    MAX_CHARS = 320
    MAX_SENTENCES = 3

    def __init__(self, settings: Settings, ai_helper: OptionalAiHelper) -> None:
        self.settings = settings
        self.ai_helper = ai_helper

    async def compose(self, result: StructuredAnswer) -> tuple[str, str | None]:
        parts: list[str] = []
        prefix = self._source_prefix(result.source)
        if prefix:
            parts.append(prefix)
        parts.append(result.answer)
        if result.uncertainty:
            parts.append(result.uncertainty)
        if result.status != ResultStatus.OK and result.next_step:
            parts.append(f"Check this next: {result.next_step}")
        elif result.next_step and result.source in {SourceType.TROUBLESHOOTING, SourceType.DOCKER}:
            parts.append(result.next_step)

        speech = self._compact(" ".join(parts))
        if len(speech) > self.MAX_CHARS and self.ai_helper.enabled:
            speech = await self.ai_helper.compress_text(speech)
            speech = self._compact(speech)

        reprompt = None
        if self.settings.alexa_enable_reprompt:
            reprompt = "You can ask about SecondBrain, Home Assistant, Docker status, or a safe action."

        return speech, reprompt

    @staticmethod
    def _source_prefix(source: SourceType) -> str:
        return {
            SourceType.SECOND_BRAIN: "According to SecondBrain,",
            SourceType.HOME_ASSISTANT: "From live Home Assistant data,",
            SourceType.DOCKER: "From live Docker status,",
            SourceType.TROUBLESHOOTING: "From configured troubleshooting guidance,",
            SourceType.LOCAL: "",
        }[source]

    def _compact(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        shortened = " ".join(sentences[: self.MAX_SENTENCES]).strip()
        if len(shortened) <= self.MAX_CHARS:
            return shortened
        clipped = shortened[: self.MAX_CHARS].rsplit(" ", 1)[0].strip()
        return clipped.rstrip(".,;:") + "."

