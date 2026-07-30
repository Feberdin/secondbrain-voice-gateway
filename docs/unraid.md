# Purpose: Explain Unraid deployment paths for `secondbrain-voice-gateway` and its optional OAuth server.
# Input/Output: Operators read this file to choose DockerMan templates or Broker GitOps and to provide environment-specific values outside Git.
# Invariants: Real Skill IDs, endpoint URLs, tokens, and private host addresses stay in runtime configuration or Broker secrets, not in this document.
# Debugging: Validate the selected deployment path first, then inspect health endpoints and redacted logs.

## Why There Are Two Templates

Unraid templates are container-centric. This project needs:

1. `secondbrain-docker-proxy`
2. `secondbrain-voice-gateway`

That split keeps the voice gateway away from the raw Docker socket.

## Assumptions

- You use Unraid with the standard Docker service enabled.
- You have shell access to the Unraid host.
- You want to keep config files under `/mnt/user/appdata/secondbrain-voice-gateway/`.
- You either clone the repository on Unraid or copy it there from another machine.
- Your existing SecondBrain containers already run on a Docker network reachable from the gateway.
- Your current SecondBrain API base URL is known to the operator.
- Your Home Assistant base URL is known to the operator.
- If another service already uses host port `8000`, choose a different gateway host port such as `8001`.

## Template Files

- [`docker-socket-proxy.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/docker-socket-proxy.xml)
- [`secondbrain-voice-gateway.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/secondbrain-voice-gateway.xml)

## Unraid Terminal Commands

### 1. Create directories and use the existing Docker network

```bash
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/source
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/configs
mkdir -p /boot/config/plugins/dockerMan/templates-user/my-secondbrain
docker network create secondbrain-net || true
```

### 2. Put the project source on Unraid

If the repository already exists on GitHub, clone it:

```bash
cd /mnt/user/appdata/secondbrain-voice-gateway/source
git clone https://github.com/Feberdin/secondbrain-voice-gateway.git .
```

If the repository currently exists only on another machine, copy it to Unraid from that machine:

```bash
rsync -a /path/to/secondbrain-voice-gateway/ root@UNRAID-IP:/mnt/user/appdata/secondbrain-voice-gateway/source/
```

### 3. Copy the Unraid templates into DockerMan

```bash
cp /mnt/user/appdata/secondbrain-voice-gateway/source/examples/unraid/docker-socket-proxy.xml \
   /boot/config/plugins/dockerMan/templates-user/my-secondbrain/

cp /mnt/user/appdata/secondbrain-voice-gateway/source/examples/unraid/secondbrain-voice-gateway.xml \
   /boot/config/plugins/dockerMan/templates-user/my-secondbrain/
```

### 4. Copy the starter YAML config files

```bash
cp /mnt/user/appdata/secondbrain-voice-gateway/source/configs/home_assistant_aliases.yml \
   /mnt/user/appdata/secondbrain-voice-gateway/configs/

cp /mnt/user/appdata/secondbrain-voice-gateway/source/configs/docker_services.yml \
   /mnt/user/appdata/secondbrain-voice-gateway/configs/

cp /mnt/user/appdata/secondbrain-voice-gateway/source/configs/troubleshooting_knowledge.yml \
   /mnt/user/appdata/secondbrain-voice-gateway/configs/
```

### 5. Build the voice gateway image locally on Unraid

```bash
cd /mnt/user/appdata/secondbrain-voice-gateway/source
docker build -t secondbrain-voice-gateway:local -f docker/Dockerfile .
```

## Import Into Unraid UI

1. Open `Docker` in the Unraid web UI.
2. Click `Add Container`.
3. Load `secondbrain-docker-proxy` from your user templates and create it first.
4. Load `secondbrain-voice-gateway` from your user templates.
5. Adjust at least:
   - `ALEXA_APPLICATION_IDS`
   - `ALEXA_ALLOWED_USER_IDS` if the skill should answer only your own Alexa account
   - `SECOND_BRAIN_BASE_URL`
   - `SECOND_BRAIN_BEARER_TOKEN` if your SecondBrain API auth is enabled
   - `HOME_ASSISTANT_BASE_URL`
   - `HOME_ASSISTANT_TOKEN`
   - `DOCKER_BASE_URL`
   - `AI_ENABLED=true` if you want OpenAI fallback
   - `AI_BASE_URL=https://api.openai.com/v1`
   - `AI_MODEL=gpt-4o-mini`
   - `AI_API_KEY`
6. Create the gateway container.

Recommended value pattern for an environment-specific deployment:

