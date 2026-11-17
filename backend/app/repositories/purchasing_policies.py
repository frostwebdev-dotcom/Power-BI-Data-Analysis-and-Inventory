"""Tenant-scoped policy history and stable identity reads."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.models import (
    MarketplaceListing,
    Product,
    PurchasingPolicyRevision,
    User,
    Vendor,
    VendorProduct,
)
from app.models.enums import Marketplace
from app.repositories.scoping import ScopedRepository


class PurchasingPolicyRepository(ScopedRepository):
    def approval_owners(self, identifiers: set[uuid.UUID]) -> set[uuid.UUID]:
        return set(
            self.session.scalars(
                self.select(User, User.id)
                .where(User.id.in_(identifiers))
                .with_for_update(read=True)
            )
        )

    def lock(self, product_id: uuid.UUID, vendor_product_id: uuid.UUID, marketplace: str) -> None:
        key = (
            f"purchasing-policy:{self.scope.organization_id}:"
            f"{product_id}:{vendor_product_id}:{marketplace}"
        )
        self.session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    def root(self, identifier: uuid.UUID, *, lock: bool = False) -> PurchasingPolicyRevision | None:
        query = self.select(PurchasingPolicyRevision).where(
            PurchasingPolicyRevision.id == identifier, PurchasingPolicyRevision.revision_number == 1
        )
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query.execution_options(populate_existing=True))
        return row if isinstance(row, PurchasingPolicyRevision) else None

    def head(self, policy_id: uuid.UUID) -> PurchasingPolicyRevision:
        query = self.select(PurchasingPolicyRevision).where(
            PurchasingPolicyRevision.policy_id == policy_id
        )
        row = self.session.scalar(
            query.order_by(PurchasingPolicyRevision.revision_number.desc()).limit(1)
        )
        assert isinstance(row, PurchasingPolicyRevision)
        return row

    def decision(
        self, policy_id: uuid.UUID, *, through_revision: int | None = None
    ) -> PurchasingPolicyRevision | None:
        query = self.select(PurchasingPolicyRevision).where(
            PurchasingPolicyRevision.policy_id == policy_id,
            PurchasingPolicyRevision.kind != "DRAFT",
        )
        if through_revision is not None:
            query = query.where(PurchasingPolicyRevision.revision_number <= through_revision)
        row = self.session.scalar(
            query.order_by(PurchasingPolicyRevision.revision_number.desc()).limit(1)
        )
        return row if isinstance(row, PurchasingPolicyRevision) else None

    def revision(
        self, policy_id: uuid.UUID, identifier: uuid.UUID
    ) -> PurchasingPolicyRevision | None:
        row = self.session.scalar(
            self.select(PurchasingPolicyRevision).where(
                PurchasingPolicyRevision.policy_id == policy_id,
                PurchasingPolicyRevision.id == identifier,
            )
        )
        return row if isinstance(row, PurchasingPolicyRevision) else None

    def page(
        self,
        page: int,
        size: int,
        *,
        policy_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
    ) -> tuple[list[PurchasingPolicyRevision], int]:
        query = self.select(PurchasingPolicyRevision)
        if policy_id is None:
            query = query.where(PurchasingPolicyRevision.revision_number == 1)
        else:
            query = query.where(PurchasingPolicyRevision.policy_id == policy_id)
        if product_id is not None:
            query = query.where(PurchasingPolicyRevision.product_id == product_id)
        total = self.session.scalar(query.with_only_columns(func.count())) or 0
        rows = self.session.scalars(
            query.order_by(
                PurchasingPolicyRevision.created_at.desc(),
                PurchasingPolicyRevision.revision_number.desc(),
                PurchasingPolicyRevision.id,
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(rows), total

    def owned(
        self,
        model: type[Product] | type[User] | type[Vendor] | type[VendorProduct],
        identifier: uuid.UUID,
    ) -> Any:
        return self.session.scalar(
            self.select(model)
            .where(model.id == identifier)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    def listings(self, product_id: uuid.UUID, marketplace: str) -> list[MarketplaceListing]:
        return list(
            self.session.scalars(
                self.select(MarketplaceListing)
                .where(
                    MarketplaceListing.product_id == product_id,
                    MarketplaceListing.marketplace == Marketplace.AMAZON,
                    MarketplaceListing.marketplace_id == marketplace,
                )
                .order_by(MarketplaceListing.id)
                .limit(201)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )
