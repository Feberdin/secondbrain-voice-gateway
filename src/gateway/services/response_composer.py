"""
Purpose: Convert normalized backend results into short speech optimized for Alexa.
Input/Output: Accepts a `StructuredAnswer` and returns spoken text plus an optional reprompt.
Invariants: Spoken output stays concise, grounded, and clear about uncertainty and origin.
Debugging: Compare `answer`, `details`, and final `spoken_text` when important context seems to disappear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gateway.config import Settings
from gateway.models.domain import ResultStatus, SourceType, StructuredAnswer
from gateway.services.ai_helper import OptionalAiHelper


@dataclass(slots=True)
class ComposedSpeech:
    """
    Purpose: Keep Alexa speech output, follow-up prompt, and deferred chunks together.
    Input/Output: The orchestrator uses this object to build one spoken answer and optional continuation state.
    Invariants: `spoken_text` is always present; `continuation_chunks` only contains still unread text.
    Debugging: Print this object when Alexa sounds too verbose or never asks whether it should continue.
    """

    spoken_text: str
    reprompt_text: str | None
    continuation_chunks: list[str]


class ResponseComposer:
    """
    Purpose: Keep speech formatting rules separate from backend logic.
    Input/Output: Produces Alexa-ready speech text and optional reprompts.
    Invariants: We favor direct answers first, then one short explanation, then one next step if useful.
    Debugging: Lower the max length locally if Alexa responses still feel too long in practice.
    """

    MAX_CHARS_PER_CHUNK = 220
    MAX_SENTENCES_PER_CHUNK = 2
    COMPRESS_THRESHOLD_CHARS = 520
    DEFAULT_REPROMPT = "Du kannst mich nach Dokumenten, Verträgen, Home Assistant oder Docker fragen."
    CONTINUE_PROMPT = "Soll ich weiterlesen?"
    CONTINUE_REPROMPT = "Wenn du mehr hören möchtest, sag einfach ja. Wenn nicht, sag nein."

    def __init__(self, settings: Settings, ai_helper: OptionalAiHelper) -> None:
        self.settings = settings
        self.ai_helper = ai_helper

    async def compose(self, result: StructuredAnswer) -> ComposedSpeech:
        parts: list[str] = []
        prefix = self._source_prefix(result.source)
        if prefix:
            parts.append(prefix)
        parts.append(result.answer)
        if result.uncertainty:
            parts.append(result.uncertainty)
        if result.status != ResultStatus.OK and result.next_step:
            parts.append(f"Prüfe als Nächstes: {result.next_step}")
        elif result.next_step and result.source in {SourceType.TROUBLESHOOTING, SourceType.DOCKER}:
            parts.append(f"Tipp: {result.next_step}")

        speech = self._clean_text(" ".join(parts))
        if len(speech) > self.COMPRESS_THRESHOLD_CHARS and self.ai_helper.enabled:
            speech = await self.ai_helper.compress_text(speech)
            speech = self._clean_text(speech)

        chunks = self._chunk_text(speech)
        primary_speech = chunks[0]
        continuation_chunks = chunks[1:]

        if continuation_chunks:
            return ComposedSpeech(
                spoken_text=f"{primary_speech} {self.CONTINUE_PROMPT}",
                reprompt_text=self.CONTINUE_REPROMPT,
                continuation_chunks=continuation_chunks,
            )

        reprompt = self.DEFAULT_REPROMPT if self.settings.alexa_enable_reprompt else None
        return ComposedSpeech(
            spoken_text=primary_speech,
            reprompt_text=reprompt,
            continuation_chunks=[],
        )

    @staticmethod
    def _source_prefix(source: SourceType) -> str:
        return {
            SourceType.SECOND_BRAIN: "",
            SourceType.HOME_ASSISTANT: "Home Assistant meldet:",
            SourceType.DOCKER: "Docker meldet:",
            SourceType.TROUBLESHOOTING: "Hinweis:",
            SourceType.LOCAL: "",
        }[source]

    @staticmethod
    def _clean_text(text: str) -> str:
        """
        Why this exists: Upstream systems often return filenames, markdown-like punctuation, or multiline blobs.
        What happens here: We normalize spacing and remove the worst speech-breaking characters before chunking.
        Example input/output:
        - Input: "Top_result:\\nfoo_bar.pdf ;  status running"
        - Output: "Top result: foo bar.pdf; status running"
        """

        cleaned = text.replace("\n", " ").replace("_", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.replace(" .", ".").replace(" ,", ",")
        return cleaned

    def _chunk_text(self, text: str) -> list[str]:
        """Split long speech into Alexa-friendly chunks that can be continued with a follow-up question."""
        if not text:
            return ["Ich habe gerade keine Antwort vorbereitet."]

        sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
        if not sentences:
            sentences = [text.strip()]

        chunks: list[str] = []
        current_parts: list[str] = []
        for sentence in sentences:
            candidate = " ".join(current_parts + [sentence]).strip()
            if (
                current_parts
                and (
                    len(candidate) > self.MAX_CHARS_PER_CHUNK
                    or len(current_parts) >= self.MAX_SENTENCES_PER_CHUNK
                )
            ):
                chunks.append(" ".join(current_parts).strip())
                current_parts = [sentence]
                continue

            if len(sentence) > self.MAX_CHARS_PER_CHUNK:
                if current_parts:
                    chunks.append(" ".join(current_parts).strip())
                    current_parts = []
                chunks.extend(self._split_long_sentence(sentence))
                continue

            current_parts.append(sentence)

        if current_parts:
            chunks.append(" ".join(current_parts).strip())

        return [chunk.rstrip(" ,;") for chunk in chunks if chunk.strip()]

    def _split_long_sentence(self, sentence: str) -> list[str]:
        """Fallback splitter for one very long sentence without useful punctuation."""
        words = sentence.split()
        chunks: list[str] = []
        current_words: list[str] = []
        for word in words:
            candidate = " ".join(current_words + [word]).strip()
            if current_words and len(candidate) > self.MAX_CHARS_PER_CHUNK:
                chunks.append(" ".join(current_words).strip().rstrip(" ,;") + ".")
                current_words = [word]
                continue
            current_words.append(word)

        if current_words:
            chunks.append(" ".join(current_words).strip().rstrip(" ,;") + ".")
        return chunks
