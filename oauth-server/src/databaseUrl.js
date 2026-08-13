/**
 * Purpose: Build a PostgreSQL connection URL without duplicating the database password as a second secret.
 * Input/Output: Accepts an environment-like object and returns one PostgreSQL connection string.
 * Invariants: Host, port, and database name are validated before credentials are URL-encoded.
 * Debugging: Validation errors name the invalid setting but never echo its value.
 */

function requireSetting(name, environment) {
  const value = environment[name];
  if (!value || !value.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value.trim();
}

function validateDatabaseComponent(name, value, pattern) {
  if (!pattern.test(value)) {
    throw new Error(`Environment variable ${name} contains unsupported characters.`);
  }
  return value;
}

export function buildDatabaseUrl(environment = process.env) {
  /**
   * Why this exists: a complete PostgreSQL URL contains a password and should not be duplicated as a second secret.
   * What happens here: an explicit DATABASE_URL remains supported, otherwise the URL is built in memory from
   * reviewable connection settings plus DB_PASSWORD.
   * Example input/output:
   * - Input: DB_HOST=postgres, DB_USER=oauth_user, DB_PASSWORD=<secret>, DB_NAME=secondbrain_oauth
   * - Output: postgresql://oauth_user:<encoded-secret>@postgres:5432/secondbrain_oauth
   */
  const explicitUrl = environment.DATABASE_URL?.trim();
  if (explicitUrl) {
    return explicitUrl;
  }

  const host = validateDatabaseComponent(
    "DB_HOST",
    (environment.DB_HOST || "postgres").trim(),
    /^[A-Za-z0-9.-]+$/,
  );
  const port = (environment.DB_PORT || "5432").trim();
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    throw new Error("Environment variable DB_PORT must be an integer between 1 and 65535.");
  }

  const user = requireSetting("DB_USER", environment);
  const password = requireSetting("DB_PASSWORD", environment);
  const database = validateDatabaseComponent(
    "DB_NAME",
    (environment.DB_NAME || "secondbrain_oauth").trim(),
    /^[A-Za-z0-9_.-]+$/,
  );

  return [
    "postgresql://",
    encodeURIComponent(user),
    ":",
    encodeURIComponent(password),
    "@",
    host,
    ":",
    port,
    "/",
    encodeURIComponent(database),
  ].join("");
}
