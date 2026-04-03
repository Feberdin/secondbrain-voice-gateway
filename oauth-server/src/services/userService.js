/**
 * Purpose: Manage user lookup, password verification, and user creation.
 * Input/Output: Other services authenticate users by email and password through this module.
 * Invariants: Emails are normalized to lowercase and passwords are always stored as bcrypt hashes.
 * Debugging: If login fails unexpectedly, verify the stored `password_hash` and the normalized email.
 */

import bcrypt from "bcrypt";
import { randomUUID } from "node:crypto";

import config from "../config.js";
import { query } from "../db.js";

function normalizeEmail(email) {
  return email.trim().toLowerCase();
}

export async function authenticateUser(email, password) {
  const normalizedEmail = normalizeEmail(email);
  const result = await query("SELECT id, email, password_hash FROM users WHERE email = $1", [normalizedEmail]);
  if (result.rowCount === 0) {
    return null;
  }

  const user = result.rows[0];
  const passwordMatches = await bcrypt.compare(password, user.password_hash);
  if (!passwordMatches) {
    return null;
  }

  return {
    id: user.id,
    email: user.email,
  };
}

export async function createUser(email, password) {
  const normalizedEmail = normalizeEmail(email);
  const passwordHash = await bcrypt.hash(password, config.bcryptRounds);
  const result = await query(
    "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3) RETURNING id, email",
    [randomUUID(), normalizedEmail, passwordHash],
  );
  return result.rows[0];
}

export async function getUserById(userId) {
  const result = await query("SELECT id, email FROM users WHERE id = $1", [userId]);
  return result.rows[0] || null;
}
