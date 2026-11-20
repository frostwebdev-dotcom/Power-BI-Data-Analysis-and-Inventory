"""Bounded, tenant-scoped catalogue reads. Search never creates a mapping."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar, cast

from sqlalchemy import Select, func, or_, select

from app.matching.normalize import normalize_gtin
from app.models.catalog import MarketplaceListing, Product, ProductIdentifier
from app.models.enums import IdentifierType, Marketplace
from app.models.vendor import Vendor, VendorProduct
from app.repositories.scoping import ScopedRepository

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProductPage[T]:
    items: Sequence[T]
    page: int
    page_size: int
    total: int


class ProductRepository(ScopedRepository):
    def _page(self, statement: Select[tuple[T]], page: int, page_size: int) -> ProductPage[T]:
        total = int(
            self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        items = self.session.scalars(
            statement.offset((page - 1) * page_size).limit(page_size)
        ).all()
        return ProductPage(items=items, page=page, page_size=page_size, total=total)

    def list(
        self, *, page: int, page_size: int, q: str | None, include_inactive: bool
    ) -> ProductPage[Product]:
        statement = self.select(Product)
        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        if q and (query := q.strip()):
            # Search text is literal: '%' and '_' must not widen the search.
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            normalized = normalize_gtin(query)
            values = [query]
            if normalized.usable and normalized.canonical is not None:
                values.append(normalized.canonical)
            identifier_hit = (
                self.select(ProductIdentifier, ProductIdentifier.id)
                .where(
                    ProductIdentifier.product_id == Product.id,
                    ProductIdentifier.is_active.is_(True),
                    or_(
                        ProductIdentifier.raw_value == query,
                        ProductIdentifier.normalized_value.in_(values),
                    ),
                    # SKU identifiers are case-sensitive; GTIN normalization only
                    # applies to UPC/EAN/GTIN, never to an ASIN or seller SKU.
                    or_(
                        ProductIdentifier.normalized_value == query,
                        ProductIdentifier.raw_value == query,
                        ProductIdentifier.identifier_type.in_(
                            (IdentifierType.UPC, IdentifierType.EAN, IdentifierType.GTIN)
                        ),
                    ),
                )
                .exists()
            )
            statement = statement.where(
                or_(
                    Product.name.ilike(pattern, escape="\\"),
                    Product.catalog_item_number.ilike(pattern, escape="\\"),
                    identifier_hit,
                )
            )
        return self._page(
            statement.order_by(Product.catalog_item_number, Product.id), page, page_size
        )

    def get(self, product_id: uuid.UUID) -> Product | None:
        return cast(
            Product | None,
            self.session.scalar(self.select(Product).where(Product.id == product_id)),
        )

    def identifiers(
        self, product_id: uuid.UUID, page: int, page_size: int
    ) -> ProductPage[ProductIdentifier]:
        return self._page(
            self.select(ProductIdentifier)
            .where(ProductIdentifier.product_id == product_id)
            .order_by(
                ProductIdentifier.identifier_type,
                ProductIdentifier.normalized_value,
                ProductIdentifier.id,
            ),
            page,
            page_size,
        )

    def listings(
        self,
        product_id: uuid.UUID,
        page: int,
        page_size: int,
        *,
        marketplace: Marketplace | None = None,
        marketplace_id: str | None = None,
    ) -> ProductPage[MarketplaceListing]:
        query = self.select(MarketplaceListing).where(MarketplaceListing.product_id == product_id)
        if marketplace is not None:
            query = query.where(MarketplaceListing.marketplace == marketplace)
        if marketplace_id is not None:
            query = query.where(MarketplaceListing.marketplace_id == marketplace_id)
        return self._page(
            query.order_by(MarketplaceListing.seller_sku, MarketplaceListing.id),
            page,
            page_size,
        )

    def vendor_products(
        self, product_id: uuid.UUID, page: int, page_size: int, q: str | None
    ) -> tuple[Sequence[tuple[VendorProduct, Vendor]], int]:
        query = (
            self.select(VendorProduct, VendorProduct, Vendor)
            .join(
                Vendor,
                (Vendor.id == VendorProduct.vendor_id)
                & (Vendor.organization_id == self.scope.organization_id),
            )
            .where(VendorProduct.product_id == product_id)
        )
        if q and (term := q.strip()):
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    Vendor.name.ilike(pattern, escape="\\"),
                    Vendor.code.ilike(pattern, escape="\\"),
                    VendorProduct.vendor_sku.ilike(pattern, escape="\\"),
                )
            )
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.execute(
            query.order_by(Vendor.name, VendorProduct.vendor_sku, VendorProduct.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(row[0], row[1]) for row in rows], total
