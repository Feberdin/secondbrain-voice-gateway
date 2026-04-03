"""
Purpose: Expose FastAPI endpoints for Alexa, readiness checks, and local operator debugging.
Input/Output: Accepts Alexa request envelopes and simple REST debug requests; returns JSON responses.
Invariants: Alexa JSON stays valid, debug data is redacted, and disabled debug endpoints stay unavailable.
Debugging: Use `/api/v1/query` before involving Alexa to isolate routing and adapter issues quickly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from gateway.alexa.models import (
    AlexaCard,
    AlexaOutputSpeech,
    AlexaReprompt,
    AlexaRequestEnvelope,
    AlexaResponseBody,
    AlexaResponseEnvelope,
)
from gateway.utils.context import set_request_id

logger = logging.getLogger(__name__)

router = APIRouter()
CONTINUATION_KEY = "continuation_chunks"
CONTINUATION_REPROMPT = "Wenn du mehr hören möchtest, sag einfach ja. Wenn nicht, sag nein."
DEFAULT_CARD_TITLE = "SecondBrain Voice Gateway"


class VoiceQueryRequest(BaseModel):
    """Small request model for local REST-based testing."""

    question: str = Field(min_length=1, max_length=500)


@router.get("/health")
async def health() -> dict[str, str]:
    """Cheap liveness endpoint for Docker, reverse proxies, and uptime checks."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, object]:
    """Aggregate readiness checks from configured adapters."""
    reports = await request.app.state.orchestrator.readiness()
    return {
        "status": "ok" if all(report.ok for report in reports) else "degraded",
        "components": [report.model_dump() for report in reports],
    }


@router.get("/debug/snapshot")
async def debug_snapshot(request: Request) -> dict[str, object]:
    """Expose a redacted operator snapshot only when explicitly enabled."""
    settings = request.app.state.settings
    if not settings.debug_endpoints_enabled:
        raise HTTPException(status_code=404, detail="Debug endpoints are disabled.")
    return request.app.state.orchestrator.debug_snapshot()


@router.post("/api/v1/query")
async def internal_query(request: Request, payload: VoiceQueryRequest) -> dict[str, object]:
    """Local REST endpoint for quick testing without the Alexa skill in the loop."""
    result = await request.app.state.orchestrator.handle_question(payload.question)
    return result.model_dump()


