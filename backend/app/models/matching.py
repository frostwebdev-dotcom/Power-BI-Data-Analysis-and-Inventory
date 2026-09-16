"""The product-mapping exception queue.

Every uncertain match lands here: no rule fired, a rule matched more than one
product, or only a priority-5 suggestion exists. Nothing is guessed and no
candidate is silently chosen (CLAUDE.md §5.2).

Approving an exception is what creates a permanent mapping on the vendor product
or marketplace listing. The check constraints below make an approval without a
resolved product, an approver, and a timestamp impossible at the database level.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ExceptionReason, ExceptionStatus
from app.models.mixins import (
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.catalog import MarketplaceListing, Product
    from app.models.identity import User
    from app.models.ingestion import ImportJob, ImportJobRow
    from app.models.vendor import Vendor, VendorProduct


class ProductMappingException(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One item awaiting human judgement about product identity."""

    __tablename__ = "product_mapping_exceptions"

    # RESTRICT: an import job that produced unresolved decisions cannot be
    # deleted out from under them.
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    # SET NULL: the staged row may be cleaned up; the decision outlives it.
    import_job_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_job_rows.id", ondelete="SET NULL"), nullable=True
    )
    # An exception is about one of two things: a vendor line (vendor import)
    # or a marketplace listing (Amazon). At least one must be named; the
    # check constraint below enforces it.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True
    )
    vendor_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="CASCADE"), nullable=True
    )
    marketplace_listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=True
    )

    reason: Mapped[ExceptionReason] = mapped_column(
        pg_enum(ExceptionReason, "exception_reason"), nullable=False
    )
    status: Mapped[ExceptionStatus] = mapped_column(
        pg_enum(ExceptionStatus, "exception_status"),
        nullable=False,
        server_default=ExceptionStatus.PENDING.value,
    )

    # The candidates offered to the reviewer, each with the reason it was
    # suggested. A description similarity may appear here and nowhere else — it
    # can never drive an automatic match (CLAUDE.md §5.2).
    suggested_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    suggestion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    candidates: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # The full ordered rule evaluation that led here, so any queue item can be
    # explained after the fact.
    match_evaluations: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    resolved_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(nullable=True)
    #: A PENDING item deferred by a reviewer drops out of the default queue
    #: view until this moment (phase 8). Null means "not deferred".
    deferred_until: Mapped[datetime | None] = mapped_column(nullable=True)

    import_job: Mapped[ImportJob | None] = relationship()
    import_job_row: Mapped[ImportJobRow | None] = relationship()
    vendor: Mapped[Vendor | None] = relationship()
    vendor_product: Mapped[VendorProduct | None] = relationship()
    marketplace_listing: Mapped[MarketplaceListing | None] = relationship()
    suggested_product: Mapped[Product | None] = relationship(foreign_keys=[suggested_product_id])
    resolved_product: Mapped[Product | None] = relationship(foreign_keys=[resolved_product_id])
    assigned_to: Mapped[User | None] = relationship(foreign_keys=[assigned_to_user_id])
    resolved_by: Mapped[User | None] = relationship(foreign_keys=[resolved_by_user_id])

    __table_args__ = (
        # One open item per vendor line. Re-importing the same unresolvable SKU
        # must not pile up duplicate queue entries for a reviewer.
        Index(
            "uq_product_mapping_exceptions_pending_vendor_product",
            "organization_id",
            "vendor_product_id",
            unique=True,
            postgresql_where=text("status = 'PENDING' and vendor_product_id is not null"),
        ),
        # Likewise one open item per marketplace listing.
        Index(
            "uq_product_mapping_exceptions_pending_listing",
            "organization_id",
            "marketplace_listing_id",
            unique=True,
            postgresql_where=text("status = 'PENDING' and marketplace_listing_id is not null"),
        ),
        Index(
            "ix_product_mapping_exceptions_marketplace_listing_id",
            "marketplace_listing_id",
            postgresql_where=text("marketplace_listing_id is not null"),
        ),
        # Queue view: oldest pending first.
        Index(
            "ix_product_mapping_exceptions_status_created_at",
            "organization_id",
            "status",
            "created_at",
        ),
        # Deferred items re-enter the queue when their time comes.
        Index(
            "ix_product_mapping_exceptions_deferred_until",
            "organization_id",
            "deferred_until",
            postgresql_where=text("deferred_until is not null"),
        ),
        Index(
            "ix_product_mapping_exceptions_vendor_id_status",
            "vendor_id",
            "status",
        ),
        Index("ix_product_mapping_exceptions_import_job_id", "import_job_id"),
        Index(
            "ix_product_mapping_exceptions_assigned_to_user_id",
            "assigned_to_user_id",
            postgresql_where=text("assigned_to_user_id is not null"),
        ),
        # An approval is a permanent, accountable decision: it must name the
        # product, the approver, and the moment.
        CheckConstraint(
            "status <> 'APPROVED'"
            " or (resolved_product_id is not null and resolved_by_user_id is not null"
            " and resolved_at is not null)",
            name="approved_requires_product_and_approver",
        ),
        CheckConstraint(
            "status <> 'REJECTED' or (resolved_by_user_id is not null and resolved_at is not null)",
            name="rejected_requires_approver",
        ),
        # A pending item has not been resolved by anyone.
        CheckConstraint(
            "status <> 'PENDING'"
            " or (resolved_at is null and resolved_by_user_id is null"
            " and resolved_product_id is null)",
            name="pending_is_unresolved",
        ),
        CheckConstraint(
            "suggestion_score is null or (suggestion_score >= 0 and suggestion_score <= 1)",
            name="suggestion_score_is_a_ratio",
        ),
        # Every exception is about something: a vendor (line) or a listing.
        CheckConstraint(
            "vendor_id is not null or marketplace_listing_id is not null",
            name="has_a_subject",
        ),
        # A vendor line implies its vendor.
        CheckConstraint(
            "vendor_product_id is null or vendor_id is not null",
            name="vendor_product_requires_vendor",
        ),
    )
