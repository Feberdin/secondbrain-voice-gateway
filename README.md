# secondbrain-voice-gateway

Self-hosted Alexa voice integration for SecondBrain, Home Assistant, and Docker operations.

## What This Project Is

`secondbrain-voice-gateway` is a dedicated FastAPI service that lets an Amazon Alexa Custom Skill answer spoken questions from:

- SecondBrain knowledge via REST API
- live Home Assistant entity state
- Docker service health through a restricted socket proxy
- built-in troubleshooting guidance
- a small allowlisted set of safe Home Assistant actions

SecondBrain context preserved in this project:

- SecondBrain is a self-hosted companion system for Paperless-ngx.
- It does not replace Paperless.
- Paperless remains the archive and source of truth.
- SecondBrain extracts structured knowledge from documents and email and exposes queryable knowledge for AI-assisted workflows.
- Existing deployment assumptions include a Docker Compose stack, PostgreSQL, structured JSON logs, a browser UI on port 8081, mail ingestion, Paperless analysis, and optional bearer-token auth.

## Short Plan

1. Accept Alexa Custom Skill requests over HTTPS.
2. Validate the Alexa request origin and skill ID.
3. Route the spoken question deterministically.
4. Query exactly one backend path for the main answer.
5. Convert the result into short Alexa-ready speech.
6. Expose health, readiness, and safe debug endpoints.
7. Keep configuration and allowlists editable through YAML and environment variables.
8. Add tests for routing, adapters, Alexa JSON, config loading, and failure handling.

## Assumptions

- Python 3.12+ is available for local development.
- Home Assistant exposes the REST API and you can create a long-lived access token.
- SecondBrain exposes `/health` and `/query`.
- Docker status is exposed through `tecnativa/docker-socket-proxy` instead of a raw Docker socket mount in the gateway container.
- A reverse proxy or edge service provides public HTTPS for Alexa.

## Repository Tree

```text
secondbrain-voice-gateway/
├── configs/
│   ├── docker_services.yml
│   ├── home_assistant_aliases.yml
│   └── troubleshooting_knowledge.yml
├── docker/
│   └── Dockerfile
├── docs/
│   ├── alexa-custom-skill.md
│   ├── architecture.md
│   └── threat-model.md
├── examples/
│   └── alexa_interaction_model.json
├── secrets/
│   └── .gitkeep
├── src/
│   └── gateway/
│       ├── adapters/
│       ├── alexa/
│       ├── api/
│       ├── models/
│       ├── routing/
│       ├── security/
│       ├── services/
│       ├── utils/
│       ├── config.py
│       └── main.py
├── tests/
├── .env.example
├── CONTRIBUTING.md
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## Architecture

```text
Amazon Alexa Custom Skill
  -> Voice Gateway API
  -> internal adapters
     - SecondBrain adapter
     - Home Assistant adapter
     - Docker adapter
  -> response composer
  -> concise spoken answer
```

Why it is structured this way:

- Alexa-specific validation is isolated from business logic.
- Each adapter owns one external integration and its errors.
- Routing is deterministic first, with optional AI fallback only if enabled.
- Home Assistant actions are allowlisted instead of built dynamically from speech.
- Docker access stays read-only and restricted by a proxy.

## Features

- `LaunchRequest`, `IntentRequest`, and `SessionEndedRequest` support
- `AskSystemIntent` with free-form `question` slot
- deterministic routing for SecondBrain, Home Assistant, Docker, explanation, and troubleshooting
- optional OpenAI-compatible AI fallback for ambiguous routing or answer compression
- optional standalone OAuth2 account-linking server under [`oauth-server/`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/oauth-server/README.md)
- structured JSON logs with request IDs
- `/health`, `/ready`, and `/debug/snapshot`
- Docker Compose deployment
- example Alexa interaction model
- example Home Assistant alias and action config
- example Docker monitor config
- tests for core behavior

## Quickstart

### 1. Prepare secrets

Create the secret files:

```bash
mkdir -p secrets
printf 'YOUR_SECONDBRAIN_TOKEN\n' > secrets/secondbrain_token.txt
printf 'YOUR_HOME_ASSISTANT_TOKEN\n' > secrets/home_assistant_token.txt
printf 'YOUR_AI_API_KEY\n' > secrets/ai_api_key.txt
```

Leave `ai_api_key.txt` empty if AI mode is disabled.

### 2. Create runtime config

```bash
cp .env.example .env
```

Set at least:

- `ALEXA_APPLICATION_IDS`
- `SECOND_BRAIN_BASE_URL`
- `HOME_ASSISTANT_BASE_URL`
- `DOCKER_BASE_URL`
- `ALEXA_VERIFY_SIGNATURE`

### 3. Adapt YAML allowlists

Review and update:

- [`configs/home_assistant_aliases.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/home_assistant_aliases.yml)
- [`configs/docker_services.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/docker_services.yml)
- [`configs/troubleshooting_knowledge.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/troubleshooting_knowledge.yml)

