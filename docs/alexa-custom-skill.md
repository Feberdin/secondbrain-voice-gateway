# Purpose

This document explains how to connect the self-hosted gateway to an Amazon Alexa Custom Skill.

## Important Skill Type Note

Use a `Custom Skill`, not a `Smart Home` skill.

Why:

- This project expects `LaunchRequest`, `IntentRequest`, `AskSystemIntent`, and simple follow-up intents such as `AMAZON.YesIntent` and `AMAZON.NoIntent`.
- The spoken pattern “Alexa, ask Second Brain ...” is a Custom Skill pattern.
- Smart Home skills use Alexa discovery and directive payloads instead of the interaction model in this repository.
- If you want account linking, add it to the Custom Skill with the OAuth server under [`oauth-server/`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/oauth-server/README.md).

## Skill Setup

1. Open the Alexa Developer Console and create a new custom skill.
2. Import [`examples/alexa_interaction_model.json`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/alexa_interaction_model.json).
3. Set the invocation name to `second brain` or change it to a name you prefer such as `my system`.
4. Point the HTTPS endpoint to `https://secondbrain-voice.feberdin.de/alexa/skill`.
5. Copy the generated Skill ID into `ALEXA_APPLICATION_IDS` in `.env`.

Current environment values:

- Skill ID: `amzn1.ask.skill.f55efcdd-a256-41ac-8f64-409d4d7b56d0`
- HTTPS endpoint: `https://secondbrain-voice.feberdin.de/alexa/skill`
- Optional private-use gate: `ALEXA_ALLOWED_USER_IDS=<your Alexa userId>`

## Endpoint Requirements

- Alexa requires a public HTTPS endpoint with a valid certificate.
- In production, leave `ALEXA_VERIFY_SIGNATURE=true`.
- Put Nginx, Traefik, or Caddy in front of the gateway if you do not want the container to terminate TLS directly.
- If you use a reverse proxy, consider setting `REVERSE_PROXY_IP_ALLOWLIST` to the proxy subnet.

## Interaction Model Notes

- The free-form slot uses `AMAZON.SearchQuery`, which is the best fit for natural spoken questions.
- For a German `de-DE` skill, keep the sample utterances in German and rebuild the model after changes.
- Keep `AMAZON.YesIntent` and `AMAZON.NoIntent` in the model so Alexa can continue longer answers in small chunks.
- Import the latest interaction model from [`examples/alexa_interaction_model.json`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/alexa_interaction_model.json) and rebuild it after gateway updates.
- The current example model also adds explicit German fallback intents:
  - `ContinueIntent` for phrases like `weiter` or `lies weiter`
  - `PositiveFeedbackIntent` for phrases like `hilfreich`
  - `NegativeFeedbackIntent` for phrases like `nicht hilfreich`
- `AskSystemIntent` is the main intent for queries such as:
  - “Alexa, öffne Second Brain.”
  - “Frage ob Jellyfin läuft.”
  - “Frage welche Verträge in den nächsten 30 Tagen ablaufen.”
  - “Frage wie voll meine EcoFlow Batterien sind.”
- Account linking is optional for the gateway itself.
- If you enable it in Alexa, use these OAuth endpoints:
  - `https://secondbrain-voice.feberdin.de/oauth/authorize`
  - `https://secondbrain-voice.feberdin.de/oauth/token`

## Keeping The Skill Private

- Leave the skill unpublished while you are the only user.
- For an extra safety layer, set `ALEXA_ALLOWED_USER_IDS` in the gateway config.
- The value should be your own Alexa `userId` from the request JSON shown in the Alexa developer test tools.
- With that setting enabled, the gateway rejects requests from other Alexa users even though the HTTPS endpoint is public.
