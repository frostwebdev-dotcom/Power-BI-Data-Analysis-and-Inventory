"""The scheduled Amazon ingestion jobs (ADR 0011).

Three jobs, each a plain callable that opens its own session, resolves the
organization the credentials belong to, recovers any stale ``RUNNING`` run
of its own job type, performs the sync as ``SCHEDULED``, and swallows every
exception — a failing job is logged and recorded on its run row, and the
scheduler keeps going. Intervals come from settings.

The orders job honours ``AMAZON_ORDERS_WINDOW_DAYS`` (default 35) by
splitting the window into report-sized (30-day) chunks and running each as
its own report request, oldest first.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.transaction import session_lifecycle
from app.jobs.runner import CATCH_UP_SUFFIX, JobRunner, is_overdue
from app.models.amazon import AmazonSyncRun
from app.models.enums import AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_inventory import run_inventory_sync
from app.services.amazon_listings import run_listings_sync
from app.services.amazon_orders import MAX_WINDOW_DAYS, run_orders_sync
from app.services.amazon_runs import recover_stale_runs, resolve_amazon_organization

_logger = get_logger(__name__)

JOB_ORDERS: Final = "amazon.orders"
JOB_INVENTORY: Final = "amazon.inventory"
JOB_LISTINGS: Final = "amazon.listings"

#: Seconds after start-up at which each catch-up checks whether its job is
#: overdue. Staggered so inventory runs before listings, as on a shared tick.
CATCH_UP_DELAYS: Final = {JOB_INVENTORY: 120, JOB_ORDERS: 180, JOB_LISTINGS: 300}
SUCCESSFUL: Final = (SyncStatus.COMPLETED, SyncStatus.COMPLETED_WITH_ERRORS)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def orders_windows(
    days: int, *, now: datetime, chunk_days: int = MAX_WINDOW_DAYS
) -> list[tuple[datetime, datetime]]:
    """Split a trailing window of ``days`` into report-sized chunks, oldest first.

    35 days with a 30-day cap becomes ``[now-35d, now-5d]`` then
    ``[now-5d, now]``. Each chunk is one report request and one run; because
    the upsert only ever moves a line forward, overlap between runs is
    harmless.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    windows: list[tuple[datetime, datetime]] = []
    start = now - timedelta(days=days)
    while start < now:
        end = min(start + timedelta(days=chunk_days), now)
        windows.append((start, end))
        start = end
    return windows


def prepare_run(
    session: Session, settings: Settings, job_type: AmazonSyncJobType, now: datetime
) -> uuid.UUID:
    """Resolve the tenant and close any run the previous process left behind."""
    organization_id = resolve_amazon_organization(session, settings.amazon_organization_slug)
    recovered = recover_stale_runs(
        session,
        organization_id,
        job_type,
        now=now,
        timeout=timedelta(minutes=settings.amazon_run_timeout_minutes),
    )
    if recovered:
        _logger.warning(
            "amazon.job.recovered_stale_runs",
            job_type=job_type.value,
            run_ids=[str(run_id) for run_id in recovered],
        )
    return organization_id


def run_orders_job(settings: Settings | None = None, *, now: Clock = _utc_now) -> None:
    settings = settings or get_settings()
    try:
        with session_lifecycle() as session:
            at = now()
            organization_id = prepare_run(session, settings, AmazonSyncJobType.ORDERS_REPORT, at)
            for window_start, window_end in orders_windows(
                settings.amazon_orders_window_days, now=at
            ):
                run = run_orders_sync(
                    session,
                    organization_id,
                    window_start,
                    window_end,
                    TriggerType.SCHEDULED,
                    now=now,
                )
                _logger.info(
                    "amazon.job.orders.window_done",
                    run_id=str(run.id),
                    status=run.status.value,
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                )
    except Exception:
        # The run row, if one was opened, is already FAILED with the detail;
        # here only the scheduler's continuity matters.
        _logger.exception("amazon.job.failed", job=JOB_ORDERS)


def run_inventory_job(settings: Settings | None = None, *, now: Clock = _utc_now) -> None:
    settings = settings or get_settings()
    try:
        with session_lifecycle() as session:
            organization_id = prepare_run(session, settings, AmazonSyncJobType.FBA_INVENTORY, now())
            run = run_inventory_sync(session, organization_id, TriggerType.SCHEDULED, now=now)
            _logger.info("amazon.job.inventory.done", run_id=str(run.id), status=run.status.value)
    except Exception:
        _logger.exception("amazon.job.failed", job=JOB_INVENTORY)


