"""
Purpose: Verify Alexa speech composition, chunking, and follow-up prompts.
Input/Output: Tests feed normalized `StructuredAnswer` objects into the composer and inspect the returned chunks.
Invariants: Long answers are split into short spoken parts and invite the user to continue with a simple yes/no turn.
Debugging: If Alexa starts reading filenames or overly long paragraphs, inspect these tests before changing routing logic.
"""

from __future__ import annotations

import pytest

from gateway.config import Settings
from gateway.models.domain import ResultStatus, SourceType, StructuredAnswer
from gateway.services.ai_helper import OptionalAiHelper
from gateway.services.response_composer import ResponseComposer


@pytest.mark.asyncio
async def test_compose_returns_german_default_reprompt() -> None:
    composer = ResponseComposer(Settings(_env_file=None), OptionalAiHelper(Settings(_env_file=None)))

    result = StructuredAnswer(
        status=ResultStatus.OK,
        source=SourceType.LOCAL,
        answer="SecondBrain ist bereit.",
    )

    composed = await composer.compose(result)

    assert composed.spoken_text == "SecondBrain ist bereit."
    assert composed.reprompt_text == "Du kannst mich nach Dokumenten, Verträgen, Home Assistant oder Docker fragen."
    assert composed.continuation_chunks == []


@pytest.mark.asyncio
async def test_compose_splits_long_answer_into_continuation_chunks() -> None:
    composer = ResponseComposer(Settings(_env_file=None), OptionalAiHelper(Settings(_env_file=None)))

    result = StructuredAnswer(
        status=ResultStatus.OK,
        source=SourceType.SECOND_BRAIN,
        answer=(
            "Ich habe das erste wichtige Detail gefunden. "
            "Hier ist das zweite wichtige Detail. "
            "Jetzt folgt noch ein drittes Detail mit zusätzlichem Kontext."
        ),
    )

    composed = await composer.compose(result)

    assert composed.spoken_text.endswith("Soll ich weiterlesen?")
    assert composed.reprompt_text == "Wenn du mehr hören möchtest, sag einfach ja. Wenn nicht, sag nein."
    assert composed.continuation_chunks


@pytest.mark.asyncio
async def test_compose_filters_retrieval_debug_sentences_from_spoken_text() -> None:
    composer = ResponseComposer(Settings(_env_file=None), OptionalAiHelper(Settings(_env_file=None)))

    result = StructuredAnswer(
        status=ResultStatus.OK,
        source=SourceType.SECOND_BRAIN,
        answer=(
            "Found 5 structured matches and 5 semantic context matches with adaptive retrieval limit 5. "
            "Jellyfin laeuft gerade ohne bekannten Fehler."
        ),
    )

    composed = await composer.compose(result)

    assert "structured matches" not in composed.spoken_text.lower()
    assert composed.spoken_text == "Jellyfin laeuft gerade ohne bekannten Fehler."
