/**
 * Purpose: Verify OAuth runtime configuration without connecting to PostgreSQL or reading production secrets.
 * Input/Output: Builds config from synthetic environment objects and checks resulting safe connection behavior.
 * Invariants: Secret values are encoded in memory and are never included in validation errors.
 * Debugging: Run `npm test` from oauth-server/ and inspect the first failed assertion.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { buildDatabaseUrl } from "../src/databaseUrl.js";

test("uses an explicit DATABASE_URL for backwards compatibility", () => {
  const databaseUrl = "postgresql://legacy:encoded@database.example:5432/oauth";

  assert.equal(buildDatabaseUrl({ DATABASE_URL: databaseUrl }), databaseUrl);
});

test("builds the PostgreSQL URL from safe components and encodes credentials", () => {
  const databaseUrl = buildDatabaseUrl({
    DB_HOST: "postgres",
    DB_PORT: "5432",
    DB_NAME: "secondbrain_oauth",
    DB_USER: "oauth_user",
    DB_PASSWORD: "p@ss:word/with spaces",
  });

  assert.equal(
    databaseUrl,
    "postgresql://oauth_user:p%40ss%3Aword%2Fwith%20spaces@postgres:5432/secondbrain_oauth",
  );
});

test("rejects an unsafe database host without echoing the supplied value", () => {
  const unsafeHost = "postgres/path?token=decoy";

  assert.throws(
    () =>
      buildDatabaseUrl({
        DB_HOST: unsafeHost,
        DB_USER: "oauth_user",
        DB_PASSWORD: "decoy-password",
      }),
    (error) => {
      assert.match(error.message, /DB_HOST contains unsupported characters/);
      assert.doesNotMatch(error.message, /token|decoy-password/);
      return true;
    },
  );
});

test("requires DB_PASSWORD when no complete DATABASE_URL is supplied", () => {
  assert.throws(
    () =>
      buildDatabaseUrl({
        DB_HOST: "postgres",
        DB_USER: "oauth_user",
      }),
    /Missing required environment variable: DB_PASSWORD/,
  );
});
