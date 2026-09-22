"""The orders ingestion run against a real PostgreSQL (ADR 0011).

A fake fetcher stands in for ``AmazonClient``; everything else — the run
row, the upsert, the partial unique index, the audit entry — is the real
thing. Report content is the same fixture the parse tests use, so a change to
one shows up in both.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AmazonOrderLine, AmazonSyncRun, AuditEvent
from app.models.enums import ActorType, AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_orders import (
    AUDIT_ACTION_COMPLETED,
    AUDIT_ACTION_FAILED,
    REPORT_TYPE,
    OrdersSyncAlreadyRunning,
    run_orders_sync,
)
from tests.integration import factories
from tests.unit.test_amazon_orders_parse import FIXTURE_ROWS, FIXTURE_UTF8, HEADER, row

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
WINDOW = (NOW - timedelta(days=30), NOW)
FIXTURE_LINE_COUNT = 8  # nine rows, one (order, SKU) pair repeated


class FakeFetcher:
    """Returns scripted report bytes, or raises, and records what was asked."""

    def __init__(self, content: bytes | None = None, *, raises: Exception | None = None) -> None:
        self.content = content
        self.raises = raises
        self.requests: list[tuple[str, datetime, datetime]] = []

    def fetch_report(
        self,
        report_type: str,
        data_start: datetime,
        data_end: datetime,
        report_options: Any = None,
        *,
        poll_interval_s: float = 15.0,
        timeout_s: float = 1800.0,
    ) -> bytes:
        self.requests.append((report_type, data_start, data_end))
        if self.raises is not None:
            raise self.raises
        assert self.content is not None
        return self.content


def sync(
    session: Session, organization_id: Any, fetcher: FakeFetcher, **kwargs: Any
) -> AmazonSyncRun:
    return run_orders_sync(
        session,
        organization_id,
        *WINDOW,
        kwargs.pop("trigger_type", TriggerType.MANUAL),
        client=fetcher,
        now=lambda: NOW,
        **kwargs,
    )


def runs_for(session: Session, organization_id: Any) -> list[AmazonSyncRun]:
    return list(
        session.execute(
            select(AmazonSyncRun)
            .where(AmazonSyncRun.organization_id == organization_id)
            .order_by(AmazonSyncRun.created_at)
        )
        .scalars()
        .all()
    )


def lines_for(session: Session, organization_id: Any) -> dict[tuple[str, str], AmazonOrderLine]:
    rows = session.execute(
        select(AmazonOrderLine).where(AmazonOrderLine.organization_id == organization_id)
    ).scalars()
    return {(line.amazon_order_id, line.seller_sku): line for line in rows}


def audit_events_for(session: Session, run: AmazonSyncRun) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "amazon_sync_runs", AuditEvent.entity_id == run.id
            )
        )
        .scalars()
        .all()
    )


# --- the happy path ---------------------------------------------------------------


def test_first_run_creates_every_line(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    fetcher = FakeFetcher(FIXTURE_UTF8)

    run = sync(db_session, organization.id, fetcher)

    assert run.status is SyncStatus.COMPLETED
    assert run.job_type is AmazonSyncJobType.ORDERS_REPORT
    assert run.trigger_type is TriggerType.MANUAL
    assert (
        run.rows_seen,
        run.rows_created,
        run.rows_updated,
        run.rows_unchanged,
        run.rows_failed,
    ) == (9, FIXTURE_LINE_COUNT, 0, 0, 0)
    assert run.started_at == NOW and run.completed_at == NOW
    assert (run.window_start, run.window_end) == WINDOW
    assert run.marketplace_id == "ATVPDKIKX0DER"
    assert fetcher.requests == [(REPORT_TYPE, *WINDOW)]

    lines = lines_for(db_session, organization.id)
    assert len(lines) == FIXTURE_LINE_COUNT
    aggregated = lines[("111-1000000-0000005", "WIDGET-12")]
    assert aggregated.quantity_ordered == 5
    assert aggregated.first_seen_sync_run_id == run.id
    assert aggregated.last_seen_sync_run_id == run.id
    assert not any(key.startswith("ship-") for key in aggregated.raw)


def test_a_completed_run_is_audited_as_system(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    run = sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))

    [event] = audit_events_for(db_session, run)
    assert event.action == AUDIT_ACTION_COMPLETED
    assert event.actor_type is ActorType.SYSTEM
    assert event.actor_user_id is None
    assert event.after is not None
    assert event.after["status"] == "COMPLETED"
    assert event.after["rows_created"] == FIXTURE_LINE_COUNT
    assert "8 created" in (event.summary or "")


def test_a_second_identical_run_changes_nothing_but_last_seen(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    first = sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))

    second = sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))

    assert second.status is SyncStatus.COMPLETED
    assert (second.rows_created, second.rows_updated, second.rows_unchanged) == (
        0,
        0,
        FIXTURE_LINE_COUNT,
    )
    lines = lines_for(db_session, organization.id)
    assert len(lines) == FIXTURE_LINE_COUNT
    for line in lines.values():
        assert line.first_seen_sync_run_id == first.id
        assert line.last_seen_sync_run_id == second.id


def test_a_newer_last_updated_date_updates_that_line_only(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))
    # The pending order shipped since the last pull.
    updated_rows = [
        row(
            "111-1000000-0000004",
            "WIDGET-12",
            status="Shipped",
            item_status="Shipped",
            updated="2026-09-13T09:00:00+00:00",
        )
        if r.startswith("111-1000000-0000004\t")
        else r
        for r in FIXTURE_ROWS
    ]
    newer = (HEADER + "\n" + "\n".join(updated_rows) + "\n").encode()

    run = sync(db_session, organization.id, FakeFetcher(newer))

    assert (run.rows_created, run.rows_updated, run.rows_unchanged) == (
        0,
        1,
        FIXTURE_LINE_COUNT - 1,
    )
    line = lines_for(db_session, organization.id)[("111-1000000-0000004", "WIDGET-12")]
    assert line.order_status == "Shipped"
    assert line.item_status == "Shipped"
    assert line.last_updated_at == datetime(2026, 9, 13, 9, 0, tzinfo=UTC)
    assert line.last_seen_sync_run_id == run.id


def test_an_older_report_cannot_regress_a_line(db_session: Session) -> None:
    """Re-running an old window must not overwrite newer data."""
    organization = factories.make_organization(db_session)
    sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))
    stale_rows = [
        row(
            "111-1000000-0000001",
            "WIDGET-12",
            status="Pending",
            item_status="Unshipped",
            updated="2026-09-01T00:00:00+00:00",
        )
        if r.startswith("111-1000000-0000001\t")
        else r
        for r in FIXTURE_ROWS
    ]
    stale = (HEADER + "\n" + "\n".join(stale_rows) + "\n").encode()

    run = sync(db_session, organization.id, FakeFetcher(stale))

    assert run.rows_updated == 0
    line = lines_for(db_session, organization.id)[("111-1000000-0000001", "WIDGET-12")]
    assert line.order_status == "Shipped"
    assert line.last_seen_sync_run_id == run.id  # still seen, just not changed


def test_rejected_rows_complete_with_errors(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    content = (HEADER + "\n" + row("111-1", "A") + "\n" + row("111-2", "B", qty="many")).encode()

    run = sync(db_session, organization.id, FakeFetcher(content))

    assert run.status is SyncStatus.COMPLETED_WITH_ERRORS
    assert (run.rows_seen, run.rows_created, run.rows_failed) == (2, 1, 1)
    assert run.error_details["rows"][0]["row"] == 2
    assert "quantity" in run.error_details["rows"][0]["reason"]


def test_lines_are_scoped_to_the_organization(db_session: Session) -> None:
    organization_a = factories.make_organization(db_session)
    organization_b = factories.make_organization(db_session)

    sync(db_session, organization_a.id, FakeFetcher(FIXTURE_UTF8))
    sync(db_session, organization_b.id, FakeFetcher(FIXTURE_UTF8))

    assert len(lines_for(db_session, organization_a.id)) == FIXTURE_LINE_COUNT
    assert len(lines_for(db_session, organization_b.id)) == FIXTURE_LINE_COUNT


def test_a_triggering_user_is_recorded(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)

    run = sync(
        db_session,
        organization.id,
        FakeFetcher(FIXTURE_UTF8),
        trigger_type=TriggerType.SCHEDULED,
        triggered_by_user_id=user.id,
    )

    assert run.trigger_type is TriggerType.SCHEDULED
    assert run.triggered_by_user_id == user.id


# --- failure ---------------------------------------------------------------------


def test_a_client_failure_leaves_one_failed_run_and_no_running_row(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    fetcher = FakeFetcher(raises=RuntimeError("socket closed mid-download"))

    with pytest.raises(RuntimeError, match="socket closed"):
        sync(db_session, organization.id, fetcher)

    runs = runs_for(db_session, organization.id)
    assert [r.status for r in runs] == [SyncStatus.FAILED]
    [failed] = runs
    assert failed.completed_at == NOW
    assert failed.error_message == "socket closed mid-download"
    assert failed.error_details == {
        "exception": "RuntimeError",
        "message": "socket closed mid-download",
    }
    assert lines_for(db_session, organization.id) == {}

    [event] = audit_events_for(db_session, failed)
    assert event.action == AUDIT_ACTION_FAILED
    assert event.actor_type is ActorType.SYSTEM


def test_a_failure_inside_the_upsert_transaction_is_rolled_back_and_recorded(
    db_session: Session,
) -> None:
    """The session is mid-failure when the run has to be closed; it still is."""
    organization = factories.make_organization(db_session)
    # asin is String(16); this violates it on INSERT, inside the upsert
    # transaction, after the RUNNING row was committed.
    content = (HEADER + "\n" + row("111-1", "A", asin="B" * 40) + "\n").encode()
    fetcher = FakeFetcher(content)

    with pytest.raises(Exception, match="value too long"):
        sync(db_session, organization.id, fetcher)

    runs = runs_for(db_session, organization.id)
    assert [r.status for r in runs] == [SyncStatus.FAILED]
    assert runs[0].error_details["exception"] == "DataError"
    assert lines_for(db_session, organization.id) == {}
    assert audit_events_for(db_session, runs[0])[0].action == AUDIT_ACTION_FAILED


def test_a_failed_run_frees_the_slot_for_the_next(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    with pytest.raises(RuntimeError):
        sync(db_session, organization.id, FakeFetcher(raises=RuntimeError("boom")))

    run = sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))

    assert run.status is SyncStatus.COMPLETED
    # Both runs use the injected ``NOW`` clock, so PostgreSQL may give their
    # server-generated timestamps the same value on a fast CI host.  The
    # contract is one retained failure and one successful retry, not their
    # incidental SELECT order.
    statuses = [r.status for r in runs_for(db_session, organization.id)]
    assert len(statuses) == 2
    assert statuses.count(SyncStatus.FAILED) == 1
    assert statuses.count(SyncStatus.COMPLETED) == 1


def test_a_second_concurrent_run_is_refused_by_the_partial_unique_index(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.RUNNING,
        started_at=NOW,
    )
    # Committed, as a real concurrent run would be; the refused attempt's
    # rollback must not be able to take the seed row with it.
    db_session.commit()
    fetcher = FakeFetcher(FIXTURE_UTF8)

    with pytest.raises(OrdersSyncAlreadyRunning):
        sync(db_session, organization.id, fetcher)

    # Nothing was fetched, and the only run is the one that was already there.
    assert fetcher.requests == []
    assert [r.status for r in runs_for(db_session, organization.id)] == [SyncStatus.RUNNING]


def test_a_running_inventory_job_does_not_block_an_orders_run(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.FBA_INVENTORY,
        status=SyncStatus.RUNNING,
        started_at=NOW,
    )

    run = sync(db_session, organization.id, FakeFetcher(FIXTURE_UTF8))

    assert run.status is SyncStatus.COMPLETED


def test_a_bad_window_is_refused_before_any_run_is_opened(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    fetcher = FakeFetcher(FIXTURE_UTF8)

    with pytest.raises(ValueError, match="future"):
        run_orders_sync(
            db_session,
            organization.id,
            NOW - timedelta(days=1),
            NOW + timedelta(days=1),
            TriggerType.MANUAL,
            client=fetcher,
            now=lambda: NOW,
        )

    assert runs_for(db_session, organization.id) == []
    assert fetcher.requests == []
