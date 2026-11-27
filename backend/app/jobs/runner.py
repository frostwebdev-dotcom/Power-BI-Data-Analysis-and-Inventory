"""The scheduler boundary.

:class:`JobRunner` is the whole contract the application has with a
scheduler: register a callable on an interval or a cron expression, start,
shut down. :class:`APSchedulerRunner` is the one implementation — an
in-process APScheduler 3.x ``BackgroundScheduler``, no broker, no extra
service — and it must run in exactly one process (the worker or the single
API replica), or every replica fires every job.

Every job is registered with ``max_instances=1`` (a slow run is never
overlapped by the next tick), ``coalesce=True`` (missed ticks collapse into
one run rather than a burst), and ``misfire_grace_time=3600`` (a tick up to
an hour late still runs; later than that is skipped and the next tick
catches up).

An interval job first fires one full interval after the process starts, so a
daily job in a process restarted more often than daily (every deploy restarts
it) would never fire at all. :meth:`JobRunner.schedule_once` adds a one-off
*catch-up* shortly after start-up; the job-specific callable it runs checks
:func:`is_overdue` against the last successful run and does nothing when the
regular schedule is still on time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.logging import get_logger

_logger = get_logger(__name__)

MAX_INSTANCES: Final = 1
COALESCE: Final = True
MISFIRE_GRACE_SECONDS: Final = 3600
#: Suffix of the one-off job that catches a periodic job up after a restart.
CATCH_UP_SUFFIX: Final = ".catch-up"


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """What a runner was asked to do — the fake runner records these."""

    job_id: str
    func: Callable[[], None]
    interval_minutes: int | None = None
    cron: str | None = None


@dataclass(frozen=True, slots=True)
class OneOffJob:
    """A job that runs once, ``delay_seconds`` after it was registered."""

    job_id: str
    func: Callable[[], None]
    delay_seconds: int


def is_overdue(last_success: datetime | None, interval_minutes: int, now: datetime) -> bool:
    """True when the regular schedule would leave the job late: it has never
    succeeded, or its last success is at least one interval old."""
    return last_success is None or now - last_success >= timedelta(minutes=interval_minutes)


class JobRunner(Protocol):
    """A scheduler: register jobs, start, stop. Nothing else is assumed."""

    def schedule(
        self,
        job_id: str,
        func: Callable[[], None],
        *,
        interval_minutes: int | None = None,
        cron: str | None = None,
    ) -> None: ...

    def schedule_once(self, job_id: str, func: Callable[[], None], *, delay_seconds: int) -> None:
        """Run ``func`` once, ``delay_seconds`` after this call."""
        ...

    def start(self) -> None: ...

    def shutdown(self) -> None: ...


def validate_schedule(interval_minutes: int | None, cron: str | None) -> None:
    """Exactly one of interval or cron, and an interval must be positive."""
    if (interval_minutes is None) == (cron is None):
        raise ValueError("a job takes exactly one of interval_minutes or cron")
    if interval_minutes is not None and interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")


class APSchedulerRunner:
    """APScheduler 3.x, in-process, one thread pool, no persistence.

    No job store: on restart every job is re-registered from settings and
    runs on its interval from then. Persistence would only matter if a
    missed tick had to be replayed, and the ingestion is idempotent, so the
    next tick is the replay.
    """

    def __init__(self, *, timezone: str = "UTC") -> None:
        # Imported here so the application does not need APScheduler loaded
        # unless a scheduler is actually built (tests never do).
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(
            timezone=timezone,
            job_defaults={
                "max_instances": MAX_INSTANCES,
                "coalesce": COALESCE,
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
            },
        )
        self._jobs: list[ScheduledJob] = []
        self._once: list[OneOffJob] = []

    @property
    def jobs(self) -> tuple[ScheduledJob, ...]:
        return tuple(self._jobs)

    @property
    def one_off_jobs(self) -> tuple[OneOffJob, ...]:
        return tuple(self._once)

    def schedule(
        self,
        job_id: str,
        func: Callable[[], None],
        *,
        interval_minutes: int | None = None,
        cron: str | None = None,
    ) -> None:
        validate_schedule(interval_minutes, cron)
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        trigger: Any
        if interval_minutes is not None:
            trigger = IntervalTrigger(minutes=interval_minutes)
        else:
            trigger = CronTrigger.from_crontab(cron)

        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
            max_instances=MAX_INSTANCES,
            coalesce=COALESCE,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        self._jobs.append(ScheduledJob(job_id, func, interval_minutes, cron))
        _logger.info("jobs.scheduled", job_id=job_id, interval_minutes=interval_minutes, cron=cron)

    def schedule_once(self, job_id: str, func: Callable[[], None], *, delay_seconds: int) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")
        from apscheduler.triggers.date import DateTrigger

        self._scheduler.add_job(
            func,
            trigger=DateTrigger(run_date=datetime.now(UTC) + timedelta(seconds=delay_seconds)),
            id=job_id,
            name=job_id,
            replace_existing=True,
            max_instances=MAX_INSTANCES,
            coalesce=COALESCE,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        self._once.append(OneOffJob(job_id, func, delay_seconds))
        _logger.info("jobs.scheduled_once", job_id=job_id, delay_seconds=delay_seconds)

    def job_ids(self) -> list[str]:
        return [job.id for job in self._scheduler.get_jobs()]

    def job_options(self, job_id: str) -> dict[str, Any]:
        """The effective options APScheduler holds for a job — for tests."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return {
            "max_instances": job.max_instances,
            "coalesce": job.coalesce,
            "misfire_grace_time": job.misfire_grace_time,
            "trigger": str(job.trigger),
        }

    def start(self) -> None:
        self._scheduler.start()
        _logger.info("jobs.scheduler_started", jobs=self.job_ids())

    def shutdown(self) -> None:
        if self._scheduler.running:
            # wait=False: do not block shutdown on a job mid-run; the run's
            # own finally closes its record, and a dead one is recovered as
            # stale by the next tick.
            self._scheduler.shutdown(wait=False)
        _logger.info("jobs.scheduler_stopped")

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)
