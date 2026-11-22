"""Append-only draft and manager-decision ledger; no purchasing execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PurchasingPolicyRevision(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    __tablename__ = "purchasing_policy_revisions"

    policy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    vendor_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_products.id", ondelete="RESTRICT")
    )
    marketplace_id: Mapped[str] = mapped_column(String(32))
    revision_number: Mapped[int] = mapped_column(Integer)
    supersedes_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    reviewed_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchasing_policy_revisions.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(16))
    definition_snapshot: Mapped[dict[str, Any]]
    definition_sha256: Mapped[str] = mapped_column(String(64))
    review_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    context_sha256: Mapped[str | None] = mapped_column(String(64))
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(String(1000))
    purchasing_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        UniqueConstraint("policy_id", "revision_number"),
        UniqueConstraint("supersedes_revision_id"),
        Index(
            "uq_purchasing_policy_root",
            "organization_id",
            "product_id",
            "vendor_product_id",
            "marketplace_id",
            unique=True,
            postgresql_where=text("revision_number = 1"),
        ),
        CheckConstraint("revision_number > 0", name="positive_revision"),
        CheckConstraint("marketplace_id = 'ATVPDKIKX0DER'", name="us_marketplace"),
        CheckConstraint("kind in ('DRAFT', 'APPROVED', 'REVOKED')", name="valid_kind"),
        CheckConstraint("length(btrim(reason)) between 10 and 1000", name="reason_required"),
        CheckConstraint("definition_sha256 ~ '^[0-9a-f]{64}$'", name="valid_digest"),
        CheckConstraint("jsonb_typeof(definition_snapshot) = 'object'", name="object_definition"),
        CheckConstraint(
            "(revision_number = 1 and policy_id = id "
            "and supersedes_revision_id is null and kind = 'DRAFT') "
            "or (revision_number > 1 and policy_id <> id and supersedes_revision_id is not null)",
            name="root_or_successor",
        ),
        CheckConstraint(
            "(kind = 'DRAFT' and reviewed_revision_id is null "
            "and review_context is null and context_sha256 is null) "
            "or (kind <> 'DRAFT' and reviewed_revision_id is not null "
            "and review_context is not null "
            "and jsonb_typeof(review_context) = 'object' and context_sha256 is not null "
            "and context_sha256 ~ '^[0-9a-f]{64}$')",
            name="review_evidence",
        ),
        CheckConstraint("not purchasing_eligible", name="not_purchase_authorization"),
    )
