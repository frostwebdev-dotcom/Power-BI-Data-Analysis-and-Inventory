"""The inventory ingestion run against a real PostgreSQL (ADR 0011).

A fake client supplies FBA summaries and the listings report; the run row,
the append-only snapshots, the unique index and the audit entry are real.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.amazon import InventorySummary
from app.models import AmazonInventorySnapshot, AmazonSyncRun, AuditEvent, MarketplaceListing
from app.models.enums import ActorType, AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_inventory import (
    AUDIT_ACTION_COMPLETED,
    AUDIT_ACTION_FAILED,
    LISTINGS_REPORT_TYPE,
    InventorySyncAlreadyRunning,
    run_inventory_sync,
)
from tests.integration import factories
from tests.unit.test_amazon_inventory_parse import FIXTURE_UTF8, HEADER, row, summary

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=6)

SUMMARIES = [
    summary("WIDGET-12"),
    summary("SHELF-9", asin="B000TEST09", fnsku="X000TEST09", fulfillable=3, inbound_working=0),
]
# FBA: WIDGET-12, SHELF-9. FBM-only from the report: GADGET-1, BLANK-1, UPC-1, CAFE-1.
EXPECTED_SKUS = {"WIDGET-12", "SHELF-9", "GADGET-1", "BLANK-1", "UPC-1", "CAFE-1"}


class FakeClient:
    def __init__(
        self,
        summaries: Sequence[InventorySummary] = SUMMARIES,
        listings: bytes = FIXTURE_UTF8,
        *,
        raise_in_summaries: Exception | None = None,
        raise_in_report: Exception | None = None,
    ) -> None:
        self.summaries = list(summaries)
        self.listings = listings
        self.raise_in_summaries = raise_in_summaries
        self.raise_in_report = raise_in_report
        self.page_delays: list[float] = []
        self.seller_sku_batches: list[tuple[str, ...] | None] = []
        self.report_requests: list[str] = []

    def iter_inventory_summaries(
        self,
        *,
        details: bool = True,
        page_delay_s: float = 0.0,
        seller_skus: Sequence[str] | None = None,
    ) -> Iterator[InventorySummary]:
        assert details is True
        self.page_delays.append(page_delay_s)
        self.seller_sku_batches.append(tuple(seller_skus) if seller_skus is not None else None)
        for index, item in enumerate(self.summaries):
            if self.raise_in_summaries is not None and index == 1:
                raise self.raise_in_summaries
            yield item

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
        self.report_requests.append(report_type)
        if self.raise_in_report is not None:
            raise self.raise_in_report
        return self.listings


def sync(
    session: Session, organization_id: Any, client: FakeClient, *, at: datetime = NOW, **kwargs: Any
) -> AmazonSyncRun:
    return run_inventory_sync(
        session,
        organization_id,
        kwargs.pop("trigger_type", TriggerType.MANUAL),
        client=client,
        now=lambda: at,
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


def snapshots_for(session: Session, run: AmazonSyncRun) -> dict[str, AmazonInventorySnapshot]:
    rows = session.execute(
        select(AmazonInventorySnapshot).where(AmazonInventorySnapshot.sync_run_id == run.id)
    ).scalars()
    return {snapshot.seller_sku: snapshot for snapshot in rows}


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


# --- the happy path ------------------------------------------------------------------


def test_one_snapshot_per_sku_with_fba_and_fbm_merged(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    client = FakeClient()

    run = sync(db_session, organization.id, client)

    assert run.status is SyncStatus.COMPLETED
    assert run.job_type is AmazonSyncJobType.FBA_INVENTORY
    assert run.rows_seen == len(SUMMARIES) + 6  # summaries + listings rows
    assert run.rows_created == len(EXPECTED_SKUS)
    assert (run.rows_updated, run.rows_unchanged, run.rows_failed) == (0, 0, 0)
    assert run.error_details["fbm_only"] == 4
    assert run.error_details["listings_mapping"]["listings_seen"] == 6
    assert client.report_requests == [LISTINGS_REPORT_TYPE]
    assert client.seller_sku_batches == [
        ("WIDGET-12", "GADGET-1", "BLANK-1", "ODD-1", "UPC-1", "CAFE-1")
    ]

    snapshots = snapshots_for(db_session, run)
    assert set(snapshots) == EXPECTED_SKUS
    widget = snapshots["WIDGET-12"]
    assert (widget.fulfillable, widget.inbound_working, widget.inbound_shipped) == (40, 100, 50)
    assert widget.fbm_quantity is None  # AMAZON_NA in the report
    assert widget.captured_at == NOW
    assert widget.amazon_last_updated_at == datetime(2026, 9, 15, 9, 30, tzinfo=UTC)
    gadget = snapshots["GADGET-1"]
    assert gadget.fbm_quantity == 17
    assert (gadget.fulfillable, gadget.inbound_working) == (0, 0)
    assert gadget.asin == "B000TEST02"
    assert snapshots["BLANK-1"].fbm_quantity is None


def test_the_page_delay_from_settings_reaches_the_client(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    client = FakeClient()

    sync(db_session, organization.id, client)

    assert client.page_delays == [0.6]


def test_inactive_listings_are_not_requested_or_snapshotted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    content = (
        HEADER
        + "\n"
        + row("ACTIVE-1", channel="DEFAULT", status="Active")
        + "\n"
        + row("OLD-1", channel="DEFAULT", status="Inactive")
    ).encode()
    client = FakeClient([summary("ACTIVE-1")], listings=content)

    run = sync(db_session, organization.id, client)

    assert client.seller_sku_batches == [("ACTIVE-1",)]
    assert set(snapshots_for(db_session, run)) == {"ACTIVE-1"}
    assert run.error_details["listings_mapping"]["inactive_skipped"] == 1


def test_a_completed_run_is_audited_as_system(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    run = sync(db_session, organization.id, FakeClient())

    [event] = audit_events_for(db_session, run)
    assert event.action == AUDIT_ACTION_COMPLETED
    assert event.actor_type is ActorType.SYSTEM
    assert event.after is not None and event.after["status"] == "COMPLETED"


def test_snapshots_are_append_only_across_runs(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    first = sync(db_session, organization.id, FakeClient(), at=NOW)
    changed = [summary("WIDGET-12", fulfillable=12, inbound_working=0)]

    second = sync(db_session, organization.id, FakeClient(changed), at=LATER)

    before = snapshots_for(db_session, first)
    after = snapshots_for(db_session, second)
    # The first run's rows are exactly as written; the second run has its own.
    assert before["WIDGET-12"].fulfillable == 40
    assert before["WIDGET-12"].captured_at == NOW
    assert after["WIDGET-12"].fulfillable == 12
    assert after["WIDGET-12"].captured_at == LATER
    assert before["WIDGET-12"].id != after["WIDGET-12"].id
    total = (
        db_session.execute(
            select(AmazonInventorySnapshot).where(
                AmazonInventorySnapshot.organization_id == organization.id
            )
        )
        .scalars()
        .all()
    )
    assert len(total) == len(before) + len(after)


def test_listings_are_mapped_in_the_same_run(db_session: Session) -> None:
    """Every parsed listings row becomes a marketplace_listings row, in the
    same transaction as the snapshots (mapping itself is tested in
    test_amazon_listings_mapping.py)."""
    organization = factories.make_organization(db_session)

    run = sync(db_session, organization.id, FakeClient())

    listings = (
        db_session.execute(
            select(MarketplaceListing).where(MarketplaceListing.organization_id == organization.id)
        )
        .scalars()
        .all()
    )
    assert {listing.seller_sku for listing in listings} == {
        "WIDGET-12",
        "GADGET-1",
        "BLANK-1",
        "ODD-1",
        "UPC-1",
        "CAFE-1",
    }
    mapping = run.error_details["listings_mapping"]
    assert mapping["listings_created"] == 6
    # No catalog was seeded, so nothing could resolve; every row is queued.
    assert mapping["unmapped"] == 6
    assert mapping["exceptions_opened"] == 6


def test_rejected_listing_rows_complete_with_errors(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    bad = (
        HEADER + "\n" + row("OK-1", channel="DEFAULT") + "\n" + row("BAD", quantity="x")
    ).encode()

    run = sync(db_session, organization.id, FakeClient(listings=bad))

    assert run.status is SyncStatus.COMPLETED_WITH_ERRORS
    assert run.rows_failed == 1
    assert run.rows_created == len(SUMMARIES) + 1
    assert run.error_details["rows"][0]["reason"].startswith("quantity is not an integer")


# --- failure -----------------------------------------------------------------------


def test_a_failure_while_paging_leaves_one_failed_run_and_no_snapshots(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    client = FakeClient(raise_in_summaries=RuntimeError("throttled beyond retries"))

    with pytest.raises(RuntimeError, match="throttled"):
        sync(db_session, organization.id, client)

    runs = runs_for(db_session, organization.id)
    assert [r.status for r in runs] == [SyncStatus.FAILED]
    assert runs[0].error_details == {
        "exception": "RuntimeError",
        "message": "throttled beyond retries",
    }
    assert snapshots_for(db_session, runs[0]) == {}
    assert client.report_requests == [LISTINGS_REPORT_TYPE]
    [event] = audit_events_for(db_session, runs[0])
    assert event.action == AUDIT_ACTION_FAILED


def test_a_failure_fetching_the_listings_report_is_recorded(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    client = FakeClient(raise_in_report=RuntimeError("report CANCELLED"))

    with pytest.raises(RuntimeError, match="CANCELLED"):
        sync(db_session, organization.id, client)

    [failed] = runs_for(db_session, organization.id)
    assert failed.status is SyncStatus.FAILED
    assert snapshots_for(db_session, failed) == {}


def test_a_failure_inside_the_insert_transaction_is_rolled_back_and_recorded(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    # asin is String(16): violates on INSERT, inside the transaction.
    client = FakeClient([summary("S", asin="B" * 40)], listings=(HEADER + "\n").encode())

    with pytest.raises(Exception, match="value too long"):
        sync(db_session, organization.id, client)

    [failed] = runs_for(db_session, organization.id)
    assert failed.status is SyncStatus.FAILED
    assert failed.error_details["exception"] == "DataError"
    assert snapshots_for(db_session, failed) == {}


def test_a_second_concurrent_run_is_refused(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.FBA_INVENTORY,
        status=SyncStatus.RUNNING,
        started_at=NOW,
    )
    db_session.commit()
    client = FakeClient()

    with pytest.raises(InventorySyncAlreadyRunning):
        sync(db_session, organization.id, client)

    assert client.page_delays == []  # nothing was read
    assert client.seller_sku_batches == []
    assert [r.status for r in runs_for(db_session, organization.id)] == [SyncStatus.RUNNING]


def test_a_running_orders_job_does_not_block_an_inventory_run(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.RUNNING,
        started_at=NOW,
    )

    run = sync(db_session, organization.id, FakeClient())

    assert run.status is SyncStatus.COMPLETED
