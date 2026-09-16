"""Vendor inventory history and detected availability transitions.

``vendor_inventory_snapshots`` is append-only: each import writes new rows and
never updates existing ones, so the history of what a vendor said, and when,
stays intact. Every foreign key out of a snapshot uses ``RESTRICT`` for the same
reason — you cannot delete an import job or vendor product that history depends
on.

``availability_events`` carries a unique constraint on
(``current_snapshot_id``, ``event_type``). One snapshot can raise a given
transition exactly once, so re-processing a batch cannot produce duplicate
alerts for the same observation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AvailabilityEventType, AvailabilityStatus
from app.models.mixins import (
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.identity import User
    from app.models.ingestion import ImportJob
    from app.models.vendor import Vendor, VendorProduct
    from app.models.watchlist import OosWatchlistEntry


class VendorInventorySnapshot(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """What one vendor stated about one product at one point in time.

    Rows are written once and never revised. Correcting a mistake means
    importing a corrected file, which produces a new snapshot — the wrong one
    stays visible, because that is what an audit trail is for.
    """

    __tablename__ = "vendor_inventory_snapshots"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
    )
    vendor_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="RESTRICT"), nullable=False
    )
    # NULL while the vendor line is unmapped. Inventory signal is recorded even
    # when identity is still pending in the exception queue.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )

    quantity_available: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        pg_enum(AvailabilityStatus, "availability_status"),
        nullable=False,
        server_default=AvailabilityStatus.UNKNOWN.value,
    )

    # When the vendor's data is effective, versus when we recorded it. They
    # differ whenever a file is uploaded later than it was produced.
    effective_at: Mapped[datetime] = mapped_column(nullable=False)
    captured_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    vendor: Mapped[Vendor] = relationship()
    vendor_product: Mapped[VendorProduct] = relationship()
    product: Mapped[Product | None] = relationship()
    import_job: Mapped[ImportJob] = relationship()

    __table_args__ = (
        # One snapshot per vendor line per import. Re-running the same job
        # cannot double-write history.
        Index(
            "uq_vendor_inventory_snapshots_job_vendor_product",
            "import_job_id",
            "vendor_product_id",
            unique=True,
        ),
        # "Latest state for this vendor line" — the hot path for diffing.
        Index(
            "ix_vendor_inventory_snapshots_vendor_product_effective_at",
            "organization_id",
            "vendor_product_id",
            "effective_at",
        ),
        Index(
            "ix_vendor_inventory_snapshots_product_effective_at",
            "organization_id",
            "product_id",
            "effective_at",
            postgresql_where=text("product_id is not null"),
        ),
        Index(
            "ix_vendor_inventory_snapshots_availability_status",
            "organization_id",
            "availability_status",
        ),
        CheckConstraint(
            "quantity_available is null or quantity_available >= 0",
            name="quantity_not_negative",
        ),
        CheckConstraint("unit_cost is null or unit_cost >= 0", name="unit_cost_not_negative"),
        CheckConstraint("unit_cost is null or currency is not null", name="cost_requires_currency"),
    )


class AvailabilityEvent(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A detected transition between two consecutive inventory snapshots.

    The unique constraint on (``current_snapshot_id``, ``event_type``) is what
    stops repeated alerts: the same observation can only ever raise one event of
    a given kind, however many times the diff runs.
    """

    __tablename__ = "availability_events"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
    )
    vendor_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="RESTRICT"), nullable=False
    )
    # NULL when the vendor line is not yet mapped — the transition is still
    # worth recording, so the signal is not lost while a mapping is pending.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    oos_watchlist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oos_watchlist.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[AvailabilityEventType] = mapped_column(
        pg_enum(AvailabilityEventType, "availability_event_type"), nullable=False
    )
    previous_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendor_inventory_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    current_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_inventory_snapshots.id", ondelete="RESTRICT"), nullable=False
    )

    previous_status: Mapped[AvailabilityStatus | None] = mapped_column(
        pg_enum(AvailabilityStatus, "availability_status"), nullable=True
    )
    new_status: Mapped[AvailabilityStatus] = mapped_column(
        pg_enum(AvailabilityStatus, "availability_status"), nullable=False
    )
    previous_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    new_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)

    detected_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Set when the event was linked to a watch entry with a ``max_unit_cost``:
    #: true means the vendor's cost is above the buyer's ceiling. The event is
    #: still raised and linked — flagged, never dropped (phase 9).
    over_max_unit_cost: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    vendor: Mapped[Vendor] = relationship()
    vendor_product: Mapped[VendorProduct] = relationship()
    product: Mapped[Product | None] = relationship()
    watchlist_entry: Mapped[OosWatchlistEntry | None] = relationship()
    previous_snapshot: Mapped[VendorInventorySnapshot | None] = relationship(
        foreign_keys=[previous_snapshot_id]
    )
    current_snapshot: Mapped[VendorInventorySnapshot] = relationship(
        foreign_keys=[current_snapshot_id]
    )
    acknowledged_by: Mapped[User | None] = relationship()

    __table_args__ = (
        # One event of each kind per observed snapshot: the de-duplication
        # guarantee that keeps alerting from repeating.
        Index(
            "uq_availability_events_snapshot_event_type",
            "current_snapshot_id",
            "event_type",
            unique=True,
        ),
        # Event-timestamp search.
        Index(
            "ix_availability_events_organization_id_detected_at",
            "organization_id",
            "detected_at",
        ),
        Index(
            "ix_availability_events_product_detected_at",
            "organization_id",
            "product_id",
            "detected_at",
            postgresql_where=text("product_id is not null"),
        ),
        Index(
            "ix_availability_events_unacknowledged",
            "organization_id",
            "detected_at",
            postgresql_where=text("acknowledged_at is null"),
        ),
        Index("ix_availability_events_vendor_product_id", "vendor_product_id"),
        CheckConstraint(
            "previous_snapshot_id is null or previous_snapshot_id <> current_snapshot_id",
            name="transition_between_distinct_snapshots",
        ),
        CheckConstraint(
            "previous_status is null or previous_status <> new_status",
            name="event_records_an_actual_change",
        ),
        CheckConstraint(
            "acknowledged_at is null or acknowledged_by_user_id is not null",
            name="acknowledgement_names_a_user",
        ),
    )
