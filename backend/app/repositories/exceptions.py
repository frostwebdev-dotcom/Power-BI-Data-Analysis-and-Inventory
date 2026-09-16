"""Exception-queue data access, through the tenant scope (ADR 0012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.models.catalog import Product
from app.models.enums import ExceptionReason, ExceptionStatus
from app.models.matching import ProductMappingException
from app.repositories.scoping import ScopedRepository

MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class ExceptionPage:
    items: Sequence[ProductMappingException]
    page: int
    page_size: int
    total: int


class ExceptionRepository(ScopedRepository):
    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: ExceptionStatus | None = ExceptionStatus.PENDING,
        reason: ExceptionReason | None = None,
        vendor_id: uuid.UUID | None = None,
        import_job_id: uuid.UUID | None = None,
        min_age_hours: float | None = None,
        include_deferred: bool = False,
    ) -> ExceptionPage:
        """The queue, oldest first. Deferred items are hidden until their time
        unless asked for."""
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        now = datetime.now(UTC)

        statement = self.select(ProductMappingException)
        if status is not None:
            statement = statement.where(ProductMappingException.status == status)
        if reason is not None:
            statement = statement.where(ProductMappingException.reason == reason)
        if vendor_id is not None:
            statement = statement.where(ProductMappingException.vendor_id == vendor_id)
        if import_job_id is not None:
            statement = statement.where(ProductMappingException.import_job_id == import_job_id)
        if min_age_hours is not None:
            statement = statement.where(
                ProductMappingException.created_at <= now - timedelta(hours=min_age_hours)
            )
        if not include_deferred:
            statement = statement.where(
                or_(
                    ProductMappingException.deferred_until.is_(None),
                    ProductMappingException.deferred_until <= now,
                )
            )

        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.options(
                    selectinload(ProductMappingException.vendor_product),
                    selectinload(ProductMappingException.import_job_row),
                )
                .order_by(ProductMappingException.created_at, ProductMappingException.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return ExceptionPage(items=items, page=page, page_size=page_size, total=total)

    def get(self, exception_id: uuid.UUID) -> ProductMappingException | None:
        return self.session.execute(
            self.select(ProductMappingException)
            .options(
                selectinload(ProductMappingException.vendor_product),
                selectinload(ProductMappingException.marketplace_listing),
                selectinload(ProductMappingException.import_job_row),
                selectinload(ProductMappingException.import_job),
                selectinload(ProductMappingException.suggested_product),
                selectinload(ProductMappingException.resolved_product),
            )
            .where(ProductMappingException.id == exception_id)
        ).scalar_one_or_none()

    def products_by_ids(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Product]:
        if not ids:
            return {}
        rows = self.session.execute(
            self.select(Product).where(Product.id.in_(list(set(ids))))
        ).scalars()
        return {p.id: p for p in rows}

    def active_product(self, product_id: uuid.UUID) -> Product | None:
        return self.session.execute(
            self.select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        ).scalar_one_or_none()
