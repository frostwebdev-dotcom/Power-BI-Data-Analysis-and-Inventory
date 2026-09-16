"""Builds the engine's lookups from the database (ADR 0012 scoped).

The index is built once per import so evaluating 20,000 rows costs no
queries. Only what the rules may read goes in: checksum-valid GTIN
identifiers, active products' catalog item numbers, **approved** vendor-SKU
mappings for the importing vendor, **approved** Amazon listings. A pending
or rejected mapping is not a mapping (CLAUDE.md §5.2).

Rule 5's suggestions come from ``pg_trgm`` similarity on ``products.name``
through ``ix_products_name_trgm``; they are the one thing looked up per row,
and only for rows nothing else could place.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func

from app.matching.engine import (
    SUGGESTION_LIMIT,
    SUGGESTION_THRESHOLD,
    MatchIndex,
    Suggestion,
    normalize_catalog_item_number,
)
from app.models.catalog import MarketplaceListing, Product, ProductIdentifier
from app.models.enums import IdentifierType, MappingStatus
from app.models.vendor import VendorProduct
from app.repositories.scoping import ScopedRepository

GTIN_IDENTIFIER_TYPES = (IdentifierType.UPC, IdentifierType.EAN, IdentifierType.GTIN)


class MatchIndexRepository(ScopedRepository):
    def build(self, vendor_id: uuid.UUID) -> MatchIndex:
        by_upc: defaultdict[str, set[uuid.UUID]] = defaultdict(set)
        for value, product_id in self.session.execute(
            self.select(
                ProductIdentifier, ProductIdentifier.normalized_value, ProductIdentifier.product_id
            ).where(
                ProductIdentifier.identifier_type.in_(GTIN_IDENTIFIER_TYPES),
                ProductIdentifier.is_active.is_(True),
                # An identifier whose own check digit failed cannot drive priority 1.
                ProductIdentifier.has_valid_checksum.isnot(False),
            )
        ).all():
            by_upc[value].add(product_id)

        by_catalog: defaultdict[str, set[uuid.UUID]] = defaultdict(set)
        for number, product_id in self.session.execute(
            self.select(Product, Product.catalog_item_number, Product.id).where(
                Product.is_active.is_(True)
            )
        ).all():
            by_catalog[normalize_catalog_item_number(number)].add(product_id)

        by_vendor_sku: defaultdict[tuple[uuid.UUID, str], set[uuid.UUID]] = defaultdict(set)
        for sku, product_id in self.session.execute(
            self.select(
                VendorProduct, VendorProduct.normalized_vendor_sku, VendorProduct.product_id
            ).where(
                VendorProduct.vendor_id == vendor_id,
                VendorProduct.mapping_status == MappingStatus.APPROVED,
                VendorProduct.product_id.is_not(None),
            )
        ).all():
            by_vendor_sku[(vendor_id, sku)].add(product_id)

        by_amazon_sku: defaultdict[str, set[uuid.UUID]] = defaultdict(set)
        for sku, product_id in self.session.execute(
            self.select(
                MarketplaceListing, MarketplaceListing.seller_sku, MarketplaceListing.product_id
            ).where(
                MarketplaceListing.mapping_status == MappingStatus.APPROVED,
                MarketplaceListing.product_id.is_not(None),
            )
        ).all():
            by_amazon_sku[sku.strip()].add(product_id)

        return MatchIndex(
            by_upc={k: tuple(sorted(v)) for k, v in by_upc.items()},
            by_catalog_item_number={k: tuple(sorted(v)) for k, v in by_catalog.items()},
            by_vendor_sku={k: tuple(sorted(v)) for k, v in by_vendor_sku.items()},
            by_amazon_sku={k: tuple(sorted(v)) for k, v in by_amazon_sku.items()},
            suggester=self.suggest_by_description,
        )

    def suggest_by_description(self, description: str) -> list[Suggestion]:
        """Rule 5: the closest product names by trigram similarity, scored.
        Ordered by score then id so equal scores come back in one order."""
        score = func.similarity(Product.name, description)
        rows = self.session.execute(
            self.select(Product, Product.id, score)
            .where(Product.is_active.is_(True), score >= SUGGESTION_THRESHOLD)
            .order_by(score.desc(), Product.id)
            .limit(SUGGESTION_LIMIT)
        ).all()
        return [Suggestion(product_id=pid, score=float(s)) for pid, s in rows]
