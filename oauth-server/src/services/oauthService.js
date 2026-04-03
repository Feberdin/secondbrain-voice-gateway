/**
 * Purpose: Implement the core OAuth2 authorization-code and refresh-token logic for Alexa account linking.
 * Input/Output: Routes call this service to validate clients, create auth codes, mint tokens, and verify bearer tokens.
 * Invariants: Redirect URIs are exact-match validated, PKCE uses S256, and authorization codes are single-use.
 * Debugging: Most account-linking failures can be traced here by checking redirect URI, PKCE, and client credential validation.
 */

import jwt from "jsonwebtoken";

import config from "../config.js";
import { query, transaction } from "../db.js";
import { buildRedirectUrl, generateOpaqueToken, pkceChallengeFromVerifier } from "../utils/oauth.js";
import { getUserById } from "./userService.js";

function normalizeScope(scope) {
  return scope || config.defaultScope;
}

function ensureHttpsRedirectUri(redirectUri) {
  const url = new URL(redirectUri);
  if (url.protocol !== "https:") {
    throw new Error("redirect_uri must use HTTPS.");
  }
}

export async function getClientById(clientId) {
  const result = await query(
    "SELECT id, client_id, client_secret, redirect_uri, scopes FROM oauth_clients WHERE client_id = $1",
    [clientId],
  );
  return result.rows[0] || null;
}

export function validateRedirectUriOrThrow(redirectUri) {
  ensureHttpsRedirectUri(redirectUri);
  if (!config.allowedRedirectUris.includes(redirectUri)) {
    throw new Error("redirect_uri is not in the allowed Alexa redirect URI list.");
  }
}

export function validateAuthorizeParamsOrThrow(params) {
  if (params.response_type !== "code") {
    throw new Error("response_type must be code.");
  }
  if (!params.client_id) {
    throw new Error("client_id is required.");
  }
  if (!params.redirect_uri) {
    throw new Error("redirect_uri is required.");
  }
  if (!params.state) {
    throw new Error("state is required.");
  }
  if (!params.code_challenge) {
    throw new Error("code_challenge is required.");
  }
  if (params.code_challenge_method !== "S256") {
    throw new Error("code_challenge_method must be S256.");
  }
}

export async function createAuthorizationCode({
  clientId,
  userId,
  redirectUri,
  codeChallenge,
}) {
  const code = generateOpaqueToken(32);
  const expiresAt = new Date(Date.now() + config.authCodeTtlSeconds * 1000);

  await query(
    `
      INSERT INTO oauth_codes (code, client_id, user_id, redirect_uri, code_challenge, expires_at)
      VALUES ($1, $2, $3, $4, $5, $6)
    `,
    [code, clientId, userId, redirectUri, codeChallenge, expiresAt],
  );

  return code;
}

function signJwtToken(payload, expiresInSeconds) {
  return jwt.sign(payload, config.jwtSecret, {
    issuer: config.jwtIssuer,
    audience: config.jwtAudience,
    expiresIn: expiresInSeconds,
  });
}

async function storeTokenPair(executor, client, userId, scope, accessToken, refreshToken) {
  const accessExpiresAt = new Date(Date.now() + config.accessTokenTtlSeconds * 1000);
  const refreshExpiresAt = new Date(Date.now() + config.refreshTokenTtlSeconds * 1000);

  await executor.query(
    `
      INSERT INTO oauth_tokens (
        access_token,
        refresh_token,
        client_id,
        user_id,
        scope,
        expires_at,
        refresh_expires_at
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7)
    `,
    [accessToken, refreshToken, client.client_id, userId, scope, accessExpiresAt, refreshExpiresAt],
  );
}

async function mintTokenPair({ executor, client, userId, scope }) {
  const resolvedScope = normalizeScope(scope);
  const accessToken = signJwtToken(
    {
      sub: userId,
      client_id: client.client_id,
      scope: resolvedScope,
      type: "access",
    },
    config.accessTokenTtlSeconds,
  );
  const refreshToken = signJwtToken(
    {
      sub: userId,
      client_id: client.client_id,
      scope: resolvedScope,
      type: "refresh",
    },
    config.refreshTokenTtlSeconds,
  );

  await storeTokenPair(executor, client, userId, resolvedScope, accessToken, refreshToken);

  return {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: "Bearer",
    expires_in: config.accessTokenTtlSeconds,
    scope: resolvedScope,
  };
}

