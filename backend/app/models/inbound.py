"""Append-only manager-reviewed evidence of complete outstanding inbound positions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class InboundEvidenceRevision(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "inbound_evidence_revisions"
    policy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    approved_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    marketplace_id: Mapped[str] = mapped_column(String(32))
    pool_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(3))
    revision_number: Mapped[int]
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_evidence_revisions.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(16))
    inventory_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("amazon_sync_runs.id", ondelete="RESTRICT")
    )
    stock_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("amazon_inventory_snapshots.id", ondelete="RESTRICT")
    )
    stock_source_listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="RESTRICT")
    )
    source_context: Mapped[dict[str, Any]]
    source_sha256: Mapped[str] = mapped_column(String(64))
    position: Mapped[dict[str, Any]]
    declared_total_base_units: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    request_id: Mapped[uuid.UUID]
    request_sha256: Mapped[str] = mapped_column(String(64))
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(String(1000))
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (
        Index("uq_inbound_request", "organization_id", "request_id", unique=True),
        Index(
            "uq_inbound_stream_revision",
            "organization_id",
            "policy_id",
            "pool_id",
            "revision_number",
            unique=True,
        ),
        Index(
            "uq_inbound_previous",
            "previous_revision_id",
            unique=True,
            postgresql_where=text("previous_revision_id IS NOT NULL"),
        ),
        CheckConstraint(
            "kind in ('CONFIRMED','REVOKED') and channel in ('FBA','FBM')",
            name="valid_kind_channel",
        ),
        CheckConstraint(
            "revision_number > 0 and declared_total_base_units >= 0", name="valid_counts"
        ),
        CheckConstraint("marketplace_id = 'ATVPDKIKX0DER'", name="us_marketplace"),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' and request_sha256 ~ '^[0-9a-f]{64}$'",
            name="valid_digests",
        ),
        CheckConstraint(
            "jsonb_typeof(position) = 'object' and jsonb_typeof(source_context) = 'object'",
            name="object_evidence",
        ),
        CheckConstraint("length(btrim(reason)) between 10 and 1000", name="reason_required"),
        CheckConstraint("not purchasing_eligible", name="not_purchase_authorization"),
    )
