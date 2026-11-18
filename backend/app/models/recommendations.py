"""Immutable historical input/result snapshots from reviewed purchasing policy."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ReplenishmentRecommendation(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    __tablename__ = "replenishment_recommendations"

    request_id: Mapped[uuid.UUID]
    request_sha256: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    approved_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    vendor_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="RESTRICT")
    )
    marketplace_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict[str, Any]]
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(String(1000))
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        Index("uq_replenishment_request", "organization_id", "request_id", unique=True),
        Index("ix_replenishment_policy_recorded", "organization_id", "policy_id", "recorded_at"),
        CheckConstraint(
            "status in ('BLOCKED', 'REVIEW_REQUIRED', 'NO_REORDER')", name="valid_status"
        ),
        CheckConstraint("marketplace_id = 'ATVPDKIKX0DER'", name="us_marketplace"),
        CheckConstraint("length(btrim(reason)) between 10 and 1000", name="reason_required"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="object_snapshot"),
        CheckConstraint("not purchasing_eligible", name="not_purchase_authorization"),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' and evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "and snapshot_sha256 ~ '^[0-9a-f]{64}$'",
            name="valid_digests",
        ),
    )
