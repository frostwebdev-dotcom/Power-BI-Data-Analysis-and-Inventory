"""Sales velocity per seller SKU (or per product) from the ingested Amazon data.

Read-only. Combines three sources already in PostgreSQL:

* ``amazon_order_lines`` — units ordered in the trailing 7 / 14 / 30 days,
  cancelled lines excluded;
* ``amazon_inventory_snapshots`` — the latest snapshot per SKU: fulfillable,
  FBM and inbound quantities;
* ``marketplace_listings`` → ``products`` → ``product_identifiers`` — the
  mapping (status and method), the Catalog Item Number, and the UPC.

Days of supply is the one derived number: on-hand (fulfillable + FBM) over
the 14-day average daily rate. It is ``None`` when nothing sold — a SKU that
sells zero has no meaningful runway, and a fake infinity is worse than a
blank. This read-only report never computes replenishment quantities (ADR 0011).
Milestone 2's explicit-input preview is separate and not wired to these rows
(ADR 0022).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.amazon import AmazonInventorySnapshot, AmazonOrderLine
from app.models.catalog import MarketplaceListing, Product, ProductIdentifier
from app.models.enums import IdentifierType, MappingStatus, Marketplace, MatchMethod
from app.repositories.scoping import TenantScope

#: Order statuses that do not count as demand, as Amazon spells them.
CANCELLED_STATUSES: Final = ("Cancelled", "Canceled")

WINDOWS_DAYS: Final = (7, 14, 30)

Level = Literal["sku", "product"]


@dataclass(frozen=True, slots=True)
class VelocityRow:
    seller_sku: str
    asin: str | None
    product_id: uuid.UUID | None
    catalog_item_number: str | None
    upc: str | None
    units_7: int
    units_14: int
    units_30: int
    fulfillable: int | None
    fbm_quantity: int | None
    inbound: int | None
    mapping_status: MappingStatus | None
    mapping_method: MatchMethod | None

    @property
    def avg_daily_14(self) -> float:
        return self.units_14 / 14

    @property
    def on_hand(self) -> int | None:
        if self.fulfillable is None and self.fbm_quantity is None:
            return None
        return (self.fulfillable or 0) + (self.fbm_quantity or 0)

    @property
    def days_of_supply(self) -> float | None:
        on_hand = self.on_hand
        if on_hand is None or self.avg_daily_14 <= 0:
            return None
        return on_hand / self.avg_daily_14


def velocity_report(
    session: Session,
    organization_id: uuid.UUID,
    *,
    level: Level = "sku",
    top: int | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[VelocityRow]:
    """Rows sorted by 30-day units descending, then seller SKU.

    ``level="product"`` merges the SKUs of one product into a single row
    (units and quantities summed, the first SKU alphabetically shown);
    unmapped SKUs stay as their own rows.
    """
    at = now()
    scope = TenantScope(organization_id)

    units = _units_by_sku(session, scope, at)
    latest = _latest_snapshot_by_sku(session, scope)
    listings = _listing_by_sku(session, scope)

    skus = sorted(set(units) | set(latest) | set(listings))
    rows: list[VelocityRow] = []
    for sku in skus:
        u = units.get(sku, (0, 0, 0))
        snapshot = latest.get(sku)
        listing = listings.get(sku)
        rows.append(
            VelocityRow(
                seller_sku=sku,
                asin=(listing.asin if listing else None) or (snapshot.asin if snapshot else None),
                product_id=listing.product_id if listing else None,
                catalog_item_number=listing.catalog_item_number if listing else None,
                upc=listing.upc if listing else None,
                units_7=u[0],
                units_14=u[1],
                units_30=u[2],
                fulfillable=snapshot.fulfillable if snapshot else None,
                fbm_quantity=snapshot.fbm_quantity if snapshot else None,
                inbound=(
                    snapshot.inbound_working + snapshot.inbound_shipped + snapshot.inbound_receiving
                    if snapshot
                    else None
                ),
                mapping_status=listing.mapping_status if listing else None,
                mapping_method=listing.mapping_method if listing else None,
            )
        )

    if level == "product":
        rows = _merge_by_product(rows)

    rows.sort(key=lambda r: (-r.units_30, -r.units_14, r.seller_sku))
    return rows[:top] if top else rows


# --- queries ---------------------------------------------------------------------------


def _units_by_sku(
    session: Session, scope: TenantScope, at: datetime
) -> dict[str, tuple[int, int, int]]:
    since_30 = at - timedelta(days=30)
    statement = scope.select(
        AmazonOrderLine,
        AmazonOrderLine.seller_sku,
        AmazonOrderLine.purchase_date,
        AmazonOrderLine.quantity_ordered,
    ).where(
        AmazonOrderLine.purchase_date >= since_30,
        AmazonOrderLine.purchase_date <= at,
        AmazonOrderLine.order_status.not_in(CANCELLED_STATUSES),
    )
    totals: dict[str, list[int]] = {}
    for sku, purchased, quantity in session.execute(statement):
        age = at - purchased
        bucket = totals.setdefault(sku, [0, 0, 0])
        for index, days in enumerate(WINDOWS_DAYS):
            if age <= timedelta(days=days):
                bucket[index] += quantity
    return {sku: (b[0], b[1], b[2]) for sku, b in totals.items()}


@dataclass(frozen=True, slots=True)
class _Snapshot:
    asin: str
    fulfillable: int
    fbm_quantity: int | None
    inbound_working: int
    inbound_shipped: int
    inbound_receiving: int


def _latest_snapshot_by_sku(session: Session, scope: TenantScope) -> dict[str, _Snapshot]:
    latest = (
        scope.select(
            AmazonInventorySnapshot,
            AmazonInventorySnapshot.seller_sku,
            func.max(AmazonInventorySnapshot.captured_at).label("captured_at"),
        )
        .group_by(AmazonInventorySnapshot.seller_sku)
        .subquery()
    )
    statement = scope.select(AmazonInventorySnapshot).join(
        latest,
        (AmazonInventorySnapshot.seller_sku == latest.c.seller_sku)
        & (AmazonInventorySnapshot.captured_at == latest.c.captured_at),
    )
    result: dict[str, _Snapshot] = {}
    for snapshot in session.execute(statement).scalars():
        result[snapshot.seller_sku] = _Snapshot(
            asin=snapshot.asin,
            fulfillable=snapshot.fulfillable,
            fbm_quantity=snapshot.fbm_quantity,
            inbound_working=snapshot.inbound_working,
            inbound_shipped=snapshot.inbound_shipped,
            inbound_receiving=snapshot.inbound_receiving,
        )
    return result


@dataclass(frozen=True, slots=True)
class _Listing:
    asin: str | None
    product_id: uuid.UUID | None
    catalog_item_number: str | None
    upc: str | None
    mapping_status: MappingStatus
    mapping_method: MatchMethod | None


def _listing_by_sku(session: Session, scope: TenantScope) -> dict[str, _Listing]:
    upc = (
        scope.select(
            ProductIdentifier,
            ProductIdentifier.product_id,
            func.min(ProductIdentifier.raw_value).label("upc"),
        )
        .where(
            ProductIdentifier.identifier_type == IdentifierType.UPC,
            ProductIdentifier.is_active.is_(True),
        )
        .group_by(ProductIdentifier.product_id)
        .subquery()
    )
    statement = (
        scope.select(
            MarketplaceListing,
            MarketplaceListing.seller_sku,
            MarketplaceListing.asin,
            MarketplaceListing.product_id,
            MarketplaceListing.mapping_status,
            MarketplaceListing.mapping_method,
            Product.catalog_item_number,
            upc.c.upc,
        )
        .outerjoin(Product, Product.id == MarketplaceListing.product_id)
        .outerjoin(upc, upc.c.product_id == MarketplaceListing.product_id)
        .where(MarketplaceListing.marketplace == Marketplace.AMAZON)
    )
    result: dict[str, _Listing] = {}
    for sku, asin, product_id, status, method, cin, upc_value in session.execute(statement):
        result[sku] = _Listing(
            asin=asin,
            product_id=product_id,
            catalog_item_number=cin,
            upc=upc_value,
            mapping_status=status,
            mapping_method=method,
        )
    return result


def _merge_by_product(rows: Sequence[VelocityRow]) -> list[VelocityRow]:
    merged: dict[uuid.UUID, VelocityRow] = {}
    standalone: list[VelocityRow] = []
    for row in rows:
        if row.product_id is None:
            standalone.append(row)
            continue
        existing = merged.get(row.product_id)
        if existing is None:
            merged[row.product_id] = row
            continue
        merged[row.product_id] = VelocityRow(
            seller_sku=min(existing.seller_sku, row.seller_sku),
            asin=existing.asin or row.asin,
            product_id=row.product_id,
            catalog_item_number=existing.catalog_item_number or row.catalog_item_number,
            upc=existing.upc or row.upc,
            units_7=existing.units_7 + row.units_7,
            units_14=existing.units_14 + row.units_14,
            units_30=existing.units_30 + row.units_30,
            fulfillable=_sum_optional(existing.fulfillable, row.fulfillable),
            fbm_quantity=_sum_optional(existing.fbm_quantity, row.fbm_quantity),
            inbound=_sum_optional(existing.inbound, row.inbound),
            mapping_status=existing.mapping_status,
            mapping_method=existing.mapping_method,
        )
    return list(merged.values()) + standalone


def _sum_optional(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)