### 4. Start the stack

```bash
docker compose up -d --build
```

### 5. Verify health

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

### 6. Test locally before Alexa

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"how full my EcoFlow batteries are"}'
```

## Local Development

Install and run locally:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/uvicorn gateway.main:app --reload
```

Run tests:

```bash
.venv/bin/pytest
```

## Configuration

Important environment variables:

- `ALEXA_APPLICATION_IDS`: comma-separated list of allowed Alexa Skill IDs
- `ALEXA_ALLOWED_USER_IDS`: optional comma-separated list of allowed Alexa account user IDs for private use
- `ALEXA_VERIFY_SIGNATURE`: keep `true` in production
- `SECOND_BRAIN_BASE_URL`: base URL of the existing SecondBrain API
- `SECOND_BRAIN_QUERY_FIELD_NAME`: JSON field used for `POST /query`
- `HOME_ASSISTANT_BASE_URL`: Home Assistant base URL
- `HOME_ASSISTANT_ALIAS_CONFIG_PATH`: path to the Home Assistant alias allowlist
- `DOCKER_BASE_URL`: Docker socket proxy base URL
- `DOCKER_MONITORS_CONFIG_PATH`: monitored container list
- `AI_ENABLED`, `AI_BASE_URL`, `AI_MODEL`: optional AI mode
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`

Secrets:

- prefer `*_TOKEN_FILE` and `AI_API_KEY_FILE` over plain environment variables
- never commit the real secret files under `secrets/`

## Alexa Custom Skill Setup

Use a `Custom Skill` for this project, not a `Smart Home` skill.

Why this matters:

- `AskSystemIntent` and free-form phrases like “Alexa, ask Second Brain ...” are Custom Skill flows.
- Add `AMAZON.YesIntent` and `AMAZON.NoIntent` so Alexa can continue longer answers in smaller spoken parts.
- Smart Home skills use discovery and directive payloads instead of the intent model implemented by this gateway.
- Account linking can still be added to the Custom Skill through the standalone OAuth server under [`oauth-server/`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/oauth-server/README.md).

1. Create a custom skill in the Alexa Developer Console.
2. Import [`examples/alexa_interaction_model.json`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/alexa_interaction_model.json).
3. Set the HTTPS endpoint to `https://secondbrain-voice.feberdin.de/alexa/skill`.
4. Copy the skill ID to `ALEXA_APPLICATION_IDS`.
5. Build and test the skill in the Alexa console.

Current production values for this environment:

- Skill ID: `amzn1.ask.skill.f55efcdd-a256-41ac-8f64-409d4d7b56d0`
- Endpoint: `https://secondbrain-voice.feberdin.de/alexa/skill`

Sample utterances handled by the model:

- “Alexa, ask Second Brain what SecondBrain is.”
- “Alexa, ask Second Brain which contracts expire in the next thirty days.”
- “Alexa, ask Second Brain how full my EcoFlow batteries are.”
- “Alexa, ask Second Brain if Jellyfin is running.”
- “Alexa, ask Second Brain why mail import is not working.”
- “Alexa, ask Second Brain how to debug SecondBrain.”
- “Alexa, ask Second Brain to turn on EV charging.”

Account linking note:

- The gateway works without account linking.
- If you want account linking now, use the standalone OAuth server under [`oauth-server/`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/oauth-server/README.md).
- For this environment the OAuth endpoints are:
  - `https://secondbrain-voice.feberdin.de/oauth/authorize`
  - `https://secondbrain-voice.feberdin.de/oauth/token`

Private-use note:

- If the skill should work only for your own Alexa account, keep the skill in development or beta and set `ALEXA_ALLOWED_USER_IDS` to your own Alexa `userId`.
- You can find that `userId` in the Alexa developer test request JSON for a live request.
- This adds a second gate on top of the skill ID and keeps the endpoint publicly reachable for Alexa while limiting who receives answers.

## Home Assistant Integration Approach

The gateway uses the Home Assistant REST API:

- read entity state with `GET /api/states/{entity_id}`
- trigger safe actions with `POST /api/services/{domain}/{service}`
- require a long-lived access token
- expose only configured aliases and allowlisted actions

Practical example aliases included:

- EcoFlow battery state of charge
- solar power
- house consumption
- Paperless availability
- go-eCharger charging switch

To add a new sensor:

