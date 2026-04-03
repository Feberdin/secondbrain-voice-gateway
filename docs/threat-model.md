# Purpose

This document captures the main security assumptions and risks for operating the voice gateway.

## Threat Model

- Alexa requests originate from the public internet and must be authenticated.
- Internal adapters reach privileged systems such as Home Assistant and Docker status endpoints.
- Spoken commands can be overheard or triggered accidentally, so only low-risk actions should be voice-enabled.

## Security Controls

- Alexa application IDs are allowlisted.
- Alexa request signature verification is supported and should remain enabled in production.
- Home Assistant actions are explicitly allowlisted in [`configs/home_assistant_aliases.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/home_assistant_aliases.yml).
- Docker access is read-only and proxied through `docker-socket-proxy`.
- Secrets can be supplied via mounted files instead of plain environment variables.
- Optional IP allowlisting can restrict who may call the gateway endpoint.

## Residual Risks

- If the reverse proxy trusts the wrong `X-Forwarded-For` headers, IP allowlisting can be bypassed.
- If a Home Assistant action is allowlisted too broadly, accidental voice execution becomes possible.
- If Docker proxy permissions are expanded carelessly, the gateway could see more daemon capabilities than intended.

