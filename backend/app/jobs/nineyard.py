"""Scheduled read-only Nineyard catalog synchronization."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.transaction import session_scope
from app.integrations.nineyard import NineyardCatalogReader, NineyardClient, NineyardConfig
from app.jobs.runner import JobRunner
from app.models.enums import TriggerType
from app.services.amazon_runs import resolve_amazon_organization
from app.services.nineyard_sync import run_nineyard_sync

_logger = get_logger(__name__)
JOB_NINEYARD_CATALOG: Final = "nineyard.catalog"
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_nineyard_job(settings: Settings | None = None, *, now: Clock = _utc_now) -> None:
    settings = settings or get_settings()
    try:
        with session_scope() as session:
            organization_id = resolve_amazon_organization(
                session, settings.nineyard_organization_slug
            )
            with NineyardClient(NineyardConfig.from_settings(settings)) as client:
                client.authenticate()
                run = run_nineyard_sync(
                    session,
                    organization_id,
                    NineyardCatalogReader(client),
                    TriggerType.SCHEDULED,
                    now=now,
                )
            _logger.info("nineyard.job.done", run_id=str(run.id), status=run.status.value)
    except Exception:
        _logger.exception("nineyard.job.failed", job=JOB_NINEYARD_CATALOG)


def register_nineyard_jobs(runner: JobRunner, settings: Settings) -> None:
    runner.schedule(
        JOB_NINEYARD_CATALOG,
        lambda: run_nineyard_job(settings),
        interval_minutes=settings.nineyard_sync_interval_minutes,
    )
