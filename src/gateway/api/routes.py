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
FOLLOW_UP_TYPE_KEY = "follow_up_type"
FEEDBACK_CONTEXT_KEY = "feedback_context"
FOLLOW_UP_CONTINUATION = "continuation"
FOLLOW_UP_FEEDBACK = "feedback"
CONTINUATION_REPROMPT = "Wenn du mehr hören möchtest, sag einfach ja. Wenn nicht, sag nein."
FEEDBACK_PROMPT = "War diese Antwort hilfreich? Sag ja oder nein."
FEEDBACK_REPROMPT = "Sag einfach ja, wenn die Antwort hilfreich war, oder nein, wenn nicht."
DEFAULT_CARD_TITLE = "SecondBrain Voice Gateway"
YES_TEXTS = {
    "ja",
    "ja bitte",
}
NO_TEXTS = {
    "nein",
    "nein danke",
}
CONTINUE_TEXTS = {
    *YES_TEXTS,
    "weiter",
    "lies weiter",
    "bitte weiter",
    "mach weiter",
    "mehr",
    "mehr details",
    "mehr details bitte",
}
STOP_TEXTS = {
    *NO_TEXTS,
    "stopp",
    "stop",
    "abbrechen",
    "genug",
    "nicht weiter",
}
CONTINUE_FOLLOW_UP_INTENTS = {
    "AMAZON.YesIntent",
    "ContinueIntent",
}
POSITIVE_FEEDBACK_INTENTS = {
    "AMAZON.YesIntent",
    "PositiveFeedbackIntent",
}
NEGATIVE_FOLLOW_UP_INTENTS = {
    "AMAZON.NoIntent",
    "NegativeFeedbackIntent",
}


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
    conversation_state = await _conversation_state(request, envelope)
    follow_up_type = _follow_up_type(conversation_state)
    continuation_chunks = _continuation_chunks(conversation_state)
    feedback_context = _feedback_context(conversation_state)

    if intent_name == "AMAZON.HelpIntent":
        if follow_up_type == FOLLOW_UP_CONTINUATION and continuation_chunks:
            session_attributes = await _persist_conversation_state(request, envelope, conversation_state)
            response = _build_alexa_response(
                speech_text=(
                    "Ich habe noch mehr Text vorbereitet. Sag ja oder weiter, wenn ich weiterlesen soll. "
                    "Sag nein oder stopp, wenn ich anhalten soll."
                ),
                reprompt_text=CONTINUATION_REPROMPT,
                should_end_session=False,
                session_attributes=session_attributes,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_help_follow_up_continuation",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        if follow_up_type == FOLLOW_UP_FEEDBACK and feedback_context:
            session_attributes = await _persist_conversation_state(request, envelope, conversation_state)
            response = _build_alexa_response(
                speech_text=(
                    "Ich warte gerade auf dein Feedback. Sag ja, wenn die Antwort hilfreich war. "
                    "Sag nein oder stopp, wenn sie nicht hilfreich war."
                ),
                reprompt_text=FEEDBACK_REPROMPT,
                should_end_session=False,
                session_attributes=session_attributes,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_help_follow_up_feedback",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        await _clear_conversation_state(request, envelope)
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
    if intent_name in CONTINUE_FOLLOW_UP_INTENTS | POSITIVE_FEEDBACK_INTENTS:
        if follow_up_type == FOLLOW_UP_FEEDBACK and feedback_context and intent_name in POSITIVE_FEEDBACK_INTENTS:
            await _clear_conversation_state(request, envelope)
            response = _build_feedback_ack_response()
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_feedback_yes",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
                feedback=_feedback_event_payload(feedback_context, helpful=True, utterance=intent_name),
            )
            return JSONResponse(response.model_dump())

        if follow_up_type != FOLLOW_UP_CONTINUATION or intent_name not in CONTINUE_FOLLOW_UP_INTENTS:
            await _clear_conversation_state(request, envelope)
            response = _build_alexa_response(
                speech_text="Ich habe gerade keinen offenen Schritt vorbereitet. Stell mir einfach eine neue Frage.",
                reprompt_text="Du kannst direkt eine neue Frage stellen.",
                should_end_session=False,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_follow_up_without_context",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                note=intent_name,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        if not continuation_chunks:
            response = _build_alexa_response(
                speech_text="Ich habe gerade nichts Weiteres zum Vorlesen vorbereitet.",
                reprompt_text="Du kannst eine neue Frage stellen.",
                should_end_session=False,
            )
            await _clear_conversation_state(request, envelope)
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_continue_empty",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
            )
            return JSONResponse(response.model_dump())

        response, next_state = _build_continuation_response(
            continuation_chunks,
            feedback_context=feedback_context,
            feedback_enabled=request.app.state.settings.alexa_feedback_enabled,
        )
        session_attributes = await _persist_conversation_state(request, envelope, next_state)
        response.sessionAttributes = session_attributes
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_continue",
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
            note=f"remaining_chunks={len(session_attributes.get(CONTINUATION_KEY, []))}",
            verification_passed=True,
        )
        return JSONResponse(response.model_dump())
    if intent_name in NEGATIVE_FOLLOW_UP_INTENTS:
        if follow_up_type == FOLLOW_UP_FEEDBACK and feedback_context:
            await _clear_conversation_state(request, envelope)
            response = _build_feedback_ack_response()
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_feedback_no",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
                feedback=_feedback_event_payload(feedback_context, helpful=False, utterance=intent_name),
            )
            return JSONResponse(response.model_dump())

        if continuation_chunks and request.app.state.settings.alexa_feedback_enabled and feedback_context:
            next_state = _feedback_state(feedback_context)
            session_attributes = await _persist_conversation_state(request, envelope, next_state)
            response = _build_alexa_response(
                "Alles klar. War die Antwort bis hierhin hilfreich? Sag ja oder nein.",
                reprompt_text=FEEDBACK_REPROMPT,
                should_end_session=False,
                session_attributes=session_attributes,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_continue_declined_feedback_requested",
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        await _clear_conversation_state(request, envelope)
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
        if follow_up_type == FOLLOW_UP_FEEDBACK and feedback_context:
            await _clear_conversation_state(request, envelope)
            response = _build_alexa_response(
                "Danke für dein Feedback. Bis bald.",
                should_end_session=True,
                reprompt_text=None,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_feedback_stop",
                speech_text=response.response.outputSpeech.text,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
                feedback=_feedback_event_payload(feedback_context, helpful=False, utterance=intent_name),
            )
            return JSONResponse(response.model_dump())

        if follow_up_type == FOLLOW_UP_CONTINUATION and continuation_chunks:
            await _clear_conversation_state(request, envelope)
            response = _build_alexa_response(
                "Alles klar, ich lese nicht weiter. Bis bald.",
                should_end_session=True,
                reprompt_text=None,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_continue_stop",
                speech_text=response.response.outputSpeech.text,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        await _clear_conversation_state(request, envelope)
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
        await _clear_conversation_state(request, envelope)
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
        await _clear_conversation_state(request, envelope)
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
        await _clear_conversation_state(request, envelope)
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

    normalized_question = _normalize_follow_up_text(question)
    if follow_up_type == FOLLOW_UP_FEEDBACK and feedback_context:
        if normalized_question in YES_TEXTS:
            await _clear_conversation_state(request, envelope)
            response = _build_feedback_ack_response()
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_feedback_text_yes",
                question=question,
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
                feedback=_feedback_event_payload(feedback_context, helpful=True, utterance=question),
            )
            return JSONResponse(response.model_dump())

        if normalized_question in NO_TEXTS:
            await _clear_conversation_state(request, envelope)
            response = _build_feedback_ack_response()
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_feedback_text_no",
                question=question,
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
                feedback=_feedback_event_payload(feedback_context, helpful=False, utterance=question),
            )
            return JSONResponse(response.model_dump())

        await _clear_conversation_state(request, envelope)

    if continuation_chunks and normalized_question in CONTINUE_TEXTS:
        response, next_state = _build_continuation_response(
            continuation_chunks,
            feedback_context=feedback_context,
            feedback_enabled=request.app.state.settings.alexa_feedback_enabled,
        )
        session_attributes = await _persist_conversation_state(request, envelope, next_state)
        response.sessionAttributes = session_attributes
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_continue_text",
            question=question,
            speech_text=response.response.outputSpeech.text,
            reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
            should_end_session=response.response.shouldEndSession,
            note=f"remaining_chunks={len(session_attributes.get(CONTINUATION_KEY, []))}",
            verification_passed=True,
        )
        return JSONResponse(response.model_dump())

    if continuation_chunks and normalized_question in STOP_TEXTS:
        if request.app.state.settings.alexa_feedback_enabled and feedback_context:
            next_state = _feedback_state(feedback_context)
            session_attributes = await _persist_conversation_state(request, envelope, next_state)
            response = _build_alexa_response(
                "Alles klar. War die Antwort bis hierhin hilfreich? Sag ja oder nein.",
                reprompt_text=FEEDBACK_REPROMPT,
                should_end_session=False,
                session_attributes=session_attributes,
            )
            await _record_alexa_event(
                request,
                envelope=envelope,
                event_type="alexa_stop_text_feedback_requested",
                question=question,
                speech_text=response.response.outputSpeech.text,
                reprompt_text=response.response.reprompt.outputSpeech.text if response.response.reprompt else None,
                should_end_session=response.response.shouldEndSession,
                verification_passed=True,
            )
            return JSONResponse(response.model_dump())

        await _clear_conversation_state(request, envelope)
        response = _build_alexa_response("Alles klar.", should_end_session=True, reprompt_text=None)
        await _record_alexa_event(
            request,
            envelope=envelope,
            event_type="alexa_stop_text",
            question=question,
            speech_text=response.response.outputSpeech.text,
            should_end_session=response.response.shouldEndSession,
            verification_passed=True,
        )
        return JSONResponse(response.model_dump())

    logger.info("Handling Alexa question at %s", datetime.now(UTC).isoformat())
    result = await request.app.state.orchestrator.handle_question(question)
    feedback_enabled = request.app.state.settings.alexa_feedback_enabled
    feedback_context = _feedback_context_from_result(envelope, question, result)
    session_attributes: dict[str, Any] = {}
    speech_text = result.spoken_text
    reprompt_text = result.reprompt_text
    if result.continuation_chunks:
        session_attributes = await _persist_conversation_state(
            request,
            envelope,
            _continuation_state(result.continuation_chunks, feedback_context if feedback_enabled else None),
        )
    elif feedback_enabled:
        speech_text = f"{speech_text} {FEEDBACK_PROMPT}"
        reprompt_text = FEEDBACK_REPROMPT
        session_attributes = await _persist_conversation_state(
            request,
            envelope,
            _feedback_state(feedback_context),
        )
    else:
        await _clear_conversation_state(request, envelope)
    response = _build_alexa_response(
        speech_text=speech_text,
        reprompt_text=reprompt_text,
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


def _continuation_chunks(conversation_state: dict[str, Any]) -> list[str]:
    """Read normalized continuation chunks from the merged conversation state."""
    raw_chunks = conversation_state.get(CONTINUATION_KEY, [])
    if not isinstance(raw_chunks, list):
        return []
    return [str(chunk).strip() for chunk in raw_chunks if str(chunk).strip()]


def _follow_up_type(conversation_state: dict[str, Any]) -> str | None:
    """Return the current expected follow-up type if one is stored."""
    raw_value = conversation_state.get(FOLLOW_UP_TYPE_KEY)
    if raw_value in {FOLLOW_UP_CONTINUATION, FOLLOW_UP_FEEDBACK}:
        return raw_value
    return None


def _feedback_context(conversation_state: dict[str, Any]) -> dict[str, Any] | None:
    """Return normalized feedback metadata kept for one completed answer."""
    raw_context = conversation_state.get(FEEDBACK_CONTEXT_KEY)
    if not isinstance(raw_context, dict):
        return None
    return dict(raw_context)


def _build_continuation_response(
    continuation_chunks: list[str],
    *,
    feedback_context: dict[str, Any] | None,
    feedback_enabled: bool,
) -> tuple[AlexaResponseEnvelope, dict[str, Any]]:
    """
    Continue reading one stored response chunk and keep the session open while text remains.

    Example input/output:
    - Input: ["Teil zwei", "Teil drei"]
    - Output: speech="Teil zwei Soll ich weiterlesen?" with the remaining chunk stored in session attributes.
    """

    speech_text = continuation_chunks[0]
    remaining_chunks = continuation_chunks[1:]
    reprompt_text = "Du kannst eine neue Frage stellen."
    next_state: dict[str, Any] = {}
    if remaining_chunks:
        speech_text = f"{speech_text} Soll ich weiterlesen?"
        reprompt_text = CONTINUATION_REPROMPT
        next_state = _continuation_state(
            remaining_chunks,
            feedback_context if feedback_enabled else None,
        )
    elif feedback_enabled and feedback_context:
        speech_text = f"{speech_text} {FEEDBACK_PROMPT}"
        reprompt_text = FEEDBACK_REPROMPT
        next_state = _feedback_state(feedback_context)

    return (
        _build_alexa_response(
            speech_text=speech_text,
            reprompt_text=reprompt_text,
            should_end_session=False,
            session_attributes=next_state,
        ),
        next_state,
    )


def _build_feedback_ack_response() -> AlexaResponseEnvelope:
    """
    Thank the user for the feedback and keep the session open for one new question.

    Example input/output:
    - Input: user says `ja`
    - Output: speech="Danke für dein Feedback. Du kannst direkt eine neue Frage stellen."
    """

    return _build_alexa_response(
        "Danke für dein Feedback. Du kannst direkt eine neue Frage stellen.",
        reprompt_text="Du kannst direkt eine neue Frage stellen.",
        should_end_session=False,
    )


def _feedback_state(feedback_context: dict[str, Any]) -> dict[str, Any]:
    """Build one normalized state that expects a positive or negative feedback answer next."""
    return {
        FOLLOW_UP_TYPE_KEY: FOLLOW_UP_FEEDBACK,
        FEEDBACK_CONTEXT_KEY: dict(feedback_context),
    }


def _continuation_state(
    continuation_chunks: list[str],
    feedback_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one normalized state that expects a continuation decision next."""
    state: dict[str, Any] = {
        CONTINUATION_KEY: list(continuation_chunks),
        FOLLOW_UP_TYPE_KEY: FOLLOW_UP_CONTINUATION,
    }
    if feedback_context:
        state[FEEDBACK_CONTEXT_KEY] = dict(feedback_context)
    return state


def _feedback_context_from_result(
    envelope: AlexaRequestEnvelope,
    question: str,
    result: Any,
) -> dict[str, Any]:
    """
    Keep a small reference to the answered request so explicit feedback can be stored meaningfully later.

    Example input/output:
    - Input: question="frage chatgpt wer war ada lovelace"
    - Output: {"source_request_id": "...", "question": "...", "route": "general_ai"}
    """

    structured = getattr(result, "result", None)
    answer = getattr(structured, "answer", None)
    answer_preview = answer[:240].rstrip() + " …" if isinstance(answer, str) and len(answer) > 240 else answer
    return {
        "source_request_id": envelope.request.requestId,
        "question": question,
        "prepared_question": getattr(result, "prepared_question", None),
        "route": getattr(getattr(getattr(result, "routing", None), "route", None), "value", None),
        "matched_rule": getattr(getattr(result, "routing", None), "matched_rule", None),
        "source": getattr(getattr(structured, "source", None), "value", None),
        "status": getattr(getattr(structured, "status", None), "value", None),
        "answer_preview": answer_preview,
    }


def _feedback_event_payload(
    feedback_context: dict[str, Any],
    *,
    helpful: bool,
    utterance: str,
) -> dict[str, Any]:
    """Build one structured feedback object for request-history entries."""
    return {
        "helpful": helpful,
        "utterance": utterance,
        **feedback_context,
    }


async def _conversation_state(request: Request, envelope: AlexaRequestEnvelope) -> dict[str, Any]:
    """
    Read follow-up state from Alexa session attributes first and fall back to the in-memory session store.

    Why this exists: Some real-world Alexa follow-up requests do not reliably return the previous session attributes.
    What happens here: We prefer explicit Alexa state when present and otherwise fall back to the server-side session cache.
    """

    session_attributes = envelope.session.attributes if envelope.session else {}
    normalized_attributes = _normalize_conversation_state(session_attributes)
    if normalized_attributes:
        return normalized_attributes

    store = getattr(request.app.state, "alexa_session_state", None)
    if not store:
        return {}
    return _normalize_conversation_state(await store.get(_session_id(envelope)))


async def _persist_conversation_state(
    request: Request,
    envelope: AlexaRequestEnvelope,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Persist one normalized follow-up state both for the response and for the in-memory fallback store."""
    normalized_state = _normalize_conversation_state(state)
    store = getattr(request.app.state, "alexa_session_state", None)
    if store:
        await store.set(_session_id(envelope), normalized_state)
    return normalized_state


async def _clear_conversation_state(request: Request, envelope: AlexaRequestEnvelope) -> None:
    """Remove any pending follow-up state for the current Alexa session."""
    store = getattr(request.app.state, "alexa_session_state", None)
    if store:
        await store.clear(_session_id(envelope))


def _normalize_conversation_state(raw_state: Any) -> dict[str, Any]:
    """Normalize arbitrary session data into the small conversation-state schema used by this route handler."""
    if not isinstance(raw_state, dict):
        return {}

    normalized: dict[str, Any] = {}
    continuation_chunks = raw_state.get(CONTINUATION_KEY, [])
    if isinstance(continuation_chunks, list):
        cleaned_chunks = [str(chunk).strip() for chunk in continuation_chunks if str(chunk).strip()]
        if cleaned_chunks:
            normalized[CONTINUATION_KEY] = cleaned_chunks

    follow_up_type = raw_state.get(FOLLOW_UP_TYPE_KEY)
    if follow_up_type in {FOLLOW_UP_CONTINUATION, FOLLOW_UP_FEEDBACK}:
        normalized[FOLLOW_UP_TYPE_KEY] = follow_up_type

    feedback_context = raw_state.get(FEEDBACK_CONTEXT_KEY)
    if isinstance(feedback_context, dict):
        normalized_feedback_context = {
            key: value
            for key, value in feedback_context.items()
            if key
            in {
                "source_request_id",
                "question",
                "prepared_question",
                "route",
                "matched_rule",
                "source",
                "status",
                "answer_preview",
            }
        }
        if normalized_feedback_context:
            normalized[FEEDBACK_CONTEXT_KEY] = normalized_feedback_context

    if normalized.get(CONTINUATION_KEY) and FOLLOW_UP_TYPE_KEY not in normalized:
        normalized[FOLLOW_UP_TYPE_KEY] = FOLLOW_UP_CONTINUATION
    return normalized


def _session_id(envelope: AlexaRequestEnvelope) -> str | None:
    """Resolve the Alexa session ID when a request participates in a multi-turn conversation."""
    return envelope.session.sessionId if envelope.session else None


def _normalize_follow_up_text(text: str) -> str:
    """Normalize short follow-up phrases so `Ja`, `weiter` and `lies weiter` are treated consistently."""
    return " ".join(text.strip().lower().split())


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
    feedback: dict[str, Any] | None = None,
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
            "feedback": feedback,
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
