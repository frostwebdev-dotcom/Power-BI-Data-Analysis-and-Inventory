"""The scheduler boundary and the Amazon jobs' registration. No threads, no network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.core.config import Settings
from app.jobs.amazon import (
    JOB_INVENTORY,
    JOB_LISTINGS,
    JOB_ORDERS,
    build_amazon_runner,
    orders_windows,
    register_amazon_jobs,
)
from app.jobs.nineyard import JOB_NINEYARD_CATALOG, register_nineyard_jobs
from app.jobs.runner import (
    CATCH_UP_SUFFIX,
    COALESCE,
    MAX_INSTANCES,
    MISFIRE_GRACE_SECONDS,
    APSchedulerRunner,
    JobRunner,
    OneOffJob,
    ScheduledJob,
    is_overdue,
    validate_schedule,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


class FakeRunner:
    """Records what was scheduled; runs nothing."""

    def __init__(self) -> None:
        self.jobs: list[ScheduledJob] = []
        self.once: list[OneOffJob] = []
        self.started = False
        self.stopped = False

    def schedule(
        self,
        job_id: str,
        func: Callable[[], None],
        *,
        interval_minutes: int | None = None,
        cron: str | None = None,
    ) -> None:
        validate_schedule(interval_minutes, cron)
        self.jobs.append(ScheduledJob(job_id, func, interval_minutes, cron))

    def schedule_once(self, job_id: str, func: Callable[[], None], *, delay_seconds: int) -> None:
        self.once.append(OneOffJob(job_id, func, delay_seconds))

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.stopped = True


def _accepts_runner(runner: JobRunner) -> JobRunner:
    return runner


class TestProtocol:
    def test_the_fake_satisfies_the_protocol(self) -> None:
        # Structural typing: passing it where a JobRunner is expected type-checks
        # and works.
        runner = _accepts_runner(FakeRunner())
        runner.schedule("x", lambda: None, interval_minutes=5)
        runner.start()
        runner.shutdown()

    def test_exactly_one_of_interval_or_cron(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            validate_schedule(None, None)
        with pytest.raises(ValueError, match="exactly one"):
            validate_schedule(5, "0 * * * *")

    def test_an_interval_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            validate_schedule(0, None)


class TestRegistration:
    def test_nineyard_catalog_job_uses_its_configured_interval(self) -> None:
        runner = FakeRunner()

        register_nineyard_jobs(runner, Settings(nineyard_sync_interval_minutes=360))

        assert [(job.job_id, job.interval_minutes) for job in runner.jobs] == [
            (JOB_NINEYARD_CATALOG, 360)
        ]

    def test_the_three_jobs_are_registered_with_settings_intervals(self) -> None:
        runner = FakeRunner()
        settings = Settings(
            amazon_orders_interval_minutes=720,
            amazon_inventory_interval_minutes=30,
            amazon_listings_interval_minutes=1440,
        )

        register_amazon_jobs(runner, settings)

        assert [(job.job_id, job.interval_minutes) for job in runner.jobs] == [
            (JOB_ORDERS, 720),
            (JOB_INVENTORY, 30),
            (JOB_LISTINGS, 1440),
        ]
        assert all(job.cron is None for job in runner.jobs)

    def test_defaults_are_24h_60m_24h_and_listings_follows_inventory(self) -> None:
        runner = FakeRunner()

        register_amazon_jobs(runner, Settings())

        intervals = {job.job_id: job.interval_minutes for job in runner.jobs}
        assert intervals == {JOB_ORDERS: 1440, JOB_INVENTORY: 60, JOB_LISTINGS: 1440}
        order = [job.job_id for job in runner.jobs]
        assert order.index(JOB_INVENTORY) < order.index(JOB_LISTINGS)

    def test_each_job_is_a_zero_argument_callable(self) -> None:
        runner = FakeRunner()
        register_amazon_jobs(runner, Settings())

        for job in runner.jobs:
            assert callable(job.func)
            assert job.func.__code__.co_argcount == 0


class TestAPSchedulerRunner:
    """The real runner, never started: registration and options only."""

    def test_jobs_carry_the_safety_options(self) -> None:
        runner = APSchedulerRunner()

        runner.schedule("tick", lambda: None, interval_minutes=15)

        options = runner.job_options("tick")
        assert options["max_instances"] == MAX_INSTANCES == 1
        assert options["coalesce"] is COALESCE is True
        assert options["misfire_grace_time"] == MISFIRE_GRACE_SECONDS == 3600
        assert "interval" in options["trigger"]
        assert runner.job_ids() == ["tick"]
        assert not runner.running

    def test_a_cron_schedule_is_accepted(self) -> None:
        runner = APSchedulerRunner()

        runner.schedule("nightly", lambda: None, cron="0 3 * * *")

        assert "cron" in runner.job_options("nightly")["trigger"]

    def test_build_amazon_runner_registers_all_three_without_starting(self) -> None:
        runner = build_amazon_runner(Settings())

        assert isinstance(runner, APSchedulerRunner)
        periodic = {JOB_ORDERS, JOB_INVENTORY, JOB_LISTINGS}
        assert {job.job_id for job in runner.jobs} == periodic
        assert set(runner.job_ids()) == periodic | {j + CATCH_UP_SUFFIX for j in periodic}
        assert not runner.running

    def test_a_one_off_job_uses_a_date_trigger_with_the_safety_options(self) -> None:
        runner = APSchedulerRunner()

        runner.schedule_once("once", lambda: None, delay_seconds=90)

        options = runner.job_options("once")
        assert "date" in options["trigger"]
        assert options["max_instances"] == 1
        assert options["misfire_grace_time"] == MISFIRE_GRACE_SECONDS
        assert [(j.job_id, j.delay_seconds) for j in runner.one_off_jobs] == [("once", 90)]

    def test_a_negative_delay_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            APSchedulerRunner().schedule_once("once", lambda: None, delay_seconds=-1)

    def test_shutdown_before_start_is_harmless(self) -> None:
        runner = APSchedulerRunner()
        runner.shutdown()

        assert not runner.running


class TestOrdersWindows:
    def test_35_days_becomes_a_30_day_and_a_5_day_report(self) -> None:
        windows = orders_windows(35, now=NOW)

        assert windows == [
            (NOW - timedelta(days=35), NOW - timedelta(days=5)),
            (NOW - timedelta(days=5), NOW),
        ]

    def test_a_window_within_the_cap_is_one_report(self) -> None:
        assert orders_windows(30, now=NOW) == [(NOW - timedelta(days=30), NOW)]
        assert orders_windows(7, now=NOW) == [(NOW - timedelta(days=7), NOW)]

    def test_windows_are_contiguous_and_end_now(self) -> None:
        windows = orders_windows(95, now=NOW)

        assert len(windows) == 4
        assert windows[0][0] == NOW - timedelta(days=95)
        assert windows[-1][1] == NOW
        for (_, end), (next_start, _) in pairwise(windows):
            assert end == next_start
        assert all(end - start <= timedelta(days=30) for start, end in windows)

    def test_non_positive_days_are_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            orders_windows(0, now=NOW)


class TestLifespanGuard:
    """The scheduler must never start in tests or when Amazon is disabled."""

    def test_disabled_settings_do_not_build_a_runner(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(Settings(app_env="test", amazon_enabled=False))
        with TestClient(app):
            assert not hasattr(app.state, "job_runner")

    def test_the_test_environment_never_starts_it_even_when_enabled(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        settings = Settings(
            app_env="test",
            amazon_enabled=True,
            amazon_lwa_client_id="id",
            amazon_lwa_client_secret="secret",
            amazon_lwa_refresh_token="Atzr|token",
            amazon_seller_id="A1SELLER",
        )
        app = create_app(settings)
        with TestClient(app):
            assert not hasattr(app.state, "job_runner")


# --- catch-up after a restart ---------------------------------------------------------------


class TestCatchUp:
    """A periodic job first fires one interval after start-up, so every restart
    would postpone a daily job by a day. A one-off catch-up runs it shortly
    after start-up — but only when it is actually overdue."""

    def test_overdue_means_never_succeeded_or_at_least_one_interval_ago(self) -> None:
        assert is_overdue(None, 60, NOW)
        assert is_overdue(NOW - timedelta(minutes=60), 60, NOW)
        assert not is_overdue(NOW - timedelta(minutes=59), 60, NOW)

    def test_amazon_registers_one_staggered_catch_up_per_job(self) -> None:
        runner = FakeRunner()

        register_amazon_jobs(runner, Settings())

        delays = {job.job_id: job.delay_seconds for job in runner.once}
        assert set(delays) == {
            JOB_INVENTORY + CATCH_UP_SUFFIX,
            JOB_ORDERS + CATCH_UP_SUFFIX,
            JOB_LISTINGS + CATCH_UP_SUFFIX,
        }
        # Inventory before listings, as on a shared periodic tick.
        assert delays[JOB_INVENTORY + CATCH_UP_SUFFIX] < delays[JOB_LISTINGS + CATCH_UP_SUFFIX]
        assert all(job.func.__code__.co_argcount == 0 for job in runner.once)

    def test_nineyard_catch_up_follows_the_amazon_listings_catch_up(self) -> None:
        runner = FakeRunner()

        register_nineyard_jobs(runner, Settings())
        register_amazon_jobs(runner, Settings())

        delays = {job.job_id: job.delay_seconds for job in runner.once}
        assert (
            delays[JOB_NINEYARD_CATALOG + CATCH_UP_SUFFIX] > delays[JOB_LISTINGS + CATCH_UP_SUFFIX]
        )

    @pytest.mark.parametrize(
        ("last_success", "expected"),
        [(None, True), (NOW - timedelta(days=2), True), (NOW - timedelta(hours=1), False)],
    )
    def test_the_amazon_catch_up_runs_only_when_overdue(
        self,
        monkeypatch: pytest.MonkeyPatch,
        last_success: datetime | None,
        expected: bool,
    ) -> None:
        import contextlib
        import uuid

        from app.jobs import amazon
        from app.models.enums import AmazonSyncJobType

        monkeypatch.setattr(amazon, "session_lifecycle", lambda: contextlib.nullcontext(object()))
        monkeypatch.setattr(amazon, "resolve_amazon_organization", lambda *_: uuid.uuid4())
        monkeypatch.setattr(amazon, "last_successful_start", lambda *_: last_success)
        calls: list[str] = []

        ran = amazon.run_if_overdue(
            JOB_ORDERS,
            lambda: calls.append("ran"),
            AmazonSyncJobType.ORDERS_REPORT,
            1440,
            Settings(),
            now=lambda: NOW,
        )

        assert ran is expected
        assert calls == (["ran"] if expected else [])

    def test_a_failed_overdue_check_does_not_run_the_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.jobs import amazon
        from app.models.enums import AmazonSyncJobType

        def unavailable() -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(amazon, "session_lifecycle", unavailable)
        calls: list[str] = []

        assert not amazon.run_if_overdue(
            JOB_ORDERS,
            lambda: calls.append("ran"),
            AmazonSyncJobType.ORDERS_REPORT,
            1440,
            Settings(),
            now=lambda: NOW,
        )
        assert calls == []

    @pytest.mark.parametrize(
        ("last_success", "expected"),
        [(None, True), (NOW - timedelta(days=1), True), (NOW - timedelta(hours=2), False)],
    )
    def test_the_nineyard_catch_up_runs_only_when_overdue(
        self,
        monkeypatch: pytest.MonkeyPatch,
        last_success: datetime | None,
        expected: bool,
    ) -> None:
        import contextlib
        import uuid

        from app.jobs import nineyard

        monkeypatch.setattr(nineyard, "session_lifecycle", lambda: contextlib.nullcontext(object()))
        monkeypatch.setattr(nineyard, "resolve_amazon_organization", lambda *_: uuid.uuid4())
        monkeypatch.setattr(nineyard, "last_successful_start", lambda *_: last_success)
        calls: list[Settings] = []
        monkeypatch.setattr(
            nineyard, "run_nineyard_job", lambda settings, **_: calls.append(settings)
        )

        ran = nineyard.run_nineyard_job_if_overdue(Settings(), now=lambda: NOW)

        assert ran is expected
        assert len(calls) == (1 if expected else 0)
