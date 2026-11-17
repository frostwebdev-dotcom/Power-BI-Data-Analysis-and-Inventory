"""Tenant-scoped financial persistence and exact approved Amazon SKU lookup."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.models.catalog import MarketplaceListing, Product
from app.models.enums import MappingStatus, Marketplace, ProductStatus
from app.models.financial import FinancialImport, FinancialImportRow
from app.models.identity import User
from app.repositories.scoping import ScopedRepository


class FinancialRepository(ScopedRepository):
    def lock_marketplace(self, marketplace_id: str) -> None:
        key = f"financial:{self.scope.organization_id}:{marketplace_id}"
        self.session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    def actor_exists(self, user_id: uuid.UUID) -> bool:
        return (
            self.session.scalar(
                self.select(User, User.id).where(User.id == user_id, User.is_active.is_(True))
            )
            is not None
        )

    def by_digest(self, digest: str) -> FinancialImport | None:
        row = self.session.scalar(
            self.select(FinancialImport).where(FinancialImport.raw_sha256 == digest)
        )
        return row if isinstance(row, FinancialImport) else None

    def get(self, identifier: uuid.UUID) -> FinancialImport | None:
        row = self.session.scalar(
            self.select(FinancialImport).where(FinancialImport.id == identifier)
        )
        return row if isinstance(row, FinancialImport) else None

    def overlap(self, marketplace: str, start: datetime, end: datetime) -> bool:
        return (
            self.session.scalar(
                self.select(FinancialImport, FinancialImport.id)
                .where(
                    FinancialImport.marketplace_id == marketplace,
                    FinancialImport.period_start < end,
                    FinancialImport.period_end > start,
                )
                .limit(1)
            )
            is not None
        )

    def approved_listings(self, marketplace: str, skus: set[str]) -> dict[str, MarketplaceListing]:
        if not skus:
            return {}
        query = (
            self.select(MarketplaceListing)
            .join(Product, Product.id == MarketplaceListing.product_id)
            .where(
                Product.organization_id == self.scope.organization_id,
                Product.is_active.is_(True),
                Product.status == ProductStatus.ACTIVE,
                MarketplaceListing.marketplace == Marketplace.AMAZON,
                MarketplaceListing.marketplace_id == marketplace,
                MarketplaceListing.seller_sku.in_(skus),
                MarketplaceListing.mapping_status == MappingStatus.APPROVED,
                MarketplaceListing.is_active.is_(True),
            )
            .with_for_update(read=True, of=(MarketplaceListing, Product))
        )
        return {row.seller_sku: row for row in self.session.scalars(query)}

    def add(self, batch: FinancialImport, rows: list[FinancialImportRow]) -> None:
        if batch.organization_id != self.scope.organization_id or any(
            r.organization_id != self.scope.organization_id or r.import_id != batch.id for r in rows
        ):
            raise ValueError("financial records must belong to their repository scope")
        self.session.add(batch)
        self.session.flush()
        self.session.add_all(rows)
        self.session.flush()

    def history(self, page: int, page_size: int) -> tuple[list[FinancialImport], int]:
        query = self.select(FinancialImport)
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        return list(
            self.session.scalars(
                query.order_by(FinancialImport.received_at.desc(), FinancialImport.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ), total

    def rows(
        self,
        identifier: uuid.UUID,
        page: int,
        page_size: int,
        *,
        review_only: bool = False,
        mapping_status: str | None = None,
    ) -> tuple[list[FinancialImportRow], int]:
        query = self.select(FinancialImportRow).where(FinancialImportRow.import_id == identifier)
        if review_only:
            query = query.where(FinancialImportRow.review_required.is_(True))
        if mapping_status is not None:
            query = query.where(FinancialImportRow.mapping_status == mapping_status)
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        return list(
            self.session.scalars(
                query.order_by(FinancialImportRow.source_row_number)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ), total
