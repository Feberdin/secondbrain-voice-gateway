"""
Purpose: Typed Alexa request and response models for safe parsing and predictable JSON output.
Input/Output: FastAPI parses incoming Alexa envelopes with these models and serializes Alexa-compatible responses.
Invariants: Request timestamps stay available for security checks, and application IDs can be read from session or context.
Debugging: Log `AlexaRequestEnvelope.application_id` when Alexa says the endpoint is invalid or the wrong skill is calling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlexaSlot(BaseModel):
    """Represent one Alexa intent slot."""

    name: str
    value: str | None = None


class AlexaIntent(BaseModel):
    """Represent the Alexa intent name and slots."""

    name: str
    slots: dict[str, AlexaSlot] = Field(default_factory=dict)


class AlexaRequestBody(BaseModel):
    """Represent the request payload inside the Alexa envelope."""

    type: str
    requestId: str
    timestamp: datetime
    locale: str | None = None
    intent: AlexaIntent | None = None
    reason: str | None = None


class AlexaApplication(BaseModel):
    """Expose the skill application ID used for skill validation."""

    applicationId: str | None = None


class AlexaSession(BaseModel):
    """Represent the session section used by most custom skill requests."""

    new: bool | None = None
    sessionId: str | None = None
    application: AlexaApplication | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AlexaContextSystem(BaseModel):
    """Represent the system context part needed for application ID access."""

    application: AlexaApplication | None = None
    user: dict[str, Any] = Field(default_factory=dict)


class AlexaContext(BaseModel):
    """Represent the Alexa context wrapper."""

    System: AlexaContextSystem | None = None


class AlexaRequestEnvelope(BaseModel):
    """
    Purpose: Parse the full Alexa request envelope with one validation step.
    Input/Output: FastAPI or manual parsing converts the raw JSON request into this model.
    Invariants: `application_id` resolves consistently from either session or context data.
    Debugging: Use `model_dump()` to inspect unexpected slot layouts during local testing.
    """

    version: str
    session: AlexaSession | None = None
    context: AlexaContext | None = None
    request: AlexaRequestBody

    @property
    def application_id(self) -> str | None:
        session_id = self.session.application.applicationId if self.session and self.session.application else None
        if session_id:
            return session_id
        context_system = self.context.System if self.context else None
        if context_system and context_system.application:
            return context_system.application.applicationId
        return None

    def question_text(self) -> str | None:
        """Return the free-form question slot if present."""
        if not self.request.intent:
            return None
        slot = self.request.intent.slots.get("question")
        return slot.value.strip() if slot and slot.value else None


class AlexaOutputSpeech(BaseModel):
    """Alexa speech response wrapper."""

    type: str = "PlainText"
    text: str


class AlexaCard(BaseModel):
    """Simple card to help operators inspect the last answer in the Alexa app."""

    type: str = "Simple"
    title: str
    content: str


class AlexaReprompt(BaseModel):
    """Alexa reprompt wrapper for follow-up prompts."""

    outputSpeech: AlexaOutputSpeech


class AlexaResponseBody(BaseModel):
    """Inner Alexa response payload."""

    outputSpeech: AlexaOutputSpeech
    card: AlexaCard
    shouldEndSession: bool = True
    reprompt: AlexaReprompt | None = None


class AlexaResponseEnvelope(BaseModel):
    """Top-level Alexa response envelope."""

    version: str = "1.0"
    response: AlexaResponseBody
    sessionAttributes: dict[str, Any] = Field(default_factory=dict)

