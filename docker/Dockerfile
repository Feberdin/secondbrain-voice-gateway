# Purpose: Build a small production container for the SecondBrain voice gateway.
# Input/Output: Docker uses this file to install the package and run Uvicorn.
# Invariants: The container runs as a non-root user and expects config via env vars and mounted files.
# Debugging: Rebuild with `docker compose build --no-cache` if dependency or import issues appear.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY configs /app/configs

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

USER appuser

EXPOSE 8000

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

