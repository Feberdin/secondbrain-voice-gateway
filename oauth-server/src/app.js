/**
 * Purpose: Wire together Express middleware, OAuth routes, and the protected sample endpoint.
 * Input/Output: Exports the configured Express app used by `src/server.js`.
 * Invariants: Authorization uses PKCE S256, token issuance requires valid client credentials, and responses are OAuth-compatible.
 * Debugging: If Alexa linking fails, test `/oauth/authorize` in a browser and `/oauth/token` with curl before checking Alexa again.
 */

import express from "express";
import helmet from "helmet";
import rateLimit from "express-rate-limit";

import config from "./config.js";
import { requireBearerToken } from "./middleware/requireBearerToken.js";
import { requireHttps } from "./middleware/requireHttps.js";
import {
  buildAuthorizationRedirect,
  createAuthorizationCode,
  exchangeAuthorizationCode,
  getClientById,
  refreshAccessToken,
  validateAuthorizeParamsOrThrow,
  validateRedirectUriOrThrow,
} from "./services/oauthService.js";
import { authenticateUser } from "./services/userService.js";
import { renderLoginPage } from "./utils/html.js";

const app = express();
app.disable("x-powered-by");
if (config.trustProxy) {
  app.set("trust proxy", 1);
}

app.use(
  helmet({
    contentSecurityPolicy: false,
  }),
);
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 20,
  standardHeaders: true,
  legacyHeaders: false,
});

const tokenLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 60,
  standardHeaders: true,
  legacyHeaders: false,
});

function oauthError(res, status, error, errorDescription) {
  res.status(status).json({
    error,
    error_description: errorDescription,
  });
}

function extractClientCredentials(req) {
  const authorization = req.headers.authorization || "";
  if (authorization.startsWith("Basic ")) {
    const decoded = Buffer.from(authorization.slice("Basic ".length), "base64").toString("utf8");
    const separatorIndex = decoded.indexOf(":");
    if (separatorIndex >= 0) {
      return {
        clientId: decoded.slice(0, separatorIndex),
        clientSecret: decoded.slice(separatorIndex + 1),
      };
    }
  }

  return {
    clientId: req.body.client_id,
    clientSecret: req.body.client_secret,
  };
}

function collectOAuthParams(source) {
  return {
    response_type: source.response_type || "",
    client_id: source.client_id || "",
    redirect_uri: source.redirect_uri || "",
    state: source.state || "",
    code_challenge: source.code_challenge || "",
    code_challenge_method: source.code_challenge_method || "",
  };
}

async function renderLogin(res, oauthParams, errorMessage, emailValue = "") {
  const html = await renderLoginPage({
    title: "SecondBrain Voice Sign In",
    description: "Sign in to link your SecondBrain Voice account with Alexa.",
    error: errorMessage,
    oauthParams,
    email: emailValue,
  });
  res.status(200).type("html").send(html);
}

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.get("/oauth/authorize", requireHttps, async (req, res) => {
  const oauthParams = collectOAuthParams(req.query);

  try {
    validateAuthorizeParamsOrThrow(oauthParams);
    validateRedirectUriOrThrow(oauthParams.redirect_uri);

    const client = await getClientById(oauthParams.client_id);
    if (!client) {
      throw new Error("client_id is unknown.");
    }

    await renderLogin(res, oauthParams, null);
  } catch (error) {
    res.status(400).type("html").send(`<pre>${error.message}</pre>`);
  }
});

app.post("/login", requireHttps, loginLimiter, async (req, res) => {
  const oauthParams = collectOAuthParams(req.body);
  const email = String(req.body.email || "").trim();
  const password = String(req.body.password || "");

  try {
    validateAuthorizeParamsOrThrow(oauthParams);
    validateRedirectUriOrThrow(oauthParams.redirect_uri);

    const client = await getClientById(oauthParams.client_id);
    if (!client) {
      throw new Error("client_id is unknown.");
    }

    const user = await authenticateUser(email, password);
    if (!user) {
      await renderLogin(res, oauthParams, "Invalid email or password.", email);
      return;
    }

    const authorizationCode = await createAuthorizationCode({
      clientId: client.client_id,
      userId: user.id,
      redirectUri: oauthParams.redirect_uri,
      codeChallenge: oauthParams.code_challenge,
    });

    res.redirect(
      302,
      buildAuthorizationRedirect({
        redirectUri: oauthParams.redirect_uri,
        state: oauthParams.state,
        code: authorizationCode,
      }),
    );
  } catch (error) {
    await renderLogin(res, oauthParams, error.message, email);
  }
});

app.post("/oauth/token", requireHttps, tokenLimiter, async (req, res) => {
  const { clientId, clientSecret } = extractClientCredentials(req);
  if (!clientId || !clientSecret) {
    oauthError(res, 401, "invalid_client", "Missing client credentials.");
    return;
  }

  const client = await getClientById(clientId);
  if (!client || client.client_secret !== clientSecret) {
    oauthError(res, 401, "invalid_client", "Client authentication failed.");
    return;
  }

  try {
    if (req.body.grant_type === "authorization_code") {
      if (!req.body.code || !req.body.redirect_uri || !req.body.code_verifier) {
        throw new Error("grant_type=authorization_code requires code, redirect_uri, and code_verifier.");
      }

      const tokenResponse = await exchangeAuthorizationCode({
        client,
        code: req.body.code,
        redirectUri: req.body.redirect_uri,
        codeVerifier: req.body.code_verifier,
      });

      res.json(tokenResponse);
      return;
    }

    if (req.body.grant_type === "refresh_token") {
      if (!req.body.refresh_token) {
        throw new Error("grant_type=refresh_token requires refresh_token.");
      }

      const tokenResponse = await refreshAccessToken({
        client,
        refreshToken: req.body.refresh_token,
      });

      res.json(tokenResponse);
      return;
    }

    throw new Error("Unsupported grant_type.");
  } catch (error) {
    oauthError(res, 400, "invalid_grant", error.message);
  }
});

app.get("/me", requireHttps, requireBearerToken, (req, res) => {
  res.json({
    user: req.auth.user,
    client_id: req.auth.clientId,
    scope: req.auth.scope,
  });
});

export default app;

