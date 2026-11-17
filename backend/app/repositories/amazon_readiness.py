"""Bounded tenant-scoped reads; readiness never invokes an external data source."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session, aliased

from app.models.amazon import AmazonInventorySnapshot, AmazonOrderLine, AmazonSyncRun
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import AmazonSyncJobType, Marketplace
from app.repositories.scoping import ScopedRepository

MAX_LISTINGS = 200
MAX_RUNS = 500
MAX_ORDER_LINES = 50000


class AmazonReadinessRepository(ScopedRepository):
    def __init__(self, session: Session, organization_id: uuid.UUID, *, lock_sources: bool = False):
        super().__init__(session, organization_id)
        self.lock_sources = lock_sources

    def product(self, identifier: uuid.UUID) -> Product | None:
        row = self.session.scalar(
            self.select(Product).where(Product.id == identifier).with_for_update(read=True)
        )
        return row if isinstance(row, Product) else None

    def listings(self, identifier: uuid.UUID, marketplace: str) -> list[MarketplaceListing]:
        return list(
            self.session.scalars(
                self.select(MarketplaceListing)
                .where(
                    MarketplaceListing.product_id == identifier,
                    MarketplaceListing.marketplace == Marketplace.AMAZON,
                    MarketplaceListing.marketplace_id == marketplace,
                )
                .order_by(MarketplaceListing.seller_sku, MarketplaceListing.id)
                .limit(MAX_LISTINGS + 1)
                .with_for_update(read=True)
            )
        )

    def runs(self, marketplace: str, job: AmazonSyncJobType) -> list[AmazonSyncRun]:
        query = (
            self.select(AmazonSyncRun)
            .where(AmazonSyncRun.marketplace_id == marketplace, AmazonSyncRun.job_type == job)
            .order_by(AmazonSyncRun.started_at.desc().nulls_last(), AmazonSyncRun.id)
            .limit(MAX_RUNS + 1)
        )
        if self.lock_sources:
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        return list(self.session.scalars(query))

    def snapshots(self, run_id: uuid.UUID, skus: set[str]) -> dict[str, AmazonInventorySnapshot]:
        query = self.select(AmazonInventorySnapshot).where(
            AmazonInventorySnapshot.sync_run_id == run_id,
            AmazonInventorySnapshot.seller_sku.in_(skus),
        )
        if self.lock_sources:
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        return {row.seller_sku: row for row in self.session.scalars(query)}

    def orders(
        self, marketplace: str, skus: set[str], start: datetime, end: datetime
    ) -> list[tuple[AmazonOrderLine, AmazonSyncRun | None]]:
        run = aliased(AmazonSyncRun)
        query = (
            self.select(AmazonOrderLine, AmazonOrderLine, run)
            .outerjoin(
                run,
                (run.id == AmazonOrderLine.last_seen_sync_run_id)
                & (run.organization_id == self.scope.organization_id)
                & (run.marketplace_id == marketplace)
                & (run.job_type == AmazonSyncJobType.ORDERS_REPORT),
            )
            .where(
                AmazonOrderLine.marketplace_id == marketplace,
                AmazonOrderLine.seller_sku.in_(skus),
                AmazonOrderLine.purchase_date >= start,
                AmazonOrderLine.purchase_date < end,
            )
            .order_by(AmazonOrderLine.id)
            .limit(MAX_ORDER_LINES + 1)
        )
        if self.lock_sources:
            # The joined run is nullable; PostgreSQL cannot lock that side of an
            # outer join. Relevant run rows were locked by runs() above.
            query = query.with_for_update(read=True, of=AmazonOrderLine).execution_options(
                populate_existing=True
            )
        return [(row[0], row[1]) for row in self.session.execute(query)]
