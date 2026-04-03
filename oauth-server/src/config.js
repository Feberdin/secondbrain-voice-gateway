/**
 * Purpose: Centralize runtime configuration and validate the required environment variables.
 * Input/Output: Reads process environment variables and exports one typed configuration object.
 * Invariants: HTTPS is enforced in production, allowed redirect URIs are explicit, and secrets must be non-empty.
 * Debugging: If the server fails on startup, read the thrown config validation message first.
 */

import { config as loadDotenv } from "dotenv";

loadDotenv();

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value.trim();
}

function parseInteger(name, fallback) {
  const raw = process.env[name];
  if (!raw) {
    return fallback;
  }

  const parsed = Number.parseInt(raw, 10);
  if (Number.isNaN(parsed) || parsed <= 0) {
    throw new Error(`Environment variable ${name} must be a positive integer.`);
  }
  return parsed;
}

function parseBoolean(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined) {
    return fallback;
  }
  return raw.toLowerCase() === "true";
}

function parseCsv(name) {
  const raw = requireEnv(name);
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

const config = {
  nodeEnv: process.env.NODE_ENV || "development",
  port: parseInteger("PORT", 3000),
  publicBaseUrl: requireEnv("PUBLIC_BASE_URL"),
  trustProxy: parseBoolean("TRUST_PROXY", true),
  requireHttps: parseBoolean("REQUIRE_HTTPS", true),
  databaseUrl: requireEnv("DATABASE_URL"),
  clientId: requireEnv("CLIENT_ID"),
  clientSecret: requireEnv("CLIENT_SECRET"),
  defaultScope: process.env.DEFAULT_SCOPE || "secondbrain.voice",
  allowedRedirectUris: parseCsv("ALLOWED_REDIRECT_URIS"),
  jwtSecret: requireEnv("JWT_SECRET"),
  jwtIssuer: process.env.JWT_ISSUER || "secondbrain-voice",
  jwtAudience: process.env.JWT_AUDIENCE || "secondbrain-voice",
  accessTokenTtlSeconds: parseInteger("ACCESS_TOKEN_TTL_SECONDS", 3600),
  refreshTokenTtlSeconds: parseInteger("REFRESH_TOKEN_TTL_SECONDS", 2_592_000),
  authCodeTtlSeconds: parseInteger("AUTH_CODE_TTL_SECONDS", 300),
  bcryptRounds: parseInteger("BCRYPT_ROUNDS", 12),
  bootstrapUserEmail: process.env.BOOTSTRAP_USER_EMAIL?.trim() || null,
  bootstrapUserPassword: process.env.BOOTSTRAP_USER_PASSWORD?.trim() || null,
};

if (config.requireHttps && !config.publicBaseUrl.startsWith("https://")) {
  throw new Error("PUBLIC_BASE_URL must use https:// when REQUIRE_HTTPS=true.");
}

export default config;

