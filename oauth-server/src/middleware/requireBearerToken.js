/**
 * Purpose: Verify bearer access tokens and attach the authenticated user to the request.
 * Input/Output: Express middleware expects `Authorization: Bearer <token>` and populates `req.auth`.
 * Invariants: Only valid, unexpired JWT access tokens that still exist in the database are accepted.
 * Debugging: Use the `/me` route to test whether your token signature and database state match.
 */

import { verifyAccessToken } from "../services/oauthService.js";

export async function requireBearerToken(req, res, next) {
  const authorization = req.headers.authorization || "";
  const [scheme, token] = authorization.split(" ");

  if (scheme !== "Bearer" || !token) {
    res.status(401).json({
      error: "invalid_token",
      error_description: "Missing or invalid bearer token.",
    });
    return;
  }

  try {
    req.auth = await verifyAccessToken(token);
    next();
  } catch (error) {
    res.status(401).json({
      error: "invalid_token",
      error_description: error.message,
    });
  }
}

