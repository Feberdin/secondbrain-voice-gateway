/**
 * Purpose: Create one local email/password user for Alexa account linking tests or production setup.
 * Input/Output: Run this script with `--email` and `--password`; it inserts a bcrypt-hashed user.
 * Invariants: The script initializes the schema first and never stores plaintext passwords.
 * Debugging: If user creation fails, inspect DATABASE_URL and whether the email already exists.
 */

import { createUser } from "../src/services/userService.js";
import { closeDatabase, initializeDatabase } from "../src/db.js";

function readArgument(flag) {
  const index = process.argv.indexOf(flag);
  if (index === -1 || index + 1 >= process.argv.length) {
    return null;
  }
  return process.argv[index + 1];
}

async function main() {
  const email = readArgument("--email");
  const password = readArgument("--password");

  if (!email || !password) {
    console.error("Usage: npm run create-user -- --email user@example.com --password 'strong-password'");
    process.exit(1);
  }

  await initializeDatabase();
  const user = await createUser(email, password);
  console.log(`Created user ${user.email} with id ${user.id}`);
  await closeDatabase();
}

main().catch(async (error) => {
  console.error("Failed to create user:", error.message);
  await closeDatabase();
  process.exit(1);
});

