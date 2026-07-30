# Dependency Security Notes 2026-07-30

Purpose: Document the reviewed security-update queue for `Feberdin/secondbrain-voice-gateway`.
Input/Output: Maintainers read this before reviewing the dependency PR; output is an audit trail of applied fixes, compatibility checks, and deferred items.
Invariants: Public examples must not expose real deployment identifiers, OAuth images must install from the reviewed lockfile, and secrets stay outside Git.
Debugging: Re-run the commands in the validation section and inspect the first failing command output.

## Scope

Reviewed queue item: `Feberdin/secondbrain-voice-gateway`, generated at `2026-07-30T08:33:43.032221+00:00`.

Focus areas:

- vulnerable npm dependency graph under `oauth-server/`
- secret-scanner findings in public docs and secret-handling code paths
- deployability risk from the OAuth Dockerfile ignoring `package-lock.json`

## Applied Changes

- Updated `oauth-server/package.json` and `oauth-server/package-lock.json` so the OAuth server resolves to a clean npm audit graph.
- Upgraded direct OAuth dependencies:
  - `bcrypt` from `^5.1.1` to `^6.0.0`
  - `express` from `^4.21.2` to `^4.22.2`
- `bcrypt@6.0.0` removes the old `@mapbox/node-pre-gyp` install path, which also removes the vulnerable transitive `tar`, `abbrev`, `chownr`, and legacy `brace-expansion` chain from the production install.
- `express@4.22.2` keeps the project on Express 4 while taking patched `body-parser@1.20.6` and `qs@6.15.3`.
- `oauth-server/Dockerfile` now copies `package-lock.json` and uses `npm ci --omit=dev`, so the production image installs exactly the reviewed lockfile graph.
- Public examples no longer include the real deployment Skill ID, internal SecondBrain host, or internal Home Assistant host.
- `deploy/unraid-broker/oauth-compose.yml` adds a Broker-specific OAuth deployment path with `secret://...` references and explicit Docker IPAM. This keeps local developer Compose unchanged while making Broker validation possible.
- `.github/workflows/ci.yml` now runs the documented Python tests, OAuth npm audit/syntax checks, and Docker image builds for `main`, `agent/**`, `codex/**`, and pull requests.

## Compatibility Notes

- Runtime remains Node `>=20.0.0`; the Docker image stays on `node:20-bookworm-slim`.
- `bcrypt@6.0.0` is compatible with this runtime line. Its upstream changelog notes that Node `<=16` support was dropped and `node-pre-gyp` was replaced with `prebuildify`.
- Express remains on the current major line. This avoids the Express 5 migration surface while still taking the latest safe Express 4 dependency graph.
- `jsonwebtoken` remains on npm `9.0.3`. The queue-listed `CVE-2026-25537` maps to the Rust crate maintained under `Keats/jsonwebtoken`, not the npm package used here. `npm audit --audit-level=low` reports zero vulnerabilities for the current npm graph.

## Secret Scanner Notes

- The code findings in `src/gateway/config.py`, `src/gateway/alexa/security.py`, `src/gateway/routing/classifier.py`, `src/gateway/services/orchestrator.py`, `src/gateway/api/routes.py`, `oauth-server/src/config.js`, `oauth-server/src/db.js`, and `oauth-server/src/services/oauthService.js` are variable names, redaction paths, or runtime token handling. No literal secret values were found in those files.
- The README and `.env.example` did include environment-specific production identifiers. Those are now placeholders.
- Real secret values must be supplied through local secret files, runtime environment variables, or the Unraid Deployment Broker secret flow. Do not commit `.env`, files under `secrets/`, literal OAuth URLs that contain credentials, or real deployment identifiers.

## Validation

Run from repository root:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/pytest
```

Run from `oauth-server/`:

```bash
npm ci
npm audit --audit-level=low
find src scripts -name '*.js' -print0 | xargs -0 -n1 node --check
```

Optional Node 20 parity syntax check from repository root when the local default Node version differs from the Docker/CI runtime:

```bash
find oauth-server/src oauth-server/scripts -name '*.js' -print0 | xargs -0 -n1 npx -y -p node@20 node --check
```

Container checks:

```bash
docker build -f docker/Dockerfile -t secondbrain-voice-gateway:local .
docker build -f oauth-server/Dockerfile -t secondbrain-voice-oauth:local oauth-server
docker compose config
docker compose -f oauth-server/docker-compose.yml config
```

Local Compose checks require a Docker CLI with the Compose plugin.

Broker preflight:

```text
stack_scan_repo -> deploy/unraid-broker/oauth-compose.yml must appear with the expected secret:// references.
stack_validate -> run against stack secondbrain-voice-oauth and the reviewed full commit SHA before deploy_plan.
```

Runtime health checks require the configured PostgreSQL database and OAuth environment variables:

```bash
curl http://localhost:3100/health
```
