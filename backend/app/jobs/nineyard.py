"""Scheduled read-only Nineyard catalog synchronization."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.transaction import session_lifecycle
from app.integrations.nineyard import NineyardCatalogReader, NineyardClient, NineyardConfig
from app.jobs.runner import CATCH_UP_SUFFIX, JobRunner, is_overdue
from app.models.enums import SyncStatus, TriggerType
from app.models.sync import NineyardSyncRun
from app.services.amazon_runs import resolve_amazon_organization
from app.services.nineyard_sync import (
    NineyardSyncAlreadyRunning,
    amazon_listing_skus,
    run_nineyard_sync,
)

_logger = get_logger(__name__)
JOB_NINEYARD_CATALOG: Final = "nineyard.catalog"
CATCH_UP_DELAY_SECONDS: Final = 600
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_nineyard_job(settings: Settings | None = None, *, now: Clock = _utc_now) -> None:
    settings = settings or get_settings()
    try:
        with session_lifecycle() as session:
            organization_id = resolve_amazon_organization(
                session, settings.nineyard_organization_slug
            )
            seller_skus = amazon_listing_skus(session, organization_id)
            if not seller_skus:
                raise RuntimeError(
                    "No active Amazon listings are available; run the listings sync first"
                )
            with NineyardClient(NineyardConfig.from_settings(settings)) as client:
                client.authenticate()
                run = run_nineyard_sync(
                    session,
                    organization_id,
                    NineyardCatalogReader(
                        client,
                        sku_account=settings.nineyard_account,
                        seller_skus=seller_skus,
                        sku_request_interval_seconds=(
                            settings.nineyard_sku_request_interval_seconds
                        ),
                    ),
                    TriggerType.SCHEDULED,
                    now=now,
                )
            _logger.info("nineyard.job.done", run_id=str(run.id), status=run.status.value)
    except NineyardSyncAlreadyRunning:
        # A manual run or another revision is already syncing; this tick is redundant.
        _logger.info("nineyard.job.skipped_already_running", job=JOB_NINEYARD_CATALOG)
    except Exception:
        _logger.exception("nineyard.job.failed", job=JOB_NINEYARD_CATALOG)


def last_successful_start(session: Session, organization_id: uuid.UUID) -> datetime | None:
    """When the latest completed Nineyard sync started, if there is one."""
    started: datetime | None = session.scalar(
        select(func.max(NineyardSyncRun.started_at)).where(
            NineyardSyncRun.organization_id == organization_id,
            NineyardSyncRun.status.in_((SyncStatus.COMPLETED, SyncStatus.COMPLETED_WITH_ERRORS)),
        )
    )
    return started


def run_nineyard_job_if_overdue(settings: Settings, *, now: Clock = _utc_now) -> bool:
    """The catch-up after a restart: sync only if the last success is at least
    one interval old. Returns whether a sync was attempted."""
    try:
        with session_lifecycle() as session:
            organization_id = resolve_amazon_organization(
                session, settings.nineyard_organization_slug
            )
            last = last_successful_start(session, organization_id)
    except Exception:
        _logger.exception("nineyard.job.catch_up_check_failed", job=JOB_NINEYARD_CATALOG)
        return False
    if not is_overdue(last, settings.nineyard_sync_interval_minutes, now()):
        _logger.info("jobs.catch_up_not_needed", job=JOB_NINEYARD_CATALOG, last_success=last)
        return False
    _logger.info("jobs.catch_up_running", job=JOB_NINEYARD_CATALOG, last_success=last)
    run_nineyard_job(settings, now=now)
    return True


def register_nineyard_jobs(runner: JobRunner, settings: Settings) -> None:
    runner.schedule(
        JOB_NINEYARD_CATALOG,
        lambda: run_nineyard_job(settings),
        interval_minutes=settings.nineyard_sync_interval_minutes,
    )

    # Without this a restart postpones the daily sync by a full day (see
    # app/jobs/runner.py). Later than the Amazon catch-ups: the sync reads the
    # active listing SKUs the listings job maintains.
    def catch_up() -> None:
        run_nineyard_job_if_overdue(settings)

    runner.schedule_once(
        JOB_NINEYARD_CATALOG + CATCH_UP_SUFFIX, catch_up, delay_seconds=CATCH_UP_DELAY_SECONDS
    )
