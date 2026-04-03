"""
Purpose: Shared domain models for routing, normalized adapter results, and file-backed config structures.
Input/Output: Adapters, services, and API handlers exchange these typed objects instead of raw dictionaries.
Invariants: Every answer states its source and status so Alexa never invents certainty.
Debugging: Print or log these models with `model_dump()` to inspect normalized behavior end to end.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RouteType(str, Enum):
    """Deterministic routes supported by the voice gateway."""

    SECOND_BRAIN = "secondbrain_query"
    GENERAL_AI = "general_ai"
    HOME_ASSISTANT_STATE = "home_assistant_state"
    HOME_ASSISTANT_ACTION = "home_assistant_action"
    DOCKER_STATUS = "docker_status"
    SYSTEM_EXPLANATION = "system_explanation"
    TROUBLESHOOTING = "troubleshooting"


class ResultStatus(str, Enum):
    """High-level execution outcomes used for speech and operator views."""

    OK = "ok"
    ERROR = "error"
    UNCERTAIN = "uncertain"


class SourceType(str, Enum):
    """Grounding sources announced in concise spoken form."""

    SECOND_BRAIN = "secondbrain"
    GENERAL_AI = "general_ai"
    HOME_ASSISTANT = "home_assistant"
    DOCKER = "docker"
    TROUBLESHOOTING = "troubleshooting"
    LOCAL = "local"


class EvidenceSnippet(BaseModel):
    """Small normalized evidence snippets kept for logs and future debug UIs."""

    title: str
    snippet: str
    url: str | None = None


class RoutingDecision(BaseModel):
    """Result of the deterministic router, including why a route was chosen."""

    route: RouteType
    confidence: float = 1.0
    reason: str
    matched_key: str | None = None
    used_ai_fallback: bool = False


class StructuredAnswer(BaseModel):
    """
    Purpose: Canonical result emitted by all backends before Alexa-specific rendering.
    Input/Output: Adapters populate this model; the response composer turns it into speech and debug text.
    Invariants: Spoken output stays concise, while debug details remain richer and grounded.
    Debugging: Inspect `details` and `evidence` when the short speech sounds correct but lacks operator context.
    """

    status: ResultStatus
    source: SourceType
    answer: str
    details: str | None = None
    next_step: str | None = None
    uncertainty: str | None = None
    evidence: list[EvidenceSnippet] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class HealthReport(BaseModel):
    """Normalized component health entry used by readiness endpoints and troubleshooting flows."""

    component: str
    ok: bool
    detail: str
    source: SourceType


class VoiceQueryResult(BaseModel):
    """Full internal result for REST debug calls and Alexa handling."""

    question: str
    routing: RoutingDecision
    result: StructuredAnswer
    spoken_text: str
    reprompt_text: str | None = None
    continuation_chunks: list[str] = Field(default_factory=list)


class HomeAssistantStateAlias(BaseModel):
    """Describe one readable Home Assistant entity with friendly speech rules."""

    key: str
    friendly_name: str
    entity_id: str
    aliases: list[str] = Field(default_factory=list)
    response_template: str | None = None
    state_map: dict[str, str] = Field(default_factory=dict)
    unit_label: str | None = None


class HomeAssistantActionAlias(BaseModel):
    """Describe one explicitly allowed Home Assistant service action."""

    key: str
    friendly_name: str
    domain: str
    service: str
    aliases: list[str] = Field(default_factory=list)
    service_data: dict[str, Any] = Field(default_factory=dict)
    confirmation_speech: str
    safety_note: str | None = None


class HomeAssistantAliasConfig(BaseModel):
    """Typed wrapper for entity aliases and safe action allowlists."""

    entities: list[HomeAssistantStateAlias] = Field(default_factory=list)
    actions: list[HomeAssistantActionAlias] = Field(default_factory=list)


class DockerMonitorConfig(BaseModel):
    """Describe a monitored container and the first checks operators should make when it fails."""

    key: str
    container_name: str
    friendly_name: str
    aliases: list[str] = Field(default_factory=list)
    first_checks: list[str] = Field(default_factory=list)


class DockerMonitorFile(BaseModel):
    """Typed container for all monitored Docker services."""

    containers: list[DockerMonitorConfig] = Field(default_factory=list)


class TroubleshootingEntry(BaseModel):
    """Grounded troubleshooting note with optional live checks."""

    key: str
    friendly_name: str
    patterns: list[str] = Field(default_factory=list)
    summary: str
    steps: list[str] = Field(default_factory=list)
    live_checks: list[str] = Field(default_factory=list)


class TroubleshootingConfig(BaseModel):
    """Static knowledge used for system explanations and troubleshooting answers."""

    about_secondbrain: str = (
        "SecondBrain is a self-hosted companion for Paperless-ngx. "
        "Paperless remains the archive and source of truth, while SecondBrain extracts structured knowledge "
        "from documents and email so that the knowledge becomes queryable."
    )
    entries: list[TroubleshootingEntry] = Field(default_factory=list)
