/**
 * Purpose: Start the Express OAuth2 server after the database schema and seed data are ready.
 * Input/Output: Initializes the database and begins listening on the configured port.
 * Invariants: The service refuses to start if config or database setup is invalid.
 * Debugging: Startup failures are printed once and terminate the process to avoid half-ready containers.
 */

import app from "./app.js";
import config from "./config.js";
import { closeDatabase, initializeDatabase } from "./db.js";

async function main() {
  await initializeDatabase();

  const server = app.listen(config.port, () => {
    console.log(`SecondBrain Voice OAuth listening on port ${config.port}`);
  });

  const shutdown = async () => {
    server.close(async () => {
      await closeDatabase();
      process.exit(0);
    });
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((error) => {
  console.error("Failed to start OAuth server:", error);
  process.exit(1);
});

