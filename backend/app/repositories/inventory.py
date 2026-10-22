"""Snapshot, event and watchlist data access, through the tenant scope (ADR 0012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.matching.normalize import normalize_gtin
from app.models.catalog import Product, ProductIdentifier
from app.models.enums import AvailabilityStatus, IdentifierType
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.models.vendor import Vendor, VendorProduct
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

    def snapshotted_lines(self, job_id: uuid.UUID, line_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        """Vendor lines this job has already snapshotted (an interrupted run)."""
        if not line_ids:
            return set()
        rows = self.session.execute(
            self.select(VendorInventorySnapshot, VendorInventorySnapshot.vendor_product_id).where(
                VendorInventorySnapshot.import_job_id == job_id,
                VendorInventorySnapshot.vendor_product_id.in_(list(line_ids)),
            )
        ).scalars()
        return set(rows)

    def latest_snapshots(
        self, line_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, VendorInventorySnapshot]:
        """Each line's most recent observation, for a chunk of lines at once."""
        if not line_ids:
            return {}
        rows = self.session.execute(
            self.select(VendorInventorySnapshot)
            .where(VendorInventorySnapshot.vendor_product_id.in_(list(line_ids)))
            .distinct(VendorInventorySnapshot.vendor_product_id)
            .order_by(
                VendorInventorySnapshot.vendor_product_id,
                VendorInventorySnapshot.captured_at.desc(),
                VendorInventorySnapshot.id.desc(),
            )
        ).scalars()
        return {snapshot.vendor_product_id: snapshot for snapshot in rows}

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
        q: str | None = None,
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
        if q and (query := q.strip()):
            # Literal substring search: user '%' and '_' are not SQL wildcards.
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            barcode = normalize_gtin(query)
            barcode_values = [query]
            if barcode.usable and barcode.canonical is not None:
                barcode_values.append(barcode.canonical)
            identifier_hit = (
                self.select(ProductIdentifier, ProductIdentifier.id)
                .where(
                    ProductIdentifier.product_id == Product.id,
                    ProductIdentifier.is_active.is_(True),
                    ProductIdentifier.identifier_type.in_(
                        (IdentifierType.UPC, IdentifierType.EAN, IdentifierType.GTIN)
                    ),
                    or_(
                        ProductIdentifier.raw_value == query,
                        ProductIdentifier.normalized_value.in_(barcode_values),
                    ),
                )
                .exists()
            )
            products = self.select(Product, Product.id).where(
                or_(
                    Product.catalog_item_number.ilike(pattern, escape="\\"),
                    Product.name.ilike(pattern, escape="\\"),
                    identifier_hit,
                )
            )
            vendor_lines = self.select(VendorProduct, VendorProduct.id).where(
                or_(
                    VendorProduct.vendor_sku.ilike(pattern, escape="\\"),
                    VendorProduct.vendor_description.ilike(pattern, escape="\\"),
                    VendorProduct.raw_upc == query,
                    VendorProduct.normalized_upc.in_(barcode_values),
                    # Older events can predate a subsequently approved mapping.
                    VendorProduct.product_id.in_(products),
                )
            )
            statement = statement.where(
                or_(
                    AvailabilityEvent.product_id.in_(products),
                    AvailabilityEvent.vendor_product_id.in_(vendor_lines),
                )
            )
        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.options(
                    selectinload(AvailabilityEvent.vendor),
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

    def active_watches_for_products(
        self, product_ids: set[uuid.UUID]
    ) -> Sequence[OosWatchlistEntry]:
        """Every active entry concerning any of these products (a chunk's worth)."""
        if not product_ids:
            return []
        return (
            self.session.execute(
                self.select(OosWatchlistEntry).where(
                    OosWatchlistEntry.product_id.in_(list(product_ids)),
                    OosWatchlistEntry.is_active.is_(True),
                )
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
                .options(selectinload(OosStatusHistory.vendor))
                .where(OosStatusHistory.oos_watchlist_id == entry_id)
                .order_by(OosStatusHistory.changed_at, OosStatusHistory.id)
            )
            .scalars()
            .all()
        )

    def available_vendors(self, product_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[Vendor]]:
        """Current suppliers, from each vendor line's latest snapshot in this tenant."""
        if not product_ids:
            return {}
        latest = (
            self.select(
                VendorInventorySnapshot,
                VendorInventorySnapshot.product_id,
                VendorInventorySnapshot.vendor_id,
                VendorInventorySnapshot.availability_status,
            )
            .where(VendorInventorySnapshot.product_id.in_(product_ids))
            .distinct(VendorInventorySnapshot.vendor_product_id)
            .order_by(
                VendorInventorySnapshot.vendor_product_id,
                VendorInventorySnapshot.captured_at.desc(),
                VendorInventorySnapshot.id.desc(),
            )
            .subquery()
        )
        rows = self.session.execute(
            self.select(Vendor, latest.c.product_id, Vendor)
            .join(latest, latest.c.vendor_id == Vendor.id)
            .where(latest.c.availability_status == AvailabilityStatus.AVAILABLE)
            .distinct()
            .order_by(Vendor.name, Vendor.code)
        ).all()
        result: dict[uuid.UUID, list[Vendor]] = {}
        for product_id, vendor in rows:
            result.setdefault(product_id, []).append(vendor)
        return result
