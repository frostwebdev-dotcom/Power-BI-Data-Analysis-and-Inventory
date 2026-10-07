# =============================================================================
# Backend image — FastAPI + SQLAlchemy 2 + Alembic
# =============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Set to "true" to include dev tooling (ruff, mypy, pytest) in the image, which
# is what lets `docker compose run api pytest` work without a host Python.
ARG INSTALL_DEV=false

COPY pyproject.toml ./
COPY alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY tests ./tests

RUN python -m pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
    python -m pip install -e ".[dev]"; \
    else \
    python -m pip install .; \
    fi

# Run as a non-root user. The storage directories are created here so the
# container still starts when no bind mount is supplied.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --home-dir /app --no-create-home \
    --shell /usr/sbin/nologin appuser \
    && mkdir -p /storage/raw /storage/processed /storage/rejected \
    && chown -R appuser:appuser /app /storage
USER appuser

# Documentation only; the platform's $PORT wins at runtime.
EXPOSE 8000

# Liveness only — it must not depend on PostgreSQL, or a database outage would
# make Docker kill an otherwise healthy container.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, sys, urllib.request; port = os.environ.get('PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).status == 200 else 1)"

# Shell form on purpose. Exec form does not expand environment variables,
# and managed platforms (Railway, Render, Cloud Run, Container Apps) inject
# the port to listen on as $PORT. Hardcoding 8000 there means the platform
# routes traffic to a port nothing is bound to, and the service never
# becomes healthy.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