@router.post("/alexa/skill")
async def alexa_skill(request: Request) -> JSONResponse:
    """
    Why this exists: Alexa sends signed JSON envelopes that must be validated before routing.
    What happens here: We parse the envelope, verify security rules, route the question, and build Alexa JSON.
    Example input/output:
    - Input: LaunchRequest
    - Output: Spoken prompt inviting the user to ask about SecondBrain, Home Assistant, or Docker.
    """

    body_bytes = await request.body()
    logger.info("Received Alexa request body on /alexa/skill with %s bytes.", len(body_bytes))
    envelope = AlexaRequestEnvelope.model_validate_json(body_bytes)
    set_request_id(envelope.request.requestId)
    logger.info(
        "Parsed Alexa request type=%s locale=%s application_id=%s user_id_present=%s",
        envelope.request.type,
        envelope.request.locale,
        envelope.application_id,
        bool(envelope.user_id),
    )

    headers = {key.lower(): value for key, value in request.headers.items()}
    try:
        await request.app.state.alexa_verifier.verify(body_bytes, headers, envelope)
    except ValueError as exc:
        logger.warning("Rejected Alexa request during verification: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - operator-facing endpoint should log unexpected verifier failures.
        logger.exception("Alexa request verification failed unexpectedly.")
        raise HTTPException(
            status_code=502,
            detail=(
                "Alexa verification failed unexpectedly. Check outbound HTTPS access, "
                "certificate validation, and reverse-proxy header forwarding."
            ),
        ) from exc

    request_type = envelope.request.type
    if request_type == "LaunchRequest":
        logger.info("Handling Alexa launch request.")
        response = _build_alexa_response(
            speech_text=(
                "SecondBrain ist bereit. "
                "Du kannst mich nach Verträgen, Dokumenten, Home Assistant oder Docker fragen."
            ),
            reprompt_text="Frag zum Beispiel, ob Jellyfin läuft oder welche Verträge bald enden.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    if request_type == "SessionEndedRequest":
        response = _build_alexa_response("Sitzung beendet.", should_end_session=True, reprompt_text=None)
        return JSONResponse(response.model_dump())

    if request_type != "IntentRequest" or not envelope.request.intent:
        raise HTTPException(status_code=400, detail="Unsupported Alexa request type.")

    intent_name = envelope.request.intent.name
    if intent_name == "AMAZON.HelpIntent":
        response = _build_alexa_response(
            speech_text=(
                "Du kannst mich nach SecondBrain Wissen, Live-Werten aus Home Assistant oder dem Status deiner Docker-Dienste fragen. "
                "Wenn eine Antwort länger ist, frage ich dich, ob ich weiterlesen soll."
            ),
            reprompt_text="Frag zum Beispiel, welche Verträge in den nächsten dreißig Tagen enden.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.YesIntent":
        continuation_chunks = _continuation_chunks(envelope)
        if not continuation_chunks:
            response = _build_alexa_response(
                speech_text="Ich habe gerade nichts Weiteres zum Vorlesen vorbereitet.",
                reprompt_text="Du kannst eine neue Frage stellen.",
                should_end_session=False,
            )
            return JSONResponse(response.model_dump())

        speech_text = continuation_chunks[0]
        remaining_chunks = continuation_chunks[1:]
        reprompt_text = "Du kannst eine neue Frage stellen."
        session_attributes: dict[str, Any] = {}
        if remaining_chunks:
            speech_text = f"{speech_text} Soll ich weiterlesen?"
            reprompt_text = CONTINUATION_REPROMPT
            session_attributes = {CONTINUATION_KEY: remaining_chunks}

        response = _build_alexa_response(
            speech_text=speech_text,
            reprompt_text=reprompt_text,
            should_end_session=False,
            session_attributes=session_attributes,
        )
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.NoIntent":
        response = _build_alexa_response("Alles klar.", should_end_session=True, reprompt_text=None)
        return JSONResponse(response.model_dump())
    if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        response = _build_alexa_response("Bis bald.", should_end_session=True, reprompt_text=None)
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.FallbackIntent":
        response = _build_alexa_response(
            speech_text="Das habe ich nicht verstanden. Stell mir bitte eine Frage zu Dokumenten, Home Assistant oder Docker.",
            reprompt_text="Frag zum Beispiel, ob Jellyfin läuft.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    if intent_name != "AskSystemIntent":
        response = _build_alexa_response(
            speech_text="Dieser Befehl ist für diese Alexa Skill nicht eingerichtet.",
            reprompt_text="Stell mir bitte einfach eine freie Frage.",
        )
        return JSONResponse(response.model_dump())

    question = envelope.question_text()
    if not question:
        response = _build_alexa_response(
            speech_text="Ich habe den Fragetext nicht verstanden. Bitte versuch es noch einmal als ganze Frage.",
            reprompt_text="Frag zum Beispiel, was SecondBrain ist oder ob Jellyfin läuft.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    logger.info("Handling Alexa question at %s", datetime.now(UTC).isoformat())
    result = await request.app.state.orchestrator.handle_question(question)
    session_attributes = {}
    if result.continuation_chunks:
        session_attributes[CONTINUATION_KEY] = result.continuation_chunks
    response = _build_alexa_response(
        speech_text=result.spoken_text,
        reprompt_text=result.reprompt_text,
        card_text=result.result.answer,
        should_end_session=False,
        session_attributes=session_attributes,
    )
    return JSONResponse(response.model_dump())


def _build_alexa_response(
    speech_text: str,
    reprompt_text: str | None = None,
    card_text: str | None = None,
    should_end_session: bool = True,
    session_attributes: dict[str, Any] | None = None,
) -> AlexaResponseEnvelope:
    """Build one Alexa-compatible response envelope from plain text inputs."""
    if reprompt_text and should_end_session:
        logger.warning("Reprompt text was provided with should_end_session=true. Keeping the Alexa session open instead.")
        should_end_session = False
    response = AlexaResponseBody(
        outputSpeech=AlexaOutputSpeech(text=speech_text),
        card=AlexaCard(
            title=DEFAULT_CARD_TITLE,
            content=card_text or speech_text,
        ),
        shouldEndSession=should_end_session,
        reprompt=AlexaReprompt(outputSpeech=AlexaOutputSpeech(text=reprompt_text)) if reprompt_text else None,
    )
    return AlexaResponseEnvelope(response=response, sessionAttributes=session_attributes or {})


def _continuation_chunks(envelope: AlexaRequestEnvelope) -> list[str]:
    """Read continuation state from the Alexa session and normalize it into a string list."""
    attributes = envelope.session.attributes if envelope.session else {}
    raw_chunks = attributes.get(CONTINUATION_KEY, [])
    if not isinstance(raw_chunks, list):
        return []
    return [str(chunk).strip() for chunk in raw_chunks if str(chunk).strip()]
