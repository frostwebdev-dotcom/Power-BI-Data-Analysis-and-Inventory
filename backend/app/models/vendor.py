"""Vendors, their catalogue lines, and their file-parsing configuration.

``vendor_products.product_id`` is the approved vendor-SKU mapping that match
priority 3 reads. It lives on the vendor product rather than in a separate
mapping table because the mapping is a property of that one vendor SKU — see
ADR 0010.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    FileFormat,
    MappingStatus,
    MatchMethod,
    VendorStatus,
)
from app.models.mixins import (
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.identity import User
    from app.models.organization import Organization


class Vendor(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A supplier."""

    __tablename__ = "vendors"

    code: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[VendorStatus] = mapped_column(
        pg_enum(VendorStatus, "vendor_status"),
        nullable=False,
        server_default=VendorStatus.ACTIVE.value,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    # Governs how vendor-supplied local dates are interpreted during extraction.
    # Storage is always UTC (risk R15).
    timezone: Mapped[str] = mapped_column(nullable=False, server_default=text("'UTC'"))
    default_lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    # Minimum order requirements, as the vendor states them. Either may be
    # absent; neither is ever negative. Milestone 1 records them and makes no
    # purchasing decision with them (milestone-1-scope.md §1).
    minimum_order_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_order_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Free-form purchasing terms — payment terms, freight, cut-off times —
    # kept as the operator entered them. No key is interpreted yet.
    purchasing_terms: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    organization: Mapped[Organization] = relationship(back_populates="vendors")
    contacts: Mapped[list[VendorContact]] = relationship(
        back_populates="vendor", order_by="VendorContact.created_at"
    )
    vendor_products: Mapped[list[VendorProduct]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
    import_profiles: Mapped[list[VendorImportProfile]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("uq_vendors_organization_id_code", "organization_id", "code", unique=True),
        Index("ix_vendors_organization_id_name", "organization_id", "name"),
        # Trigram index for substring vendor-name search. Declared here so a
        # later autogenerate does not treat it as stray and drop it.
        Index(
            "ix_vendors_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_vendors_organization_id_status", "organization_id", "status"),
        CheckConstraint("code = upper(code)", name="code_is_uppercase"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("currency = upper(currency)", name="currency_is_uppercase"),
        CheckConstraint("length(currency) = 3", name="currency_is_iso_4217_length"),
        CheckConstraint(
            "default_lead_time_days is null or default_lead_time_days >= 0",
            name="lead_time_not_negative",
        ),
        CheckConstraint(
            "minimum_order_quantity is null or minimum_order_quantity >= 0",
            name="minimum_order_quantity_not_negative",
        ),
        CheckConstraint(
            "minimum_order_value is null or minimum_order_value >= 0",
            name="minimum_order_value_not_negative",
        ),
    )


class VendorContact(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A person at a vendor — the "email address(es)" the brief asks for.

    ``RESTRICT`` on the vendor: contacts are deactivated with their vendor,
    never dropped by a cascade nobody reviewed. Email is unique per vendor
    case-insensitively (a functional index on ``lower(email)``), and at most
    one *active* contact is the primary one.
    """

    __tablename__ = "vendor_contacts"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(nullable=False)
    # Free text: "Sales rep", "Accounts receivable" — the vendor's vocabulary.
    role: Mapped[str | None] = mapped_column(nullable=True)
    phone: Mapped[str | None] = mapped_column(nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    vendor: Mapped[Vendor] = relationship(back_populates="contacts")

    __table_args__ = (
        Index(
            "uq_vendor_contacts_vendor_id_lower_email",
            "vendor_id",
            func.lower(text("email")),
            unique=True,
        ),
        Index(
            "uq_vendor_contacts_primary",
            "vendor_id",
            unique=True,
            postgresql_where=text("is_primary and is_active"),
        ),
        Index("ix_vendor_contacts_vendor_id", "vendor_id"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("position('@' in email) > 1", name="email_looks_like_an_address"),
    )


class VendorProduct(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One line in a vendor's catalogue, and its approved mapping to a product.

    ``product_id`` is set only by an approved mapping. Until then it is NULL and
    the row's inventory is still recorded — signal is never discarded just
    because identity is unresolved.
    """

    __tablename__ = "vendor_products"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    vendor_sku: Mapped[str] = mapped_column(nullable=False)
    normalized_vendor_sku: Mapped[str] = mapped_column(nullable=False)
    vendor_description: Mapped[str | None] = mapped_column(nullable=True)
    raw_upc: Mapped[str | None] = mapped_column(nullable=True)
    normalized_upc: Mapped[str | None] = mapped_column(nullable=True)
    pack_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_of_measure: Mapped[str | None] = mapped_column(nullable=True)

    # --- Approved mapping (match priority 3) ---------------------------------
    # RESTRICT: a product that vendor lines point at cannot be deleted. Products
    # are deactivated instead.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    mapping_status: Mapped[MappingStatus] = mapped_column(
        pg_enum(MappingStatus, "mapping_status"),
        nullable=False,
        server_default=MappingStatus.UNMAPPED.value,
    )
    mapping_method: Mapped[MatchMethod | None] = mapped_column(
        pg_enum(MatchMethod, "match_method"), nullable=True
    )
    mapping_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    mapping_approved_at: Mapped[datetime | None] = mapped_column(nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    vendor: Mapped[Vendor] = relationship(back_populates="vendor_products")
    product: Mapped[Product | None] = relationship()
    mapping_approved_by: Mapped[User | None] = relationship()

    __table_args__ = (
        Index(
            "uq_vendor_products_vendor_id_vendor_sku",
            "organization_id",
            "vendor_id",
            "vendor_sku",
            unique=True,
        ),
        # Vendor-SKU search.
        Index(
            "ix_vendor_products_normalized_vendor_sku",
            "organization_id",
            "normalized_vendor_sku",
        ),
        # UPC search on the vendor side.
        Index(
            "ix_vendor_products_normalized_upc",
            "organization_id",
            "normalized_upc",
            postgresql_where=text("normalized_upc is not null"),
        ),
        Index("ix_vendor_products_product_id", "product_id"),
        Index(
            "ix_vendor_products_organization_id_mapping_status",
            "organization_id",
            "mapping_status",
        ),
        # An approved mapping must point at a product and name its approver.
        CheckConstraint(
            "mapping_status <> 'APPROVED'"
            " or (product_id is not null and mapping_approved_at is not null)",
            name="approved_mapping_requires_product",
        ),
        # The converse: a product may only be attached through a real mapping
        # state, never left dangling on an UNMAPPED row.
        CheckConstraint(
            "product_id is null or mapping_status <> 'UNMAPPED'",
            name="mapped_row_is_not_unmapped",
        ),
        CheckConstraint("length(trim(vendor_sku)) > 0", name="vendor_sku_not_blank"),
        CheckConstraint("pack_size is null or pack_size > 0", name="pack_size_positive"),
    )


class VendorImportProfile(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Versioned, per-vendor description of how to read that vendor's file.

    Profiles are data, not code, so onboarding a vendor needs no deployment
    (ADR 0005). Editing one creates a new version; older import jobs keep
    referencing the version they ran under, so a historical import stays
    interpretable.
    """

    __tablename__ = "vendor_import_profiles"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    file_format: Mapped[FileFormat] = mapped_column(
        pg_enum(FileFormat, "file_format"), nullable=False
    )
    encoding: Mapped[str | None] = mapped_column(nullable=True)
    delimiter: Mapped[str | None] = mapped_column(String(4), nullable=True)
    quote_char: Mapped[str | None] = mapped_column(String(1), nullable=True)
    header_row_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    skip_rows: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    sheet_name: Mapped[str | None] = mapped_column(nullable=True)
    sheet_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    column_map: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    normalization_rules: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    availability_rules: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    quantity_semantics: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    price_semantics: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    pack_size_handling: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # Fingerprint of the expected header row. A file whose header does not match
    # fails the batch outright rather than being mapped positionally (risk R3).
    header_signature: Mapped[str | None] = mapped_column(nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vendor: Mapped[Vendor] = relationship(back_populates="import_profiles")
    created_by: Mapped[User | None] = relationship()

    __table_args__ = (
        Index(
            "uq_vendor_import_profiles_vendor_id_name_version",
            "organization_id",
            "vendor_id",
            "name",
            "version",
            unique=True,
        ),
        # At most one active version of a named profile per vendor, so "which
        # profile applies" is never ambiguous.
        Index(
            "uq_vendor_import_profiles_active_name",
            "organization_id",
            "vendor_id",
            "name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("header_row_index >= 0", name="header_row_index_not_negative"),
        CheckConstraint("skip_rows >= 0", name="skip_rows_not_negative"),
        # A sheet selector only makes sense for a workbook.
        CheckConstraint(
            "file_format = 'XLSX' or (sheet_name is null and sheet_index is null)",
            name="sheet_selector_requires_xlsx",
        ),
        CheckConstraint(
            "sheet_name is null or sheet_index is null",
            name="sheet_selected_by_name_or_index_not_both",
        ),
    )
