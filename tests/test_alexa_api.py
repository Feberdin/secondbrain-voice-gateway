"""
Purpose: Verify Alexa-compatible JSON handling for launch and intent requests.
Input/Output: FastAPI `TestClient` posts Alexa envelopes and inspects the returned JSON.
Invariants: The main skill endpoint must stay stable even when backend services are mocked out.
Debugging: If Alexa integration breaks, compare these payloads with the live request body in the Alexa console.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from gateway.main import create_app
from gateway.models.domain import ResultStatus, RouteType, RoutingDecision, SourceType, StructuredAnswer, VoiceQueryResult
from gateway.config import Settings


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_alexa_launch_request_returns_prompt() -> None:
    app = create_app(Settings(_env_file=None, alexa_verify_signature=False, alexa_application_ids=["amzn1.ask.skill.test"]))
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {"new": True, "sessionId": "SessionId.1", "application": {"applicationId": "amzn1.ask.skill.test"}},
        "request": {
            "type": "LaunchRequest",
            "requestId": "EdwRequestId.launch",
            "timestamp": now_iso(),
            "locale": "en-US",
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 200
    assert "SecondBrain voice gateway is ready" in response.json()["response"]["outputSpeech"]["text"]
    assert response.json()["response"]["shouldEndSession"] is False
    assert "reprompt" in response.json()["response"]


def test_alexa_launch_request_rejects_disallowed_user() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            alexa_verify_signature=False,
            alexa_application_ids=["amzn1.ask.skill.test"],
            alexa_allowed_user_ids=["amzn1.ask.account.allowed-user"],
        )
    )
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {
            "new": True,
            "sessionId": "SessionId.unauthorized",
            "application": {"applicationId": "amzn1.ask.skill.test"},
            "user": {"userId": "amzn1.ask.account.blocked-user"},
        },
        "request": {
            "type": "LaunchRequest",
            "requestId": "EdwRequestId.unauthorized",
            "timestamp": now_iso(),
            "locale": "en-US",
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 401
    assert response.json()["detail"] == "Alexa user is not allowed for this gateway."


def test_alexa_intent_request_returns_spoken_answer() -> None:
    app = create_app(Settings(_env_file=None, alexa_verify_signature=False, alexa_application_ids=["amzn1.ask.skill.test"]))

    async def fake_handle_question(question: str) -> VoiceQueryResult:
        assert question == "what SecondBrain is"
        return VoiceQueryResult(
            question=question,
            routing=RoutingDecision(route=RouteType.SYSTEM_EXPLANATION, reason="test"),
            result=StructuredAnswer(
                status=ResultStatus.OK,
                source=SourceType.LOCAL,
                answer="SecondBrain is your document knowledge layer.",
            ),
            spoken_text="SecondBrain is your document knowledge layer.",
            reprompt_text="You can ask a follow-up question.",
        )

    app.state.orchestrator.handle_question = fake_handle_question
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {"new": False, "sessionId": "SessionId.2", "application": {"applicationId": "amzn1.ask.skill.test"}},
        "request": {
            "type": "IntentRequest",
            "requestId": "EdwRequestId.intent",
            "timestamp": now_iso(),
            "locale": "en-US",
            "intent": {
                "name": "AskSystemIntent",
                "slots": {
                    "question": {"name": "question", "value": "what SecondBrain is"}
                },
            },
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["outputSpeech"]["text"] == "SecondBrain is your document knowledge layer."
    assert body["response"]["reprompt"]["outputSpeech"]["text"] == "You can ask a follow-up question."


def test_alexa_fallback_keeps_session_open() -> None:
    app = create_app(Settings(_env_file=None, alexa_verify_signature=False, alexa_application_ids=["amzn1.ask.skill.test"]))
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {"new": False, "sessionId": "SessionId.fallback", "application": {"applicationId": "amzn1.ask.skill.test"}},
        "request": {
            "type": "IntentRequest",
            "requestId": "EdwRequestId.fallback",
            "timestamp": now_iso(),
            "locale": "en-US",
            "intent": {"name": "AMAZON.FallbackIntent", "slots": {}},
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 200
    assert response.json()["response"]["shouldEndSession"] is False
    assert "reprompt" in response.json()["response"]
