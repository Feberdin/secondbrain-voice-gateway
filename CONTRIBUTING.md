# Purpose

This guide explains how to make safe changes to `secondbrain-voice-gateway`.

## Development Workflow

1. Create a virtual environment with `python3 -m venv .venv`.
2. Activate it and install dependencies with `.venv/bin/pip install -e .[dev]`.
3. Run tests with `.venv/bin/pytest`.
4. Start the API locally with `.venv/bin/uvicorn gateway.main:app --reload`.

## Change Guidelines

- Keep routing deterministic first and document every new heuristic.
- Add or update tests for new routes, adapters, and error handling.
- Prefer extending YAML config files for aliases and troubleshooting notes before adding hardcoded logic.
- When exposing new Home Assistant actions, keep them narrowly scoped and clearly documented.

## Debugging Tips

- Use `POST /api/v1/query` before testing through Alexa.
- Use `GET /debug/snapshot` only in controlled environments with `DEBUG_ENDPOINTS_ENABLED=true`.
- Inspect JSON logs and the `X-Request-ID` response header to trace failures.