def run_listings_job(settings: Settings | None = None, *, now: Clock = _utc_now) -> None:
    settings = settings or get_settings()
    try:
        with session_lifecycle() as session:
            organization_id = prepare_run(
                session, settings, AmazonSyncJobType.LISTINGS_REPORT, now()
            )
            run = run_listings_sync(session, organization_id, TriggerType.SCHEDULED, now=now)
            _logger.info("amazon.job.listings.done", run_id=str(run.id), status=run.status.value)
    except Exception:
        _logger.exception("amazon.job.failed", job=JOB_LISTINGS)


def last_successful_start(
    session: Session, organization_id: uuid.UUID, job_type: AmazonSyncJobType
) -> datetime | None:
    """When the latest completed run of ``job_type`` started, if there is one."""
    started: datetime | None = session.scalar(
        select(func.max(AmazonSyncRun.started_at)).where(
            AmazonSyncRun.organization_id == organization_id,
            AmazonSyncRun.job_type == job_type,
            AmazonSyncRun.status.in_(SUCCESSFUL),
        )
    )
    return started


def run_if_overdue(
    job_id: str,
    job: Callable[[], None],
    job_type: AmazonSyncJobType,
    interval_minutes: int,
    settings: Settings,
    *,
    now: Clock = _utc_now,
) -> bool:
    """The catch-up after a restart: run ``job`` only if its last success is
    at least one interval old. Returns whether it ran."""
    try:
        with session_lifecycle() as session:
            organization_id = resolve_amazon_organization(
                session, settings.amazon_organization_slug
            )
            last = last_successful_start(session, organization_id, job_type)
    except Exception:
        _logger.exception("amazon.job.catch_up_check_failed", job=job_id)
        return False
    if not is_overdue(last, interval_minutes, now()):
        _logger.info("jobs.catch_up_not_needed", job=job_id, last_success=last)
        return False
    _logger.info("jobs.catch_up_running", job=job_id, last_success=last)
    job()
    return True


def register_amazon_jobs(runner: JobRunner, settings: Settings) -> None:
    """Put the three jobs on a runner with the intervals from settings.

    Order matters only for the listings job, which is registered after
    inventory so that on a shared tick the inventory pull (which already maps
    listings) has run first and the listings job finds nothing new to do.
    """
    runner.schedule(
        JOB_ORDERS,
        lambda: run_orders_job(settings),
        interval_minutes=settings.amazon_orders_interval_minutes,
    )
    runner.schedule(
        JOB_INVENTORY,
        lambda: run_inventory_job(settings),
        interval_minutes=settings.amazon_inventory_interval_minutes,
    )
    runner.schedule(
        JOB_LISTINGS,
        lambda: run_listings_job(settings),
        interval_minutes=settings.amazon_listings_interval_minutes,
    )
    # One-off catch-ups: without them a restart postpones each job by a full
    # interval, and a daily job in a process restarted daily never runs.
    catch_ups: tuple[tuple[str, Callable[[], None], AmazonSyncJobType, int], ...] = (
        (
            JOB_INVENTORY,
            lambda: run_inventory_job(settings),
            AmazonSyncJobType.FBA_INVENTORY,
            settings.amazon_inventory_interval_minutes,
        ),
        (
            JOB_ORDERS,
            lambda: run_orders_job(settings),
            AmazonSyncJobType.ORDERS_REPORT,
            settings.amazon_orders_interval_minutes,
        ),
        (
            JOB_LISTINGS,
            lambda: run_listings_job(settings),
            AmazonSyncJobType.LISTINGS_REPORT,
            settings.amazon_listings_interval_minutes,
        ),
    )
    for job_id, job, job_type, interval in catch_ups:
        runner.schedule_once(
            job_id + CATCH_UP_SUFFIX,
            _catch_up(job_id, job, job_type, interval, settings),
            delay_seconds=CATCH_UP_DELAYS[job_id],
        )


def _catch_up(
    job_id: str,
    job: Callable[[], None],
    job_type: AmazonSyncJobType,
    interval_minutes: int,
    settings: Settings,
) -> Callable[[], None]:
    def catch_up() -> None:
        run_if_overdue(job_id, job, job_type, interval_minutes, settings)

    return catch_up


def build_amazon_runner(settings: Settings) -> JobRunner:
    """An APScheduler runner with the Amazon jobs registered, not yet started."""
    from app.jobs.runner import APSchedulerRunner

    runner = APSchedulerRunner()
    register_amazon_jobs(runner, settings)
    return runner
