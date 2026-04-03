"""
Purpose: Expose FastAPI endpoints for Alexa, readiness checks, and local operator debugging.
Input/Output: Accepts Alexa request envelopes and simple REST debug requests; returns JSON responses.
Invariants: Alexa JSON stays valid, debug data is redacted, and disabled debug endpoints stay unavailable.
Debugging: Use `/api/v1/query` before involving Alexa to isolate routing and adapter issues quickly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

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
                "SecondBrain voice gateway is ready. "
                "Ask about contracts, Home Assistant sensors, Docker services, or a safe action."
            ),
            reprompt_text="Try asking how full your EcoFlow batteries are or whether Jellyfin is running.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    if request_type == "SessionEndedRequest":
        response = _build_alexa_response("Session ended.", should_end_session=True, reprompt_text=None)
        return JSONResponse(response.model_dump())

    if request_type != "IntentRequest" or not envelope.request.intent:
        raise HTTPException(status_code=400, detail="Unsupported Alexa request type.")

    intent_name = envelope.request.intent.name
    if intent_name == "AMAZON.HelpIntent":
        response = _build_alexa_response(
            speech_text=(
                "You can ask me about SecondBrain knowledge, live Home Assistant values, Docker service status, "
                "or a safe action like turning on EV charging."
            ),
            reprompt_text="For example, ask which contracts expire in the next thirty days.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())
    if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        response = _build_alexa_response("Goodbye.", should_end_session=True, reprompt_text=None)
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.FallbackIntent":
        response = _build_alexa_response(
            speech_text="I did not understand that request. Try a question about SecondBrain, Home Assistant, or Docker.",
            reprompt_text="For example, ask if Jellyfin is running.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    if intent_name != "AskSystemIntent":
        response = _build_alexa_response(
            speech_text="That intent is not configured for this skill.",
            reprompt_text="Try asking a free-form question with Ask System.",
        )
        return JSONResponse(response.model_dump())

    question = envelope.question_text()
    if not question:
        response = _build_alexa_response(
            speech_text="I did not catch the question text. Please try again with a full request.",
            reprompt_text="Try asking what SecondBrain is or whether Jellyfin is running.",
            should_end_session=False,
        )
        return JSONResponse(response.model_dump())

    logger.info("Handling Alexa question at %s", datetime.now(UTC).isoformat())
    result = await request.app.state.orchestrator.handle_question(question)
    response = _build_alexa_response(
        speech_text=result.spoken_text,
        reprompt_text=result.reprompt_text,
        card_text=result.result.details or result.spoken_text,
        should_end_session=False,
    )
    return JSONResponse(response.model_dump())


def _build_alexa_response(
    speech_text: str,
    reprompt_text: str | None = None,
    card_text: str | None = None,
    should_end_session: bool = True,
) -> AlexaResponseEnvelope:
    """Build one Alexa-compatible response envelope from plain text inputs."""
    if reprompt_text and should_end_session:
        logger.warning("Reprompt text was provided with should_end_session=true. Keeping the Alexa session open instead.")
        should_end_session = False
    response = AlexaResponseBody(
        outputSpeech=AlexaOutputSpeech(text=speech_text),
        card=AlexaCard(
            title="SecondBrain Voice Gateway",
            content=card_text or speech_text,
        ),
        shouldEndSession=should_end_session,
        reprompt=AlexaReprompt(outputSpeech=AlexaOutputSpeech(text=reprompt_text)) if reprompt_text else None,
    )
    return AlexaResponseEnvelope(response=response)
