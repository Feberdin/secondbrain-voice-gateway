# Purpose

This document explains the production architecture and the main design decisions behind `secondbrain-voice-gateway`.

## Architecture Summary

```text
Amazon Alexa Custom Skill
  -> HTTPS endpoint /alexa/skill
  -> FastAPI voice gateway
  -> deterministic question router
  -> adapters
     - SecondBrain REST
     - Home Assistant REST
     - Docker Engine HTTP API via socket proxy
  -> response composer
  -> concise Alexa JSON response
```

## Why This Structure Exists

- The Alexa boundary is isolated so signature verification, application ID checks, and request parsing stay auditable.
- The router is deterministic first, because operational questions should stay explainable and reproducible.
- Each adapter owns one external system, which keeps timeout handling, auth, and normalization local to that integration.
- The response composer is separated from the adapters so voice UX can evolve without changing backend logic.
- Troubleshooting logic is partly static and partly live, because many operator questions need grounded guidance plus one or two live checks.

## Trust Boundaries

- Alexa reaches only the gateway HTTPS endpoint.
- The gateway reaches internal services over your private Docker network.
- The Docker adapter talks only to a restricted socket proxy, not to the raw Docker socket.
- Home Assistant actions are allowlisted in YAML and cannot be constructed dynamically from spoken input.

## Main Design Decisions

- FastAPI was chosen because it is easy to operate, typed, testable, and well suited for JSON webhook endpoints.
- Pydantic settings were chosen because environment variables, Docker secrets, and typed validation fit this deployment model well.
- YAML alias files were chosen because non-programmers can safely extend aliases and allowlists without touching Python code.
- Optional AI mode stays strictly optional so the system remains fully usable and deterministic without an external model.

