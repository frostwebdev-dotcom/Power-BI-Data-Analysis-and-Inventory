"""Snapshot, event and watchlist data access, through the tenant scope (ADR 0012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.enums import AvailabilityStatus
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.models.watchlist import OosStatusHistory, OosWatchlistEntry
from app.repositories.scoping import ScopedRepository

MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class EventPage:
    items: Sequence[AvailabilityEvent]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True, slots=True)
class WatchlistPage:
    items: Sequence[OosWatchlistEntry]
    page: int
    page_size: int
    total: int


class InventoryRepository(ScopedRepository):
    # --- snapshots -----------------------------------------------------------------------

    def snapshot_for(
        self, job_id: uuid.UUID, vendor_product_id: uuid.UUID
    ) -> VendorInventorySnapshot | None:
        return self.session.execute(
            self.select(VendorInventorySnapshot).where(
                VendorInventorySnapshot.import_job_id == job_id,
                VendorInventorySnapshot.vendor_product_id == vendor_product_id,
            )
        ).scalar_one_or_none()

    def latest_snapshot(self, vendor_product_id: uuid.UUID) -> VendorInventorySnapshot | None:
        """The vendor line's most recent observation, by capture time."""
        return self.session.execute(
            self.select(VendorInventorySnapshot)
            .where(VendorInventorySnapshot.vendor_product_id == vendor_product_id)
            .order_by(VendorInventorySnapshot.captured_at.desc(), VendorInventorySnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def latest_status_by_line(
        self, product_id: uuid.UUID, *, vendor_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, AvailabilityStatus]:
        """Latest availability per vendor line for a product (optionally one vendor)."""
        ranked = self.select(
            VendorInventorySnapshot,
            VendorInventorySnapshot.vendor_product_id,
            VendorInventorySnapshot.availability_status,
            func.row_number()
            .over(
                partition_by=VendorInventorySnapshot.vendor_product_id,
                order_by=(
                    VendorInventorySnapshot.captured_at.desc(),
                    VendorInventorySnapshot.id.desc(),
                ),
            )
            .label("rank"),
        ).where(VendorInventorySnapshot.product_id == product_id)
        if vendor_id is not None:
            ranked = ranked.where(VendorInventorySnapshot.vendor_id == vendor_id)
        subquery = ranked.subquery()
        rows = self.session.execute(
            select(subquery.c.vendor_product_id, subquery.c.availability_status).where(
                subquery.c.rank == 1
            )
        ).all()
        return {line: AvailabilityStatus(status) for line, status in rows}

    # --- events --------------------------------------------------------------------------

    def list_events(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        vendor_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        watchlist_only: bool = False,
        since: datetime | None = None,
    ) -> EventPage:
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        statement = self.select(AvailabilityEvent)
        if vendor_id is not None:
            statement = statement.where(AvailabilityEvent.vendor_id == vendor_id)
        if product_id is not None:
            statement = statement.where(AvailabilityEvent.product_id == product_id)
        if watchlist_only:
            statement = statement.where(AvailabilityEvent.oos_watchlist_id.is_not(None))
        if since is not None:
            statement = statement.where(AvailabilityEvent.detected_at >= since)
        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.options(
                    selectinload(AvailabilityEvent.vendor_product),
                    selectinload(AvailabilityEvent.product),
                )
                .order_by(AvailabilityEvent.detected_at.desc(), AvailabilityEvent.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return EventPage(items=items, page=page, page_size=page_size, total=total)

    # --- watchlist -----------------------------------------------------------------------

    def active_watches_for(
        self, product_id: uuid.UUID, vendor_id: uuid.UUID
    ) -> Sequence[OosWatchlistEntry]:
        """Active entries an event for (product, vendor) concerns: the vendor-
        specific one first, then the all-vendors one."""
        return (
            self.session.execute(
                self.select(OosWatchlistEntry)
                .where(
                    OosWatchlistEntry.product_id == product_id,
                    OosWatchlistEntry.is_active.is_(True),
                    (OosWatchlistEntry.vendor_id == vendor_id)
                    | (OosWatchlistEntry.vendor_id.is_(None)),
                )
                .order_by(OosWatchlistEntry.vendor_id.is_(None), OosWatchlistEntry.created_at)
            )
            .scalars()
            .all()
        )

    def list_watchlist(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        vendor_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        include_inactive: bool = False,
    ) -> WatchlistPage:
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        statement = self.select(OosWatchlistEntry)
        if not include_inactive:
            statement = statement.where(OosWatchlistEntry.is_active.is_(True))
        if vendor_id is not None:
            statement = statement.where(OosWatchlistEntry.vendor_id == vendor_id)
        if product_id is not None:
            statement = statement.where(OosWatchlistEntry.product_id == product_id)
        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.options(selectinload(OosWatchlistEntry.product))
                .order_by(OosWatchlistEntry.created_at.desc(), OosWatchlistEntry.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return WatchlistPage(items=items, page=page, page_size=page_size, total=total)

    def get_watch(self, entry_id: uuid.UUID) -> OosWatchlistEntry | None:
        return self.session.execute(
            self.select(OosWatchlistEntry)
            .options(selectinload(OosWatchlistEntry.product))
            .where(OosWatchlistEntry.id == entry_id)
        ).scalar_one_or_none()

    def active_watch(
        self, product_id: uuid.UUID, vendor_id: uuid.UUID | None
    ) -> OosWatchlistEntry | None:
        statement = self.select(OosWatchlistEntry).where(
            OosWatchlistEntry.product_id == product_id, OosWatchlistEntry.is_active.is_(True)
        )
        statement = (
            statement.where(OosWatchlistEntry.vendor_id.is_(None))
            if vendor_id is None
            else statement.where(OosWatchlistEntry.vendor_id == vendor_id)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def history(self, entry_id: uuid.UUID) -> Sequence[OosStatusHistory]:
        return (
            self.session.execute(
                self.select(OosStatusHistory)
                .where(OosStatusHistory.oos_watchlist_id == entry_id)
                .order_by(OosStatusHistory.changed_at, OosStatusHistory.id)
            )
            .scalars()
            .all()
        )
