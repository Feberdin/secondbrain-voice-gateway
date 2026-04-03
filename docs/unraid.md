# Purpose

This document explains how to deploy `secondbrain-voice-gateway` on Unraid with secure defaults.

## Why There Are Two Templates

Unraid templates are container-centric. This project needs:

1. `secondbrain-docker-proxy`
2. `secondbrain-voice-gateway`

That split keeps the voice gateway away from the raw Docker socket.

## Assumptions

- You use Unraid with the standard Docker service enabled.
- You have shell access to the Unraid host.
- You want to keep config and secret files under `/mnt/user/appdata/secondbrain-voice-gateway/`.
- You either clone the repository on Unraid or copy it there from another machine.

## Template Files

- [`docker-socket-proxy.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/docker-socket-proxy.xml)
- [`secondbrain-voice-gateway.xml`](/Users/joachim.stiegler/HomeAssistant-AlexaAI/examples/unraid/secondbrain-voice-gateway.xml)

## Unraid Terminal Commands

### 1. Create directories and a dedicated Docker network

```bash
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/source
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/configs
mkdir -p /mnt/user/appdata/secondbrain-voice-gateway/secrets
mkdir -p /boot/config/plugins/dockerMan/templates-user/my-secondbrain
docker network create secondbrain_voice_net || true
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

### 5. Create the secret files

```bash
printf 'YOUR_SECONDBRAIN_TOKEN\n' > /mnt/user/appdata/secondbrain-voice-gateway/secrets/secondbrain_token.txt
printf 'YOUR_HOME_ASSISTANT_TOKEN\n' > /mnt/user/appdata/secondbrain-voice-gateway/secrets/home_assistant_token.txt
printf '\n' > /mnt/user/appdata/secondbrain-voice-gateway/secrets/ai_api_key.txt
chmod 600 /mnt/user/appdata/secondbrain-voice-gateway/secrets/*.txt
```

### 6. Build the voice gateway image locally on Unraid

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
   - `SECOND_BRAIN_BASE_URL`
   - `HOME_ASSISTANT_BASE_URL`
   - `DOCKER_BASE_URL`
6. Create the gateway container.

## First Start Checks

After both containers are running:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Networks}}' | grep secondbrain
curl http://UNRAID-IP:8000/health
curl http://UNRAID-IP:8000/ready
```

## Debugging

If the gateway cannot answer Docker questions:

```bash
docker logs secondbrain-docker-proxy --tail 100
docker logs secondbrain-voice-gateway --tail 100
docker network inspect secondbrain_voice_net
```

If Home Assistant calls fail:

- check the token file content
- check the entity IDs in `home_assistant_aliases.yml`
- verify the configured Home Assistant URL is reachable from Unraid

If SecondBrain calls fail:

- verify the bearer token file
- verify `SECOND_BRAIN_BASE_URL`
- test `/health` from the Unraid host

