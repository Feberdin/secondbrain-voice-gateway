"""
Purpose: Application bootstrap for the SecondBrain voice gateway.
Input/Output: Loads settings, builds adapters and services, and returns the FastAPI app instance.
Invariants: Startup is deterministic and all runtime dependencies are created in one visible place.
Debugging: If startup fails, read this file first to see which config file or adapter constructor is involved.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.adapters.docker import DockerAdapter
from gateway.adapters.home_assistant import HomeAssistantAdapter
from gateway.adapters.secondbrain import SecondBrainAdapter
from gateway.alexa.security import AlexaRequestVerifier
from gateway.api.routes import router
from gateway.config import (
    Settings,
    load_docker_monitor_config,
    load_home_assistant_alias_config,
    load_troubleshooting_config,
)
from gateway.routing.classifier import QuestionRouter
from gateway.security.network import enforce_client_allowlist
from gateway.services.ai_helper import OptionalAiHelper
from gateway.services.orchestrator import VoiceGatewayOrchestrator
from gateway.services.response_composer import ResponseComposer
from gateway.services.troubleshooting import TroubleshootingService
from gateway.utils.context import get_request_id, set_request_id
from gateway.utils.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Purpose: Build the FastAPI app and wire runtime services together.
    Input/Output: Optionally accepts prebuilt settings for tests; otherwise reads from the environment.
    Invariants: One settings object drives the entire app lifecycle.
    Debugging: Create the app with explicit test settings to isolate environment-related surprises.
    """

    resolved_settings = settings or Settings()
    configure_logging(resolved_settings.log_level)

    home_assistant_aliases = load_home_assistant_alias_config(resolved_settings)
    docker_monitors = load_docker_monitor_config(resolved_settings)
    troubleshooting_config = load_troubleshooting_config(resolved_settings)

    ai_helper = OptionalAiHelper(resolved_settings)
    secondbrain_adapter = SecondBrainAdapter(resolved_settings)
    home_assistant_adapter = HomeAssistantAdapter(resolved_settings, home_assistant_aliases)
    docker_adapter = DockerAdapter(resolved_settings, docker_monitors)
    troubleshooting_service = TroubleshootingService(
        troubleshooting_config,
        secondbrain_adapter=secondbrain_adapter,
        home_assistant_adapter=home_assistant_adapter,
        docker_adapter=docker_adapter,
    )
    router_service = QuestionRouter(
        state_aliases=home_assistant_adapter.state_aliases(),
        action_aliases=home_assistant_adapter.action_aliases(),
        docker_monitors=docker_adapter.monitors(),
        troubleshooting_entries=troubleshooting_service.entries(),
        ai_helper=ai_helper,
    )
    response_composer = ResponseComposer(resolved_settings, ai_helper)
    orchestrator = VoiceGatewayOrchestrator(
        settings=resolved_settings,
        router=router_service,
        secondbrain_adapter=secondbrain_adapter,
        home_assistant_adapter=home_assistant_adapter,
        docker_adapter=docker_adapter,
        troubleshooting_service=troubleshooting_service,
        response_composer=response_composer,
    )

    app = FastAPI(
        title="SecondBrain Voice Gateway",
        version="0.1.0",
        description="Alexa custom skill backend for SecondBrain, Home Assistant, and Docker status.",
    )
    app.state.settings = resolved_settings
    app.state.alexa_verifier = AlexaRequestVerifier(resolved_settings)
    app.state.orchestrator = orchestrator

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or request.headers.get("x-amzn-requestid")
        request_id = set_request_id(request_id)
        enforce_client_allowlist(request, resolved_settings)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled gateway exception.")
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": "The gateway hit an unexpected error. Check the JSON logs with the request ID.",
                "request_id": get_request_id(),
            },
        )

    app.include_router(router)
    return app


app = create_app()
