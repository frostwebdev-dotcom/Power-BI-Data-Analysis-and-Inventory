"""Immutable financial imports, deliberately separate from vendor stock ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class FinancialImport(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "financial_imports"

    original_filename: Mapped[str] = mapped_column(String(255))
    storage_uri: Mapped[str]
    raw_sha256: Mapped[str] = mapped_column(String(64))
    profile_sha256: Mapped[str] = mapped_column(String(64))
    profile_snapshot: Mapped[dict[str, Any]]
    report_snapshot: Mapped[dict[str, Any]]
    marketplace_id: Mapped[str] = mapped_column(String(64))
    store_name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    received_at: Mapped[datetime]
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer)
    linked_rows: Mapped[int] = mapped_column(Integer)
    review_rows: Mapped[int] = mapped_column(Integer)
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    recording_transaction_id: Mapped[int] = mapped_column(
        BigInteger, server_default=text("txid_current()")
    )
    supersedes_import_id: Mapped[uuid.UUID | None]
    replacement_reason: Mapped[str | None] = mapped_column(String(1000))
    replacement_preview_sha256: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("organization_id", "raw_sha256"),
        UniqueConstraint("supersedes_import_id"),
        ForeignKeyConstraint(
            ["supersedes_import_id", "organization_id"],
            ["financial_imports.id", "financial_imports.organization_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(supersedes_import_id is null and replacement_reason is null "
            "and replacement_preview_sha256 is null) or (supersedes_import_id is not null "
            "and replacement_reason is not null "
            "and length(btrim(replacement_reason)) between 10 and 1000 "
            "and replacement_preview_sha256 is not null "
            "and replacement_preview_sha256 ~ '^[0-9a-f]{64}$')",
            name="valid_replacement",
        ),
        Index(
            "ix_financial_imports_period",
            "organization_id",
            "marketplace_id",
            "period_start",
            "period_end",
        ),
        CheckConstraint("period_end > period_start", name="positive_period"),
        CheckConstraint("currency = 'USD'", name="supported_currency"),
        CheckConstraint(
            "raw_sha256 ~ '^[0-9a-f]{64}$' and profile_sha256 ~ '^[0-9a-f]{64}$'",
            name="valid_digests",
        ),
        CheckConstraint("status in ('RECORDED', 'REVIEW_REQUIRED')", name="valid_status"),
        CheckConstraint(
            "row_count > 0 and linked_rows between 0 and row_count "
            "and review_rows between 0 and row_count",
            name="valid_counters",
        ),
        CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )


class FinancialImportRow(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "financial_import_rows"

    import_id: Mapped[uuid.UUID]
    source_row_number: Mapped[int] = mapped_column(Integer)
    seller_sku: Mapped[str | None]
    row_kind: Mapped[str] = mapped_column(String(16))
    mapping_status: Mapped[str] = mapped_column(String(32))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    match_priority: Mapped[int | None] = mapped_column(Integer)
    mapping_snapshot: Mapped[dict[str, Any]]
    source_snapshot: Mapped[dict[str, Any]]
    review_reasons: Mapped[list[str]]
    review_required: Mapped[bool] = mapped_column(Boolean)
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    net_sales: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))
    fees: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))
    ad_spend: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))
    reported_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))
    computed_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 16))

    __table_args__ = (
        UniqueConstraint("id", "import_id", "organization_id"),
        ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["financial_imports.id", "financial_imports.organization_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("import_id", "source_row_number"),
        Index(
            "ix_financial_import_rows_review",
            "organization_id",
            "import_id",
            "review_required",
            "source_row_number",
        ),
        Index("ix_financial_import_rows_product_id", "product_id"),
        Index("ix_financial_import_rows_listing_id", "listing_id"),
        CheckConstraint("source_row_number > 1", name="positive_source_row"),
        CheckConstraint(
            "mapping_status in ('LINKED', 'UNRESOLVED', 'UNALLOCATED', "
            "'INVALID_SOURCE', 'ASIN_CONFLICT')",
            name="valid_mapping_status",
        ),
        CheckConstraint(
            "(row_kind = 'UNALLOCATED' and seller_sku is null "
            "and mapping_status = 'UNALLOCATED') "
            "or (row_kind = 'SKU' and mapping_status <> 'UNALLOCATED')",
            name="valid_row_kind",
        ),
        CheckConstraint(
            "(mapping_status = 'LINKED' and product_id is not null "
            "and listing_id is not null and match_priority is not null and match_priority = 4) "
            "or (mapping_status <> 'LINKED' and product_id is null "
            "and listing_id is null and match_priority is null)",
            name="approved_links_only",
        ),
        CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )


class FinancialBindingRevision(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "financial_binding_revisions"

    import_id: Mapped[uuid.UUID]
    revision_number: Mapped[int] = mapped_column(Integer)
    supersedes_revision_id: Mapped[uuid.UUID | None]
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(1000))
    bindings_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer)
    linked_rows: Mapped[int] = mapped_column(Integer)
    review_rows: Mapped[int] = mapped_column(Integer)
    changed_rows: Mapped[int] = mapped_column(Integer)
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    recording_transaction_id: Mapped[int] = mapped_column(
        BigInteger, server_default=text("txid_current()")
    )

    __table_args__ = (
        UniqueConstraint("id", "import_id", "organization_id"),
        UniqueConstraint("import_id", "revision_number"),
        UniqueConstraint("supersedes_revision_id"),
        ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["financial_imports.id", "financial_imports.organization_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_revision_id", "import_id", "organization_id"],
            [
                "financial_binding_revisions.id",
                "financial_binding_revisions.import_id",
                "financial_binding_revisions.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision_number > 0", name="positive_revision"),
        CheckConstraint("length(btrim(reason)) between 10 and 1000", name="reason_required"),
        CheckConstraint("bindings_sha256 ~ '^[0-9a-f]{64}$'", name="valid_digest"),
        CheckConstraint("status in ('RECORDED', 'REVIEW_REQUIRED')", name="valid_status"),
        CheckConstraint(
            "row_count > 0 and linked_rows between 0 and row_count "
            "and review_rows between 0 and row_count and changed_rows between 1 and row_count",
            name="valid_counters",
        ),
        CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )


class FinancialBindingRow(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "financial_binding_rows"

    revision_id: Mapped[uuid.UUID]
    import_id: Mapped[uuid.UUID]
    source_row_id: Mapped[uuid.UUID]
    mapping_status: Mapped[str] = mapped_column(String(32))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    match_priority: Mapped[int | None] = mapped_column(Integer)
    mapping_snapshot: Mapped[dict[str, Any]]
    review_reasons: Mapped[list[str]]
    review_required: Mapped[bool] = mapped_column(Boolean)
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "import_id", "organization_id"],
            [
                "financial_binding_revisions.id",
                "financial_binding_revisions.import_id",
                "financial_binding_revisions.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_row_id", "import_id", "organization_id"],
            [
                "financial_import_rows.id",
                "financial_import_rows.import_id",
                "financial_import_rows.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("revision_id", "source_row_id"),
        Index(
            "ix_financial_binding_rows_review", "organization_id", "revision_id", "review_required"
        ),
        CheckConstraint(
            "mapping_status in ('LINKED', 'UNRESOLVED', 'UNALLOCATED', "
            "'INVALID_SOURCE', 'ASIN_CONFLICT')",
            name="valid_mapping_status",
        ),
        CheckConstraint(
            "(mapping_status = 'LINKED' and product_id is not null and listing_id is not null "
            "and match_priority is not null and match_priority = 4) "
            "or (mapping_status <> 'LINKED' and product_id is null "
            "and listing_id is null and match_priority is null)",
            name="approved_links_only",
        ),
        CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )
