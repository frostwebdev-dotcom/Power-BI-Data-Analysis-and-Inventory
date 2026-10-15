"""The canonical product catalog and everything that identifies a product.

Identity rules that this module encodes (CLAUDE.md §5):

* ``products.id`` is the immutable internal UUID and the only foreign-key
  target. ``catalog_item_number`` is a unique business attribute, never a key.
* One catalog item may have many Amazon SKUs, so each SKU is its own row in
  ``marketplace_listings`` — never several values packed into one column.
* ``product_identifiers`` is the unified lookup surface that match priorities 1
  and 2 read, and it carries source-system, vendor, and marketplace context.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    IdentifierType,
    ListingStatus,
    MappingStatus,
    Marketplace,
    MatchMethod,
    ProductStatus,
    SourceSystem,
)
from app.models.mixins import (
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.identity import User
    from app.models.vendor import Vendor


class Product(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A catalog item, sourced from Nineyard.

    Products are deactivated, never deleted, so historical import rows, mappings
    and inventory snapshots stay interpretable (ADR 0002).
    """

    __tablename__ = "products"

    catalog_item_number: Mapped[str] = mapped_column(nullable=False)
    # Nineyard's numeric API ItemId is not the client's Product ID / catalogue number.
    nineyard_item_id: Mapped[str | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(nullable=False)
    brand: Mapped[str | None] = mapped_column(nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    pack_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_of_measure: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        pg_enum(ProductStatus, "product_status"),
        nullable=False,
        server_default=ProductStatus.ACTIVE.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    nineyard_last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    identifiers: Mapped[list[ProductIdentifier]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    marketplace_listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The Nineyard Catalog Item Number is the primary business reference and
        # is unique within a tenant.
        Index(
            "uq_products_organization_id_catalog_item_number",
            "organization_id",
            "catalog_item_number",
            unique=True,
        ),
        Index(
            "uq_products_organization_id_nineyard_item_id",
            "organization_id",
            "nineyard_item_id",
            unique=True,
        ),
        # Product-name search. The btree serves prefix and exact lookups; the
        # trigram GIN index is what makes `name ILIKE '%...%'` usable on a large
        # catalog. Declared here, not only in the migration, so a later
        # autogenerate does not decide it is stray and drop it.
        Index("ix_products_organization_id_name", "organization_id", "name"),
        Index(
            "ix_products_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_products_organization_id_brand", "organization_id", "brand"),
        Index("ix_products_organization_id_status", "organization_id", "status"),
        CheckConstraint(
            "length(trim(catalog_item_number)) > 0", name="catalog_item_number_not_blank"
        ),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("pack_size is null or pack_size > 0", name="pack_size_positive"),
    )


class MarketplaceListing(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One marketplace SKU for a product.

    A catalog item may have many Amazon SKUs, so this is a one-to-many table;
    packing multiple SKUs into a single column would make match priority 4
    unimplementable and the data unqueryable.

    ``mapping_status`` is the approved Amazon-SKU mapping that match priority 4
    reads. It changes only through an explicit human action, or through
    priority 4 itself when the catalog source already names the SKU
    (CLAUDE.md §5.2). ``product_id`` is set only by a mapping — ``PENDING``
    carries the candidate a reviewer sees, ``APPROVED`` the permanent link —
    and is null while ``UNMAPPED``, exactly as on ``vendor_products``.
    """

    __tablename__ = "marketplace_listings"

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    marketplace: Mapped[Marketplace] = mapped_column(
        pg_enum(Marketplace, "marketplace"), nullable=False
    )
    marketplace_id: Mapped[str] = mapped_column(nullable=False)
    seller_sku: Mapped[str] = mapped_column(nullable=False)
    asin: Mapped[str | None] = mapped_column(nullable=True)
    listing_status: Mapped[ListingStatus] = mapped_column(
        pg_enum(ListingStatus, "listing_status"),
        nullable=False,
        server_default=ListingStatus.UNKNOWN.value,
    )
    mapping_status: Mapped[MappingStatus] = mapped_column(
        pg_enum(MappingStatus, "mapping_status"),
        nullable=False,
        server_default=MappingStatus.UNMAPPED.value,
    )
    # Which rule in the priority chain produced the mapping (CLAUDE.md §5.1).
    mapping_method: Mapped[MatchMethod | None] = mapped_column(
        pg_enum(MatchMethod, "match_method"), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # The listing row as the marketplace reported it — the columns the
    # ingestion chose to keep. Display only; never an input to matching.
    raw: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    product: Mapped[Product | None] = relationship(back_populates="marketplace_listings")
    approved_by: Mapped[User | None] = relationship()

    __table_args__ = (
        Index(
            "uq_marketplace_listings_marketplace_id_seller_sku",
            "organization_id",
            "marketplace",
            "marketplace_id",
            "seller_sku",
            unique=True,
        ),
        Index("ix_marketplace_listings_product_id", "product_id"),
        Index("ix_marketplace_listings_organization_id_asin", "organization_id", "asin"),
        Index(
            "ix_marketplace_listings_organization_id_mapping_status",
            "organization_id",
            "mapping_status",
        ),
        # An approved mapping must record who approved it and when. Accountability
        # for a permanent mapping is not optional.
        CheckConstraint(
            "mapping_status <> 'APPROVED'"
            " or (approved_by_user_id is not null and approved_at is not null)",
            name="approved_requires_approver",
        ),
        # An approved mapping must point at a product; a product may only be
        # attached through a real mapping — mirrors vendor_products.
        CheckConstraint(
            "mapping_status <> 'APPROVED' or product_id is not null",
            name="approved_requires_product",
        ),
        CheckConstraint(
            "product_id is null or mapping_status <> 'UNMAPPED'",
            name="mapped_row_is_not_unmapped",
        ),
        CheckConstraint("length(trim(seller_sku)) > 0", name="seller_sku_not_blank"),
    )


class ProductIdentifier(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Any identifier that resolves to a product.

    ``raw_value`` is exactly what the source supplied; ``normalized_value`` is
    the canonical comparison form. Both are kept — normalising in place would
    destroy the evidence needed to debug a bad match (ADR 0004's principle
    applied to identifiers).

    Context columns carry the identifier's scope: a ``VENDOR_SKU`` is only
    meaningful for one vendor, an ``AMAZON_SKU`` only for one listing. The check
    constraint below makes an inconsistent combination impossible.
    """

    __tablename__ = "product_identifiers"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    identifier_type: Mapped[IdentifierType] = mapped_column(
        pg_enum(IdentifierType, "identifier_type"), nullable=False
    )
    raw_value: Mapped[str] = mapped_column(nullable=False)
    normalized_value: Mapped[str] = mapped_column(nullable=False)
    source_system: Mapped[SourceSystem] = mapped_column(
        pg_enum(SourceSystem, "source_system"), nullable=False
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=True
    )
    marketplace_listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=True
    )
    # NULL where a check digit does not apply. A false value disqualifies the
    # identifier from automatic priority-1 matching but does not delete it.
    has_valid_checksum: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    product: Mapped[Product] = relationship(back_populates="identifiers")
    vendor: Mapped[Vendor | None] = relationship()
    marketplace_listing: Mapped[MarketplaceListing | None] = relationship()

    __table_args__ = (
        # Business identifiers stay unique. Barcodes may be shared by products
        # in the source; retain every owner so priority 1 reports ambiguity.
        Index(
            "uq_product_identifiers_global_value",
            "organization_id",
            "identifier_type",
            "normalized_value",
            unique=True,
            postgresql_where=text(
                "is_active and vendor_id is null and marketplace_listing_id is null"
                " and identifier_type not in ('UPC', 'EAN', 'GTIN')"
            ),
        ),
        Index(
            "uq_product_identifiers_barcode_value",
            "organization_id",
            "product_id",
            "identifier_type",
            "normalized_value",
            unique=True,
            postgresql_where=text("is_active and identifier_type in ('UPC', 'EAN', 'GTIN')"),
        ),
        # A vendor SKU is unique within its vendor, not globally: two vendors
        # may legitimately use the same SKU string for different products.
        Index(
            "uq_product_identifiers_vendor_value",
            "organization_id",
            "vendor_id",
            "identifier_type",
            "normalized_value",
            unique=True,
            postgresql_where=text("is_active and vendor_id is not null"),
        ),
        Index(
            "uq_product_identifiers_listing_value",
            "organization_id",
            "marketplace_listing_id",
            "identifier_type",
            "normalized_value",
            unique=True,
            postgresql_where=text("is_active and marketplace_listing_id is not null"),
        ),
        # Lookup path for the matching engine.
        Index(
            "ix_product_identifiers_type_normalized_value",
            "organization_id",
            "identifier_type",
            "normalized_value",
        ),
        Index("ix_product_identifiers_product_id", "product_id"),
        # An AMAZON_SKU may exist before any listing does: the catalog source
        # (Nineyard) names the SKU, and the listing arrives later from Amazon.
        # Unlinked, it is global within the tenant (the first unique index
        # above) — which is exactly what match priority 4 needs. Linked, it is
        # scoped to its listing. It never carries a vendor.
        CheckConstraint(
            "(identifier_type = 'VENDOR_SKU' and vendor_id is not null"
            " and marketplace_listing_id is null)"
            " or (identifier_type = 'AMAZON_SKU' and vendor_id is null)"
            " or (identifier_type not in ('VENDOR_SKU', 'AMAZON_SKU')"
            " and vendor_id is null and marketplace_listing_id is null)",
            name="context_matches_identifier_type",
        ),
        CheckConstraint("length(trim(normalized_value)) > 0", name="normalized_value_not_blank"),
    )
