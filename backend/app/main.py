"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health as liveness
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.imports.storage import build_storage_backend


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log startup and shutdown, and run the Amazon scheduler when enabled.

    No database connection is opened here: connecting at startup would make
    the API refuse to boot whenever PostgreSQL is briefly unavailable, when
    reporting the problem through the readiness probe is far more useful.

    The scheduler starts only when ``AMAZON_ENABLED`` is true and never under
    ``APP_ENV=test``. It is in-process, so it belongs in exactly one process;
    a deployment with several API replicas moves it to the worker.
    """
    settings: Settings = app.state.settings
    logger = get_logger(__name__)
    logger.info(
        "app.startup",
        app_name=settings.app_name,
        environment=settings.app_env,
        api_prefix=settings.api_v1_prefix,
    )
    runner = None
    if settings.amazon_enabled and settings.app_env.strip().lower() != "test":
        from app.jobs.amazon import build_amazon_runner

        runner = build_amazon_runner(settings)
        runner.start()
        app.state.job_runner = runner
        logger.info("app.scheduler_started")
    try:
        yield
    finally:
        if runner is not None:
            runner.shutdown()
            logger.info("app.scheduler_stopped")
        logger.info("app.shutdown", app_name=settings.app_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the application.

    A factory rather than a module-level singleton so tests can build an app
    against a different configuration.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Purchasing & Replenishment Management System",
        description=("Vendor catalogue, inventory imports, and deterministic product matching."),
        version="0.1.0",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        # Must be set explicitly. It does not follow docs_url, so leaving it at
        # the default puts an unversioned /docs/oauth2-redirect route on the app.
        swagger_ui_oauth2_redirect_url=f"{settings.api_v1_prefix}/docs/oauth2-redirect",
        lifespan=lifespan,
    )

    # Published so route dependencies read the settings this app was built
    # with, rather than the process-wide cache (see api/deps.get_app_settings).
    app.state.settings = settings
    # Retained-file storage (ADR 0004). Built once here so every route uses the
    # backend this app was configured with; nothing is created on disk until
    # the first upload.
    app.state.storage = build_storage_backend(settings)

    # Centralised error handling: one envelope for every failure, and no
    # stack trace ever reaches a client in production (app/core/errors.py).
    register_exception_handlers(app, settings)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Unversioned liveness probe — the sole route outside /api/v1.
    app.include_router(liveness.router)
    # Everything else is versioned.
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
