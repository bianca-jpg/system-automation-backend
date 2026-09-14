# syntax=docker/dockerfile:1.4
FROM python:3.13-slim

ARG UID=1000
ARG GID=1000
ARG INSTALL_DEV=false

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/workspace/.cache/uv \
    PATH="/workspace/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${GID}" appuser && \
    useradd --uid "${UID}" --gid "${GID}" --no-create-home --shell /bin/bash appuser

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/workspace/.cache/uv \
    if [ "${INSTALL_DEV}" = "true" ]; then \
      uv sync --frozen --group dev; \
    else \
      uv sync --frozen --no-dev; \
    fi && \
    chown -R appuser:appuser /workspace

COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic/ ./alembic/

USER appuser

EXPOSE 8000

# Default de execução (usado pelo ECS, que não passa `command` na task definition).
# O Alembic usa advisory lock no PostgreSQL para serializar tasks concorrentes.
# `exec` torna o Uvicorn PID 1 depois do upgrade, preservando shutdown graceful.
# --proxy-headers/--forwarded-allow-ips: request.url respeita o scheme do ALB.
# CIDRs = subnets privadas da VPC do ECS (confirmados pela mantenedora em
# 2026-08-31); uvicorn>=0.29 aceita notação CIDR nativamente. Configurável via
# UVICORN_FORWARDED_ALLOW_IPS (env do task definition) sem precisar rebuild de
# imagem, caso a VPC mude.
CMD ["sh", "-c", \
     "alembic upgrade head && exec uvicorn app.main:app \
     --host 0.0.0.0 --port 8000 \
     --proxy-headers --forwarded-allow-ips=\"${UVICORN_FORWARDED_ALLOW_IPS:-30.1.1.0/24,30.1.11.0/24,30.1.21.0/24}\""]

