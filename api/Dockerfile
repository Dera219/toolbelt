# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Python 3.12 rather than 3.14: several deps still ship no 3.14 wheels, and
# building them from source in a container is slow and fragile. Nothing in this
# codebase needs 3.13+ syntax.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change does not invalidate the wheel layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. The app writes only to the uploads dir.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations run before the server starts, so a deploy that changes the schema
# is a single atomic step. If a migration fails the container exits and the
# platform keeps the previous release serving.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
