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
    assert "SecondBrain ist bereit" in response.json()["response"]["outputSpeech"]["text"]
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
                answer="SecondBrain ist deine Wissensschicht für Dokumente.",
            ),
            spoken_text="SecondBrain ist deine Wissensschicht für Dokumente.",
            reprompt_text="Du kannst direkt eine weitere Frage stellen.",
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
    assert body["response"]["outputSpeech"]["text"] == "SecondBrain ist deine Wissensschicht für Dokumente."
    assert body["response"]["reprompt"]["outputSpeech"]["text"] == "Du kannst direkt eine weitere Frage stellen."


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


def test_alexa_yes_intent_reads_continuation_chunk() -> None:
    app = create_app(Settings(_env_file=None, alexa_verify_signature=False, alexa_application_ids=["amzn1.ask.skill.test"]))
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {
            "new": False,
            "sessionId": "SessionId.continue",
            "application": {"applicationId": "amzn1.ask.skill.test"},
            "attributes": {
                "continuation_chunks": [
                    "Das ist der zweite Teil.",
                    "Das ist der dritte Teil.",
                ]
            },
        },
        "request": {
            "type": "IntentRequest",
            "requestId": "EdwRequestId.yes",
            "timestamp": now_iso(),
            "locale": "de-DE",
            "intent": {"name": "AMAZON.YesIntent", "slots": {}},
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["outputSpeech"]["text"] == "Das ist der zweite Teil. Soll ich weiterlesen?"
    assert body["response"]["shouldEndSession"] is False
    assert body["sessionAttributes"]["continuation_chunks"] == ["Das ist der dritte Teil."]


def test_alexa_no_intent_ends_session() -> None:
    app = create_app(Settings(_env_file=None, alexa_verify_signature=False, alexa_application_ids=["amzn1.ask.skill.test"]))
    client = TestClient(app)

    payload = {
        "version": "1.0",
        "session": {"new": False, "sessionId": "SessionId.no", "application": {"applicationId": "amzn1.ask.skill.test"}},
        "request": {
            "type": "IntentRequest",
            "requestId": "EdwRequestId.no",
            "timestamp": now_iso(),
            "locale": "de-DE",
            "intent": {"name": "AMAZON.NoIntent", "slots": {}},
        },
    }

    response = client.post("/alexa/skill", json=payload)

    assert response.status_code == 200
    assert response.json()["response"]["outputSpeech"]["text"] == "Alles klar."
    assert response.json()["response"]["shouldEndSession"] is True
