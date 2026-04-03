/**
 * Purpose: Render the minimal login page without bringing in a full templating engine.
 * Input/Output: Loads the HTML template and substitutes hidden inputs, titles, and error messages.
 * Invariants: User-controlled values are HTML-escaped before being inserted into the page.
 * Debugging: If the login page renders broken markup, inspect the hidden OAuth form values first.
 */

import fs from "node:fs/promises";

let templateCache = null;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function getTemplate() {
  if (!templateCache) {
    templateCache = await fs.readFile(new URL("../views/login.html", import.meta.url), "utf8");
  }
  return templateCache;
}

export async function renderLoginPage({ title, description, error, oauthParams, email = "" }) {
  const template = await getTemplate();
  const hiddenInputs = Object.entries(oauthParams)
    .map(
      ([key, value]) =>
        `<input type="hidden" name="${escapeHtml(key)}" value="${escapeHtml(value ?? "")}">`,
    )
    .join("\n");

  const errorBlock = error
    ? `<div class="error" role="alert">${escapeHtml(error)}</div>`
    : "";

  return template
    .replaceAll("{{title}}", escapeHtml(title))
    .replaceAll("{{description}}", escapeHtml(description))
    .replaceAll("{{hidden_inputs}}", hiddenInputs)
    .replaceAll("{{error_block}}", errorBlock)
    .replaceAll("{{email_value}}", escapeHtml(email));
}

