/**
 * Purpose: Small OAuth and crypto helpers shared across routes and services.
 * Input/Output: Exposes base64url SHA-256, random token creation, and safe redirect URL building.
 * Invariants: PKCE verification uses S256 only, and redirect URL query values are appended safely.
 * Debugging: If PKCE validation fails, compare the stored challenge with the output of `pkceChallengeFromVerifier`.
 */

import { createHash, randomBytes } from "node:crypto";

export function generateOpaqueToken(byteLength = 32) {
  return randomBytes(byteLength).toString("base64url");
}

export function pkceChallengeFromVerifier(verifier) {
  return createHash("sha256").update(verifier, "utf8").digest("base64url");
}

export function buildRedirectUrl(baseUrl, params) {
  const url = new URL(baseUrl);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      url.searchParams.set(key, String(value));
    }
  });
  return url.toString();
}

