# Purpose: Build the minimal OAuth2 authorization server for Alexa account linking.
# Input/Output: Docker uses this file to install production dependencies and run the Express app.
# Invariants: The image runs as the non-root node user and expects configuration via environment variables.
# Debugging: Rebuild with `docker compose build --no-cache` if dependency resolution or startup imports fail.

FROM node:20-bookworm-slim

ENV NODE_ENV=production

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY . ./

USER node

EXPOSE 3000

CMD ["npm", "start"]