export async function exchangeAuthorizationCode({
  client,
  code,
  redirectUri,
  codeVerifier,
}) {
  validateRedirectUriOrThrow(redirectUri);

  return transaction(async (dbClient) => {
    const codeResult = await dbClient.query(
      `
        SELECT code, client_id, user_id, redirect_uri, code_challenge, expires_at
        FROM oauth_codes
        WHERE code = $1
        FOR UPDATE
      `,
      [code],
    );

    if (codeResult.rowCount === 0) {
      throw new Error("authorization_code is invalid.");
    }

    const row = codeResult.rows[0];
    if (row.client_id !== client.client_id) {
      throw new Error("authorization_code does not belong to this client.");
    }
    if (row.redirect_uri !== redirectUri) {
      throw new Error("redirect_uri does not match the original authorization request.");
    }
    if (new Date(row.expires_at).getTime() <= Date.now()) {
      throw new Error("authorization_code has expired.");
    }

    const expectedChallenge = pkceChallengeFromVerifier(codeVerifier);
    if (expectedChallenge !== row.code_challenge) {
      throw new Error("code_verifier failed PKCE validation.");
    }

    await dbClient.query("DELETE FROM oauth_codes WHERE code = $1", [code]);
    return mintTokenPair({
      executor: dbClient,
      client,
      userId: row.user_id,
      scope: client.scopes?.[0] || config.defaultScope,
    });
  });
}

export async function refreshAccessToken({ client, refreshToken }) {
  const decoded = jwt.verify(refreshToken, config.jwtSecret, {
    issuer: config.jwtIssuer,
    audience: config.jwtAudience,
  });

  if (decoded.type !== "refresh") {
    throw new Error("refresh_token is not a refresh token.");
  }
  if (decoded.client_id !== client.client_id) {
    throw new Error("refresh_token does not belong to this client.");
  }

  return transaction(async (dbClient) => {
    const result = await dbClient.query(
      `
        SELECT refresh_token, user_id, client_id, scope, refresh_expires_at
        FROM oauth_tokens
        WHERE refresh_token = $1
        FOR UPDATE
      `,
      [refreshToken],
    );

    if (result.rowCount === 0) {
      throw new Error("refresh_token is invalid.");
    }

    const row = result.rows[0];
    if (row.client_id !== client.client_id) {
      throw new Error("refresh_token does not belong to this client.");
    }
    if (new Date(row.refresh_expires_at).getTime() <= Date.now()) {
      throw new Error("refresh_token has expired.");
    }

    await dbClient.query("DELETE FROM oauth_tokens WHERE refresh_token = $1", [refreshToken]);
    return mintTokenPair({
      executor: dbClient,
      client,
      userId: row.user_id,
      scope: row.scope,
    });
  });
}

export function buildAuthorizationRedirect({ redirectUri, state, code }) {
  return buildRedirectUrl(redirectUri, { state, code });
}

export async function verifyAccessToken(accessToken) {
  const decoded = jwt.verify(accessToken, config.jwtSecret, {
    issuer: config.jwtIssuer,
    audience: config.jwtAudience,
  });

  if (decoded.type !== "access") {
    throw new Error("access_token is not an access token.");
  }

  const tokenResult = await query(
    `
      SELECT access_token, client_id, user_id, scope, expires_at
      FROM oauth_tokens
      WHERE access_token = $1
    `,
    [accessToken],
  );

  if (tokenResult.rowCount === 0) {
    throw new Error("access_token was not found.");
  }

  const tokenRow = tokenResult.rows[0];
  if (new Date(tokenRow.expires_at).getTime() <= Date.now()) {
    throw new Error("access_token has expired.");
  }

  const user = await getUserById(tokenRow.user_id);
  if (!user) {
    throw new Error("User for access_token no longer exists.");
  }

  return {
    user,
    clientId: tokenRow.client_id,
    scope: tokenRow.scope,
    jwtPayload: decoded,
  };
}
