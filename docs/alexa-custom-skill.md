# Purpose

This document explains how to connect the self-hosted gateway to an Amazon Alexa Custom Skill.

## Skill Setup

1. Open the Alexa Developer Console and create a new custom skill.
2. Import [`examples/alexa_interaction_model.json`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/alexa_interaction_model.json).
3. Set the invocation name to `second brain` or change it to a name you prefer such as `my system`.
4. Point the HTTPS endpoint to `https://your-public-hostname/alexa/skill`.
5. Copy the generated Skill ID into `ALEXA_APPLICATION_IDS` in `.env`.

## Endpoint Requirements

- Alexa requires a public HTTPS endpoint with a valid certificate.
- In production, leave `ALEXA_VERIFY_SIGNATURE=true`.
- Put Nginx, Traefik, or Caddy in front of the gateway if you do not want the container to terminate TLS directly.
- If you use a reverse proxy, consider setting `REVERSE_PROXY_IP_ALLOWLIST` to the proxy subnet.

## Interaction Model Notes

- The free-form slot uses `AMAZON.SearchQuery`, which is the best fit for natural spoken questions.
- `AskSystemIntent` is the main intent for queries such as:
  - “Alexa, ask Second Brain what SecondBrain is.”
  - “Alexa, ask Second Brain which contracts expire in the next thirty days.”
  - “Alexa, ask Second Brain if Jellyfin is running.”
- Account linking is optional. Add it only if you want user-specific personalization or per-user authorization in the future.

