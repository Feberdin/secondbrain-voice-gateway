/**
 * Purpose: Enforce HTTPS for OAuth endpoints when running behind Cloudflare or another trusted proxy.
 * Input/Output: Express middleware blocks insecure requests with a clear error response.
 * Invariants: Production deployments should never process OAuth credentials over plain HTTP.
 * Debugging: If valid proxied HTTPS requests are rejected, verify `TRUST_PROXY=true` and forwarded headers.
 */

import config from "../config.js";

export function requireHttps(req, res, next) {
  if (!config.requireHttps) {
    next();
    return;
  }

  if (req.secure || req.headers["x-forwarded-proto"] === "https") {
    next();
    return;
  }

  res.status(400).json({
    error: "invalid_request",
    error_description: "HTTPS is required for this endpoint.",
  });
}

