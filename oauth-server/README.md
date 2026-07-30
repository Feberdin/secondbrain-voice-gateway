# SecondBrain Voice OAuth Server

Purpose: Document the optional OAuth2 authorization server for Alexa account linking.
Input/Output: Operators read this before local or Broker deployment; the server exposes authorize, login, token, and protected test endpoints.
Invariants: Client secrets, JWT secrets, database passwords, bootstrap credentials, and real public hostnames stay outside Git.
Debugging: Start with `/health`, then inspect config validation errors and OAuth server logs.

Minimal OAuth2 authorization server for Alexa Skill Account Linking using the Authorization Code Grant with PKCE.

Important scope note:

- This OAuth server works for Alexa account linking.
- The current `secondbrain-voice-gateway` uses an Alexa `Custom Skill`.
- If you want phrases like “Alexa, ask Second Brain ...”, keep using a Custom Skill and add account linking there.
- A `Smart Home` skill would need a different directive-based backend than the one in this repository.

## Folder Structure

```text
oauth-server/
├── db/
│   └── init.sql
├── scripts/
│   └── create-user.js
├── src/
│   ├── middleware/
│   │   ├── requireBearerToken.js
│   │   └── requireHttps.js
│   ├── services/
│   │   ├── oauthService.js
│   │   └── userService.js
│   ├── utils/
│   │   ├── html.js
│   │   └── oauth.js
│   ├── views/
│   │   └── login.html
│   ├── app.js
│   ├── config.js
│   ├── db.js
│   └── server.js
├── .dockerignore
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── package.json
└── README.md
```

## What It Does

- `GET /oauth/authorize`
  - validates Alexa OAuth parameters
  - shows a minimal email/password login form
  - stores a short-lived authorization code with PKCE challenge
- `POST /login`
  - authenticates the user with email/password
  - redirects back to Alexa with `state` and `code`
- `POST /oauth/token`
  - exchanges authorization codes for JWT access/refresh tokens
  - supports refresh-token rotation
- `GET /me`
  - example protected route using bearer-token middleware

## Deploy

1. Copy `.env.example` to `.env`
2. Replace:
   - `CLIENT_SECRET`
   - `JWT_SECRET`
   - `DB_PASSWORD`
   - `BOOTSTRAP_USER_EMAIL`
   - `BOOTSTRAP_USER_PASSWORD`
3. Start the stack:

```bash
cd oauth-server
cp .env.example .env
docker compose up -d --build
```

4. Test health:

```bash
curl http://localhost:3100/health
```

5. Point your reverse proxy so:

- `/oauth/authorize`, `/login`, `/oauth/token`, and `/me` -> this OAuth container
- `/alexa/skill` and all remaining gateway paths -> the voice gateway container

Path-specific rules must appear before the hostname-wide gateway rule. Otherwise Alexa reaches the
FastAPI gateway for `/oauth/authorize` and receives `{"detail":"Not Found"}`.

Because Cloudflare terminates TLS in front of your origin, leave:

- `TRUST_PROXY=true`
- `REQUIRE_HTTPS=true`

## Create an Additional User

```bash
cd oauth-server
docker compose exec oauth-server npm run create-user -- --email user@example.com --password 'very-strong-password'
```

## Alexa Developer Console Values

Authorization URI:

`https://voice-gateway.example.com/oauth/authorize`

Access Token URI:

`https://voice-gateway.example.com/oauth/token`

Client ID:

`alexa-secondbrain`

Client Secret:

Use the value of `CLIENT_SECRET` from your `.env`

Scope:

`secondbrain.voice`

Domain:

`voice-gateway.example.com`

Token Expiration:

`3600`

Only users that already exist in the OAuth database can link the skill. The service has no public
registration endpoint. For a single-user installation, keep only your bootstrap account and disable
Alexa's "Allow users to enable skill without account linking" option.

## Curl Example For Token Exchange

This simulates Alexa exchanging an authorization code:

```bash
curl -X POST https://voice-gateway.example.com/oauth/token \
  -u 'alexa-secondbrain:YOUR_CLIENT_SECRET' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode 'code=AUTH_CODE_FROM_AUTHORIZE_REDIRECT' \
  --data-urlencode 'redirect_uri=https://layla.amazon.com/api/skill/link/M28MIEWY5KGX14' \
  --data-urlencode 'code_verifier=YOUR_PKCE_CODE_VERIFIER'
```

Refresh-token example:

```bash
curl -X POST https://voice-gateway.example.com/oauth/token \
  -u 'alexa-secondbrain:YOUR_CLIENT_SECRET' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=refresh_token' \
  --data-urlencode 'refresh_token=YOUR_REFRESH_TOKEN'
```

## Security Notes

- Redirect URIs are exact-match validated against the Alexa redirect list from your skill.
- PKCE supports `S256` only.
- Access tokens and refresh tokens are signed JWTs.
- Passwords are stored with bcrypt.
- The service rejects non-HTTPS requests when `REQUIRE_HTTPS=true`.