- Network: `secondbrain-net`
- Web UI host port: `8001`
- `ALEXA_APPLICATION_IDS=amzn1.ask.skill.example-skill-id`
- `ALEXA_ALLOWED_USER_IDS=<your Alexa userId>` if you want a private single-user setup
- `SECOND_BRAIN_BASE_URL=http://secondbrain.example.internal:8080`
- `HOME_ASSISTANT_BASE_URL=http://homeassistant.example.internal:8123`
- `DOCKER_BASE_URL=http://secondbrain-docker-proxy:2375`
- Alexa HTTPS endpoint: `https://voice-gateway.example.com/alexa/skill`
- OAuth authorization endpoint: `https://voice-gateway.example.com/oauth/authorize`
- OAuth token endpoint: `https://voice-gateway.example.com/oauth/token`

Important Alexa note:

- Create a `Custom Skill`.
- Do not use a `Smart Home` skill for the current gateway, because this project is built around `AskSystemIntent` and a free-form question slot.

If you prefer internal Docker DNS instead of the host IP, you can also try:

- `SECOND_BRAIN_BASE_URL=http://SecondBrain-App:8080`

That last value is an inference from your current container naming and network setup.

## Broker GitOps For OAuth

Use [`deploy/unraid-broker/oauth-compose.yml`](../deploy/unraid-broker/oauth-compose.yml) when the optional account-linking OAuth server should be deployed through the Unraid Deployment Broker.

Why this exists:

- The local `oauth-server/docker-compose.yml` is optimized for developer machines.
- The Broker compose file uses `secret://...` references so real values are injected only during `deploy_apply`.
- The OAuth server is separate from the legacy DockerMan voice gateway containers, so validating it does not imply a gateway cutover.

Required Broker secrets:

- `SECONDBRAIN_VOICE_OAUTH_POSTGRES_PASSWORD`
- `SECONDBRAIN_VOICE_OAUTH_CLIENT_SECRET`
- `SECONDBRAIN_VOICE_OAUTH_ALLOWED_REDIRECT_URIS`
- `SECONDBRAIN_VOICE_OAUTH_JWT_SECRET`
- `SECONDBRAIN_VOICE_OAUTH_BOOTSTRAP_USER_EMAIL`
- `SECONDBRAIN_VOICE_OAUTH_BOOTSTRAP_USER_PASSWORD`

Broker stack settings:

- Stack name: `secondbrain-voice-oauth`
- Compose file: `deploy/unraid-broker/oauth-compose.yml`
- Working directory: repository root
- Git ref: a full commit SHA, never a branch name

The database URL is assembled inside the OAuth process from the safe Compose values and
`SECONDBRAIN_VOICE_OAUTH_POSTGRES_PASSWORD`. `PUBLIC_BASE_URL` is also a normal Compose value, not
a secret.

Cloudflare Tunnel rule order for `secondbrain-voice.feberdin.de`:

1. `/oauth/authorize`, `/login`, `/oauth/token`, and `/me` -> `http://UNRAID-IP:3100`
2. hostname-wide fallback -> `http://UNRAID-IP:8001`

The path-specific OAuth rule must come first. A `{"detail":"Not Found"}` response during Alexa
account linking means the request still reached the FastAPI gateway on port `8001`.

## First Start Checks

After both containers are running:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Networks}}' | grep secondbrain
curl http://UNRAID-IP:8001/health
curl http://UNRAID-IP:8001/ready
```

## Debugging

If the gateway cannot answer Docker questions:

```bash
docker logs secondbrain-docker-proxy --tail 100
docker logs secondbrain-voice-gateway --tail 100
docker network inspect secondbrain-net
```

If Home Assistant calls fail:

- check the configured Home Assistant token
- check the entity IDs in `home_assistant_aliases.yml`
- verify the configured Home Assistant URL is reachable from Unraid

If SecondBrain calls fail:

- verify the configured bearer token
- verify `SECOND_BRAIN_BASE_URL`
- test `/health` from the Unraid host

## OpenAI Notes

The gateway already supports OpenAI without further code changes.

Set these in the Unraid template or Broker secret/config flow:

- `AI_ENABLED=true`
- `AI_BASE_URL=https://api.openai.com/v1`
- `AI_MODEL=gpt-4o-mini`
- `AI_API_KEY=<configured outside Git>`

I chose these defaults because OpenAI's API reference documents Chat Completions under the `/v1/chat/completions` path, and the current OpenAI model page describes `gpt-4o-mini` as a fast, affordable model for focused tasks.
