/**
 * Purpose: Provide PostgreSQL access helpers plus idempotent schema and bootstrap initialization.
 * Input/Output: Other modules call `query`, `transaction`, and `initializeDatabase`.
 * Invariants: Startup keeps the schema current, seeds the Alexa client, and can optionally bootstrap one admin user.
 * Debugging: Database connectivity or SQL errors surface here first during service startup.
 */

import fs from "node:fs/promises";
import { randomUUID } from "node:crypto";

import bcrypt from "bcrypt";
import { Pool } from "pg";

import config from "./config.js";

const pool = new Pool({
  connectionString: config.databaseUrl,
});

function normalizeEmail(email) {
  return email.trim().toLowerCase();
}

export async function query(text, params = []) {
  return pool.query(text, params);
}

export async function transaction(callback) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await callback(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

async function runSchema() {
  const schemaSql = await fs.readFile(new URL("../db/init.sql", import.meta.url), "utf8");
  await pool.query(schemaSql);
}

async function seedOAuthClient() {
  const primaryRedirectUri = config.allowedRedirectUris[0];
  await query(
    `
      INSERT INTO oauth_clients (id, client_id, client_secret, redirect_uri, scopes)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (client_id)
      DO UPDATE SET
        client_secret = EXCLUDED.client_secret,
        redirect_uri = EXCLUDED.redirect_uri,
        scopes = EXCLUDED.scopes
    `,
    [randomUUID(), config.clientId, config.clientSecret, primaryRedirectUri, [config.defaultScope]],
  );
}

async function seedBootstrapUser() {
  if (!config.bootstrapUserEmail || !config.bootstrapUserPassword) {
    return;
  }

  const existing = await query("SELECT id FROM users WHERE email = $1", [normalizeEmail(config.bootstrapUserEmail)]);
  if (existing.rowCount > 0) {
    return;
  }

  const passwordHash = await bcrypt.hash(config.bootstrapUserPassword, config.bcryptRounds);
  await query("INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)", [
    randomUUID(),
    normalizeEmail(config.bootstrapUserEmail),
    passwordHash,
  ]);
}

export async function initializeDatabase() {
  await runSchema();
  await seedOAuthClient();
  await seedBootstrapUser();
}

export async function closeDatabase() {
  await pool.end();
}
