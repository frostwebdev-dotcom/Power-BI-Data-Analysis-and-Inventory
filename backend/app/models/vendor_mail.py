"""Read-only vendor mailbox routing and durable delivery history (ADR 0019)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.vendor import Vendor, VendorImportProfile


class VendorMailSource(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "vendor_mail_sources"

    name: Mapped[str] = mapped_column(String(100))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"))
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_import_profiles.id", ondelete="RESTRICT")
    )
    sender_addresses: Mapped[list[str]] = mapped_column(default=list)
    subject_contains: Mapped[str | None] = mapped_column(String(200))
    filename_pattern: Mapped[str] = mapped_column(String(200), default="*.xlsx")
    mode: Mapped[str] = mapped_column(String(20), default="ATTACHMENT")
    download_hosts: Mapped[list[str]] = mapped_column(default=list)
    download_path_prefix: Mapped[str | None] = mapped_column(String(500))
    download_filename: Mapped[str | None] = mapped_column(String(200))
    stale_after_hours: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    starts_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_received_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    vendor: Mapped[Vendor] = relationship()
    profile: Mapped[VendorImportProfile] = relationship()

    __table_args__ = (
        Index("uq_vendor_mail_sources_org_name", "organization_id", "name", unique=True),
        CheckConstraint("mode in ('ATTACHMENT', 'LINK')", name="valid_mode"),
        CheckConstraint(
            "stale_after_hours is null or stale_after_hours > 0", name="positive_cadence"
        ),
    )


class VendorMailState(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "vendor_mail_states"

    account_key: Mapped[str] = mapped_column(String(64))
    # Private cursor; never returned by the API or included in audit snapshots.
    checkpoint_url: Mapped[str | None]
    initial_since: Mapped[datetime]
    last_poll_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("uq_vendor_mail_states_org_account", "organization_id", "account_key", unique=True),
    )


class VendorMailDelivery(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "vendor_mail_deliveries"

    state_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_mail_states.id", ondelete="RESTRICT")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_mail_sources.id", ondelete="RESTRICT")
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_import_profiles.id", ondelete="RESTRICT")
    )
    message_id: Mapped[str]  # Opaque provider message ID; no body/subject retained.
    item_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    attachment_id: Mapped[str | None]
    filename: Mapped[str] = mapped_column(String(255))
    received_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        Index(
            "uq_vendor_mail_deliveries_identity",
            "state_id",
            "source_id",
            "message_id",
            "item_key",
            unique=True,
        ),
        Index("ix_vendor_mail_deliveries_queue", "organization_id", "status", "received_at"),
        CheckConstraint(
            "status in ('PENDING', 'RUNNING', 'COMPLETED', 'DUPLICATE', 'FAILED', "
            "'NEEDS_REVIEW', 'SUPERSEDED')",
            name="valid_status",
        ),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )
