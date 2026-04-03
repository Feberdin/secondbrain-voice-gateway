"""
Purpose: Expose FastAPI endpoints for Alexa, readiness checks, and local operator debugging.
Input/Output: Accepts Alexa request envelopes and simple REST debug requests; returns JSON responses.
Invariants: Alexa JSON stays valid, debug data is redacted, and disabled debug endpoints stay unavailable.
Debugging: Use `/api/v1/query` before involving Alexa to isolate routing and adapter issues quickly.
"""

from __future__ import annotations

import hashlib
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
    await _record_internal_query(request, payload.question, result)
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
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_rejected",
            question=envelope.question_text(),
            note=str(exc),
            verification_passed=False,
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - operator-facing endpoint should log unexpected verifier failures.
        logger.exception("Alexa request verification failed unexpectedly.")
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_verification_error",
            question=envelope.question_text(),
            note=(
                "Alexa verification failed unexpectedly. Check outbound HTTPS access, "
                "certificate validation, and reverse-proxy header forwarding."
            ),
            verification_passed=False,
        )
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
                "Sag zum Beispiel frage ChatGPT, frage Paperless, frage Home Assistant oder frage Docker."
            ),
            reprompt_text="Sag zum Beispiel frage Docker ob Jellyfin läuft.",
            should_end_session=False,
        )
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_launch",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
        )
        return JSONResponse(response.model_dump())

    if request_type == "SessionEndedRequest":
        response = _build_alexa_response("Sitzung beendet.", should_end_session=True, reprompt_text=None)
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_session_ended",
            speech_text=response.response.outputSpeech.text,
            should_end_session=response.response.shouldEndSession,
            note=envelope.request.reason,
        )
        return JSONResponse(response.model_dump())

    if request_type != "IntentRequest" or not envelope.request.intent:
        raise HTTPException(status_code=400, detail="Unsupported Alexa request type.")

    intent_name = envelope.request.intent.name
    if intent_name == "AMAZON.HelpIntent":
        response = _build_alexa_response(
            speech_text=(
                "Du kannst sagen: frage ChatGPT, frage Paperless, frage Home Assistant oder frage Docker. "
                "Wenn eine Antwort länger ist, frage ich dich, ob ich weiterlesen soll."
            ),
            reprompt_text="Sag zum Beispiel frage Paperless welche Verträge bald enden.",
            should_end_session=False,
        )
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_help",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
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
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_continue_empty",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
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
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_continue",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
            note=f"remaining_chunks={len(remaining_chunks)}",
        )
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.NoIntent":
        response = _build_alexa_response("Alles klar.", should_end_session=True, reprompt_text=None)
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_no",
            speech_text=response.response.outputSpeech.text,
            should_end_session=response.response.shouldEndSession,
        )
        return JSONResponse(response.model_dump())
    if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        response = _build_alexa_response("Bis bald.", should_end_session=True, reprompt_text=None)
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_stop",
            speech_text=response.response.outputSpeech.text,
            should_end_session=response.response.shouldEndSession,
        )
        return JSONResponse(response.model_dump())
    if intent_name == "AMAZON.FallbackIntent":
        response = _build_alexa_response(
            speech_text=(
                "Das habe ich nicht verstanden. "
                "Sag zum Beispiel frage ChatGPT, frage Paperless, frage Home Assistant oder frage Docker."
            ),
            reprompt_text="Sag zum Beispiel frage ChatGPT warum der Himmel blau ist.",
            should_end_session=False,
        )
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_fallback",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
        )
        return JSONResponse(response.model_dump())

    if intent_name != "AskSystemIntent":
        response = _build_alexa_response(
            speech_text="Dieser Befehl ist für diese Alexa Skill nicht eingerichtet.",
            reprompt_text="Stell mir bitte einfach eine freie Frage.",
        )
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_unknown_intent",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
            note=intent_name,
        )
        return JSONResponse(response.model_dump())

    question = envelope.question_text()
    if not question:
        response = _build_alexa_response(
            speech_text="Ich habe den Fragetext nicht verstanden. Bitte versuch es noch einmal als ganze Frage.",
            reprompt_text="Sag zum Beispiel frage Home Assistant wie voll meine Hausbatterie ist.",
            should_end_session=False,
        )
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_missing_question",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
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
    await _record_alexa_event(
        request,
        envelope=envelope,
        event_type="alexa_question",
        question=question,
        result=result,
        speech_text=response.response.outputSpeech.text,
        card_text=response.response.card.content,
        reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
        should_end_session=response.response.shouldEndSession,
        verification_passed=True,
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


async def _record_internal_query(request: Request, question: str, result: Any) -> None:
    """Persist local REST debug queries in the same format as Alexa questions when enabled."""
    recorder = getattr(request.app.state, "request_history", None)
    if not recorder:
        return

    await recorder.record_event(
        {
            "event_type": "api_debug_query",
            "request": {
                "request_id": request.headers.get("x-request-id"),
                "request_type": "api_debug_query",
                "question": question,
                "prepared_question": getattr(result, "prepared_question", None),
                "routing": _routing_snapshot(getattr(result, "routing", None)),
            },
            "response": _result_snapshot(result),
        }
    )


async def _record_alexa_event(
    request: Request,
    *,
    envelope: AlexaRequestEnvelope,
    event_type: str,
    question: str | None = None,
    result: Any | None = None,
    speech_text: str | None = None,
    card_text: str | None = None,
    reprompt_text: str | None = None,
    should_end_session: bool | None = None,
    note: str | None = None,
    verification_passed: bool | None = None,
) -> None:
    """
    Why this exists: Real Alexa usage is the best source for improving routing, prompts, and edge-case handling.
    What happens here: We record one privacy-aware event without storing raw Alexa tokens or full account IDs.
    Example input/output:
    - Input: AskSystemIntent for `frage chatgpt wer war ada lovelace`
    - Output: One JSONL event with the original question, prepared backend question, route, and answer.
    """

    recorder = getattr(request.app.state, "request_history", None)
    if not recorder:
        return

    await recorder.record_event(
        {
            "event_type": event_type,
            "request": {
                "request_id": envelope.request.requestId,
                "request_timestamp": envelope.request.timestamp.isoformat(),
                "request_type": envelope.request.type,
                "intent_name": envelope.request.intent.name if envelope.request.intent else None,
                "request_reason": envelope.request.reason,
                "locale": envelope.request.locale,
                "application_id": envelope.application_id,
                "session_is_new": envelope.session.new if envelope.session else None,
                "session_id_hash": _hash_identifier(envelope.session.sessionId if envelope.session else None),
                "user_id_hash": _hash_identifier(envelope.user_id),
                "question": question,
                "prepared_question": getattr(result, "prepared_question", None),
                "routing": _routing_snapshot(getattr(result, "routing", None)),
                "verification_passed": verification_passed,
            },
            "response": {
                **_result_snapshot(result),
                "spoken_text": speech_text,
                "card_text": card_text,
                "reprompt_text": reprompt_text,
                "should_end_session": should_end_session,
            },
            "note": note,
        }
    )


def _routing_snapshot(routing: Any | None) -> dict[str, Any] | None:
    if routing is None:
        return None
    return {
        "route": getattr(getattr(routing, "route", None), "value", None),
        "confidence": getattr(routing, "confidence", None),
        "reason": getattr(routing, "reason", None),
        "matched_key": getattr(routing, "matched_key", None),
        "matched_rule": getattr(routing, "matched_rule", None),
        "used_ai_fallback": getattr(routing, "used_ai_fallback", None),
    }


def _result_snapshot(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {}

    structured = getattr(result, "result", None)
    evidence = getattr(structured, "evidence", []) if structured else []
    return {
        "status": getattr(getattr(structured, "status", None), "value", None),
        "source": getattr(getattr(structured, "source", None), "value", None),
        "answer": getattr(structured, "answer", None),
        "next_step": getattr(structured, "next_step", None),
        "uncertainty": getattr(structured, "uncertainty", None),
        "continuation_chunk_count": len(getattr(result, "continuation_chunks", []) or []),
        "evidence_titles": [item.title for item in evidence[:3] if getattr(item, "title", None)],
    }


def _hash_identifier(value: str | None) -> str | None:
    """Hash user and session identifiers so we can group requests without storing raw Alexa account data."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