1. Add a new `entities` entry to [`configs/home_assistant_aliases.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/home_assistant_aliases.yml).
2. Add one or more spoken aliases.
3. Add a `response_template` that sounds natural when read aloud.

To add a new safe action:

1. Add a new `actions` entry to [`configs/home_assistant_aliases.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/home_assistant_aliases.yml).
2. Keep it low risk.
3. Use a very specific alias list.
4. Document the safety note.

## Docker Integration Approach

The gateway never mounts `/var/run/docker.sock` directly.

Instead:

- `docker-proxy` mounts the raw socket read-only
- the gateway talks to `http://docker-proxy:2375`
- the proxy only enables a very small subset of Docker API endpoints

Supported Docker voice questions:

- is container X running
- which monitored containers are unhealthy
- summarize recent restarts
- what should I check first

Operational examples included:

- Jellyfin
- SecondBrain app
- SecondBrain chat on port 8081
- Paperless webserver

## Unraid Installation

For Unraid, use the dedicated templates:

- [`secondbrain-voice-gateway.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/secondbrain-voice-gateway.xml)
- [`docker-socket-proxy.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/docker-socket-proxy.xml)

Why two templates are needed:

- Unraid templates are per container
- this project intentionally separates the voice gateway from the raw Docker socket
- the proxy container keeps Docker access read-only and much narrower

Quick Unraid terminal steps:

```bash
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/source
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/configs
mkdir -p /boot/config/plugins/dockerMan/templates-user/my-secondbrain
docker network create secondbrain-net || true
```

Then:

```bash
cd /mnt/user/appdata/secondbrain-voice-gateway/source
git clone https://github.com/Feberdin/secondbrain-voice-gateway.git .
docker build -t secondbrain-voice-gateway:local -f docker/Dockerfile .
cp examples/unraid/*.xml /boot/config/plugins/dockerMan/templates-user/my-secondbrain/
cp configs/*.yml /mnt/user/appdata/secondbrain-voice-gateway/configs/
```

For your current environment, start with these values in the Unraid template:

```bash
SECOND_BRAIN_BASE_URL=http://192.168.57.10:8080
DOCKER_BASE_URL=http://secondbrain-docker-proxy:2375
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
```

Full step-by-step Unraid notes are in [`docs/unraid.md`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/docs/unraid.md).

## How It Routes Questions

Deterministic routing rules:

1. Safe Home Assistant action aliases plus action verbs
2. Troubleshooting patterns from YAML
3. Built-in explanation questions
4. Docker aliases and status keywords
5. Home Assistant entity aliases and live-state keywords
6. SecondBrain document and knowledge keywords
7. Optional AI fallback
8. Default fallback to SecondBrain

## Logging, Debugging, and Observability

Logging:

- JSON logs on stdout
- request correlation ID in every line
- no full secret values in debug snapshot

Endpoints:

- `GET /health`
- `GET /ready`
- `GET /debug/snapshot` when `DEBUG_ENDPOINTS_ENABLED=true`
- `POST /api/v1/query` for local testing

Where to look first:

```bash
docker compose logs -f voice-gateway
docker compose logs -f docker-proxy
curl http://localhost:8000/ready
```

Typical failure modes:

- Alexa says the endpoint is invalid
  - Check public HTTPS, `ALEXA_APPLICATION_IDS`, and `ALEXA_VERIFY_SIGNATURE`
- Home Assistant answers fail
  - Check the long-lived token, entity IDs, and alias YAML
- Docker answers fail
  - Check the proxy service, monitored container names, and Docker API reachability
- SecondBrain answers fail
  - Check `/health`, bearer token, and the expected `/query` payload field
- Mail ingestion answers are vague
  - Extend [`configs/troubleshooting_knowledge.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/troubleshooting_knowledge.yml) with a more specific playbook

## Major Design Decisions

- FastAPI: clear request handling, strong typing, simple testing
- Pydantic settings: predictable env handling and easy secret-file support
- YAML config files: easier for operators than editing Python code
- Deterministic routing first: explainable and safer for operations
- Optional AI mode only: useful enhancement without becoming a hard dependency
- Docker socket proxy: much safer than mounting the full raw socket into the gateway container

## So You Start It

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```

## So You Debug It

If Alexa cannot answer:

```bash
docker compose logs -f voice-gateway
curl http://localhost:8000/ready
curl -X POST http://localhost:8000/api/v1/query -H 'Content-Type: application/json' -d '{"question":"is Jellyfin running"}'
```

If the answer source seems wrong:

- inspect the routing decision from `POST /api/v1/query`
- check aliases in [`configs/home_assistant_aliases.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/home_assistant_aliases.yml)
- check monitored containers in [`configs/docker_services.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/docker_services.yml)
- extend troubleshooting entries in [`configs/troubleshooting_knowledge.yml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/configs/troubleshooting_knowledge.yml)
