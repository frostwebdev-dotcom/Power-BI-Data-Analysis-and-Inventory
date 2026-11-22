"""Tenant-scoped financial persistence and exact approved Amazon SKU lookup."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased

from app.models.catalog import MarketplaceListing, Product
from app.models.enums import MappingStatus, Marketplace, ProductStatus
from app.models.financial import (
    FinancialBindingRevision,
    FinancialBindingRow,
    FinancialImport,
    FinancialImportRow,
)
from app.models.identity import User
from app.repositories.scoping import ScopedRepository


class FinancialRepository(ScopedRepository):
    def successors(self, identifiers: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        return {
            row[0]: row[1]
            for row in self.session.execute(
                self.select(
                    FinancialImport, FinancialImport.supersedes_import_id, FinancialImport.id
                ).where(FinancialImport.supersedes_import_id.in_(identifiers))
            )
        }

    def has_successor(self, identifier: uuid.UUID) -> bool:
        return bool(self.successors([identifier]))

    def binding_head(self, identifier: uuid.UUID) -> FinancialBindingRevision | None:
        row = self.session.scalar(
            self.select(FinancialBindingRevision)
            .where(FinancialBindingRevision.import_id == identifier)
            .order_by(FinancialBindingRevision.revision_number.desc())
            .limit(1)
        )
        return row if isinstance(row, FinancialBindingRevision) else None

    def binding_revision(
        self, identifier: uuid.UUID, revision_id: uuid.UUID
    ) -> FinancialBindingRevision | None:
        row = self.session.scalar(
            self.select(FinancialBindingRevision).where(
                FinancialBindingRevision.import_id == identifier,
                FinancialBindingRevision.id == revision_id,
            )
        )
        return row if isinstance(row, FinancialBindingRevision) else None

    def binding_history(
        self, identifier: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[FinancialBindingRevision], int]:
        query = self.select(FinancialBindingRevision).where(
            FinancialBindingRevision.import_id == identifier
        )
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        return list(
            self.session.scalars(
                query.order_by(FinancialBindingRevision.revision_number.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ), total

    def evaluation_rows(
        self, identifier: uuid.UUID, skus: set[str], revision_id: uuid.UUID | None
    ) -> tuple[list[FinancialImportRow], dict[uuid.UUID, FinancialBindingRow]]:
        """Bound the known-product lookup; overflow is reported, never truncated totals."""
        rows = list(
            self.session.scalars(
                self.select(FinancialImportRow)
                .where(
                    FinancialImportRow.import_id == identifier,
                    FinancialImportRow.seller_sku.in_(skus),
                    FinancialImportRow.row_kind == "SKU",
                )
                .order_by(FinancialImportRow.source_row_number)
                .limit(201)
            )
        )
        bindings = (
            {}
            if revision_id is None
            else {
                row.source_row_id: row
                for row in self.session.scalars(
                    self.select(FinancialBindingRow).where(
                        FinancialBindingRow.import_id == identifier,
                        FinancialBindingRow.revision_id == revision_id,
                        FinancialBindingRow.source_row_id.in_([row.id for row in rows]),
                    )
                )
            }
        )
        return rows, bindings

    def source_rows(self, identifier: uuid.UUID) -> list[FinancialImportRow]:
        return list(
            self.session.scalars(
                self.select(FinancialImportRow)
                .where(FinancialImportRow.import_id == identifier)
                .order_by(FinancialImportRow.source_row_number)
            )
        )

    def binding_rows(self, revision_id: uuid.UUID) -> dict[uuid.UUID, FinancialBindingRow]:
        return {
            row.source_row_id: row
            for row in self.session.scalars(
                self.select(FinancialBindingRow).where(
                    FinancialBindingRow.revision_id == revision_id
                )
            )
        }

    def revised_rows(
        self,
        identifier: uuid.UUID,
        revision_id: uuid.UUID,
        page: int,
        page_size: int,
        *,
        review_only: bool = False,
        mapping_status: str | None = None,
    ) -> tuple[list[tuple[FinancialImportRow, FinancialBindingRow]], int]:
        query = (
            self.select(FinancialImportRow, FinancialImportRow, FinancialBindingRow)
            .join(FinancialBindingRow, FinancialBindingRow.source_row_id == FinancialImportRow.id)
            .where(
                FinancialImportRow.import_id == identifier,
                FinancialBindingRow.organization_id == self.scope.organization_id,
                FinancialBindingRow.import_id == identifier,
                FinancialBindingRow.revision_id == revision_id,
            )
        )
        if review_only:
            query = query.where(FinancialBindingRow.review_required.is_(True))
        if mapping_status is not None:
            query = query.where(FinancialBindingRow.mapping_status == mapping_status)
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        result = self.session.execute(
            query.order_by(FinancialImportRow.source_row_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(r[0], r[1]) for r in result], total

    def add_binding_revision(
        self, revision: FinancialBindingRevision, rows: list[FinancialBindingRow]
    ) -> None:
        if revision.organization_id != self.scope.organization_id or any(
            row.organization_id != self.scope.organization_id
            or row.revision_id != revision.id
            or row.import_id != revision.import_id
            for row in rows
        ):
            raise ValueError("binding revisions must belong to their repository scope")
        self.session.add(revision)
        self.session.flush()
        self.session.add_all(rows)
        self.session.flush()

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

    def history(
        self, page: int, page_size: int, *, current_only: bool = False
    ) -> tuple[list[FinancialImport], int]:
        query = self.select(FinancialImport)
        if current_only:
            successor = aliased(FinancialImport)
            query = query.where(
                ~exists(
                    select(successor.id).where(
                        successor.supersedes_import_id == FinancialImport.id,
                        successor.organization_id == self.scope.organization_id,
                    )
                )
            )
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
