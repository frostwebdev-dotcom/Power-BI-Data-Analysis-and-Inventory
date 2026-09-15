"""Vendor file ingestion: the retained file, the job, and the staged rows.

Idempotency is enforced in two places (both required by the milestone brief):

* ``import_files.sha256`` is unique per tenant, so the same bytes are stored
  once no matter how many times they are uploaded;
* a partial unique index allows only one non-failed ``import_jobs`` row per
  file, so a successful import can never be silently processed twice, while a
  failed or cancelled one can still be retried.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    AvailabilityStatus,
    ImportJobStage,
    ImportJobStatus,
    ImportRowStatus,
    MatchMethod,
    MatchResult,
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
    from app.models.vendor import Vendor, VendorImportProfile, VendorProduct


class ImportFile(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A vendor file, retained byte-for-byte.

    The row records where the bytes are and what they hashed to; the bytes
    themselves are never modified, re-encoded, or deleted by normal processing
    (ADR 0004). The stored object must reproduce ``sha256`` on retrieval.
    """

    __tablename__ = "import_files"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(nullable=False)
    storage_uri: Mapped[str] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_mime: Mapped[str | None] = mapped_column(nullable=True)
    detected_encoding: Mapped[str | None] = mapped_column(nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    vendor: Mapped[Vendor] = relationship()
    uploaded_by: Mapped[User | None] = relationship()
    import_jobs: Mapped[list[ImportJob]] = relationship(back_populates="import_file")

    __table_args__ = (
        # Content-addressed idempotency: identical bytes are one file.
        Index("uq_import_files_organization_id_sha256", "organization_id", "sha256", unique=True),
        Index("ix_import_files_vendor_id_uploaded_at", "vendor_id", "uploaded_at"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_is_lowercase_hex"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_not_negative"),
        CheckConstraint("length(trim(original_filename)) > 0", name="original_filename_not_blank"),
    )


class ImportJob(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One execution of an import against one retained file."""

    __tablename__ = "import_jobs"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
    )
    import_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_files.id", ondelete="RESTRICT"), nullable=False
    )
    vendor_import_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendor_import_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    # Denormalised so the job still records which profile version it ran under
    # even if the profile row is later superseded.
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ImportJobStatus] = mapped_column(
        pg_enum(ImportJobStatus, "import_job_status"),
        nullable=False,
        server_default=ImportJobStatus.PENDING.value,
    )

    #: Progress within RUNNING (phase 6); null when not running.
    current_stage: Mapped[ImportJobStage | None] = mapped_column(
        pg_enum(ImportJobStage, "import_job_stage"), nullable=True
    )

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    matched_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    exception_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    error_message: Mapped[str | None] = mapped_column(nullable=True)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vendor: Mapped[Vendor] = relationship()
    import_file: Mapped[ImportFile] = relationship(back_populates="import_jobs")
    import_profile: Mapped[VendorImportProfile | None] = relationship()
    triggered_by: Mapped[User | None] = relationship()
    rows: Mapped[list[ImportJobRow]] = relationship(
        back_populates="import_job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One live import per file. A FAILED or CANCELLED attempt may be retried;
        # a completed one can never be silently re-processed.
        Index(
            "uq_import_jobs_active_import_file",
            "organization_id",
            "import_file_id",
            unique=True,
            postgresql_where=text("status not in ('FAILED', 'CANCELLED')"),
        ),
        # Import-status search.
        Index("ix_import_jobs_organization_id_status", "organization_id", "status"),
        Index(
            "ix_import_jobs_vendor_id_created_at",
            "vendor_id",
            "created_at",
        ),
        Index("ix_import_jobs_import_file_id", "import_file_id"),
        CheckConstraint(
            "total_rows >= 0 and processed_rows >= 0 and matched_rows >= 0"
            " and exception_rows >= 0 and error_rows >= 0 and skipped_rows >= 0",
            name="counts_not_negative",
        ),
        CheckConstraint(
            "completed_at is null or started_at is not null",
            name="completed_requires_started",
        ),
        CheckConstraint(
            "completed_at is null or started_at is null or completed_at >= started_at",
            name="completed_after_started",
        ),
        CheckConstraint(
            "status not in ('COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED')"
            " or completed_at is not null",
            name="terminal_status_requires_completed_at",
        ),
    )


class ImportJobRow(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One staged source row, addressable by its line number in the original file.

    ``raw_data`` holds the original strings exactly as read. Nothing here is
    type-inferred on the way in: coercion happens after extraction, because
    inference silently destroys leading zeros in UPCs and vendor SKUs
    (ADR 0008, risk R2).
    """

    __tablename__ = "import_job_rows"

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    raw_data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    normalized_data: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    vendor_sku: Mapped[str | None] = mapped_column(nullable=True)
    normalized_vendor_sku: Mapped[str | None] = mapped_column(nullable=True)
    raw_upc: Mapped[str | None] = mapped_column(nullable=True)
    normalized_upc: Mapped[str | None] = mapped_column(nullable=True)
    catalog_item_number: Mapped[str | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)

    quantity: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    availability_status: Mapped[AvailabilityStatus | None] = mapped_column(
        pg_enum(AvailabilityStatus, "availability_status"), nullable=True
    )

    status: Mapped[ImportRowStatus] = mapped_column(
        pg_enum(ImportRowStatus, "import_row_status"),
        nullable=False,
        server_default=ImportRowStatus.PENDING.value,
    )
    match_result: Mapped[MatchResult | None] = mapped_column(
        pg_enum(MatchResult, "match_result"), nullable=True
    )
    matched_by: Mapped[MatchMethod | None] = mapped_column(
        pg_enum(MatchMethod, "match_method"), nullable=True
    )
    match_priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    vendor_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="SET NULL"), nullable=True
    )

    error_code: Mapped[str | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)

    import_job: Mapped[ImportJob] = relationship(back_populates="rows")
    product: Mapped[Product | None] = relationship()
    vendor_product: Mapped[VendorProduct | None] = relationship()

    __table_args__ = (
        Index(
            "uq_import_job_rows_import_job_id_row_number",
            "import_job_id",
            "row_number",
            unique=True,
        ),
        Index("ix_import_job_rows_import_job_id_status", "import_job_id", "status"),
        Index(
            "ix_import_job_rows_normalized_upc",
            "organization_id",
            "normalized_upc",
            postgresql_where=text("normalized_upc is not null"),
        ),
        Index(
            "ix_import_job_rows_normalized_vendor_sku",
            "organization_id",
            "normalized_vendor_sku",
            postgresql_where=text("normalized_vendor_sku is not null"),
        ),
        CheckConstraint("row_number >= 0", name="row_number_not_negative"),
        CheckConstraint(
            "match_priority is null or match_priority between 1 and 5",
            name="match_priority_within_chain",
        ),
        # A matched row must say which rule decided it. An unexplained match is
        # exactly what the deterministic chain exists to prevent (CLAUDE.md §5.1).
        CheckConstraint(
            "match_result <> 'MATCHED'"
            " or (matched_by is not null and match_priority is not null"
            " and product_id is not null)",
            name="matched_row_records_deciding_rule",
        ),
        CheckConstraint("quantity is null or quantity >= 0", name="quantity_not_negative"),
        CheckConstraint("unit_cost is null or unit_cost >= 0", name="unit_cost_not_negative"),
    )
