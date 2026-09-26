"""Source-system synchronisation bookkeeping and raw payload retention.

Tables only — no Nineyard client, HTTP or parsing. The integration and service
depend on these source-neutral records, so upstream field names never leak into
the persistence model.

``source_records`` is the general retention mechanism for source-system
references: it keeps the raw payload, its hash, and the link between an external
record and the local row it produced. Retaining payloads is what makes a sync
replayable and diffable without calling the API again.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import SourceSystem, SyncStatus, TriggerType
from app.models.mixins import (
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.identity import User
    from app.models.ingestion import ImportJob


class NineyardSyncRun(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """One catalog synchronisation execution, with its outcome counts."""

    __tablename__ = "nineyard_sync_runs"

    status: Mapped[SyncStatus] = mapped_column(
        pg_enum(SyncStatus, "sync_status"),
        nullable=False,
        server_default=SyncStatus.PENDING.value,
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        pg_enum(TriggerType, "trigger_type"),
        nullable=False,
        server_default=TriggerType.MANUAL.value,
    )

    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    items_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    items_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    items_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    items_removed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # Opaque high-water mark for incremental sync, if the API turns out to
    # support one (B1).
    cursor: Mapped[str | None] = mapped_column(nullable=True)

    error_message: Mapped[str | None] = mapped_column(nullable=True)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    triggered_by: Mapped[User | None] = relationship()
    source_records: Mapped[list[SourceRecord]] = relationship(back_populates="sync_run")

    __table_args__ = (
        Index(
            "ix_nineyard_sync_runs_organization_id_status",
            "organization_id",
            "status",
            "started_at",
        ),
        CheckConstraint(
            "items_seen >= 0 and items_created >= 0 and items_updated >= 0"
            " and items_unchanged >= 0 and items_removed >= 0 and items_failed >= 0",
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
    )


class SourceRecord(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A retained reference to, and payload from, an external system.

    This is how an internal UUID stays connected to the external identifier it
    came from without letting that external identifier become a key. The payload
    is kept verbatim so a sync can be replayed and diffed offline.
    """

    __tablename__ = "source_records"

    source_system: Mapped[SourceSystem] = mapped_column(
        pg_enum(SourceSystem, "source_system"), nullable=False
    )
    # What the record is called in the source system, e.g. "catalog_item".
    source_entity_type: Mapped[str] = mapped_column(nullable=False)
    # Its identifier there. A string, because we do not control its format.
    source_record_id: Mapped[str] = mapped_column(nullable=False)

    # The local row it produced, if any. Loosely coupled on purpose: a payload
    # may be retained before, or without, a local row existing.
    local_entity_type: Mapped[str | None] = mapped_column(nullable=True)
    local_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    nineyard_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nineyard_sync_runs.id", ondelete="SET NULL"), nullable=True
    )
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True
    )

    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    sync_run: Mapped[NineyardSyncRun | None] = relationship(back_populates="source_records")
    import_job: Mapped[ImportJob | None] = relationship()

    __table_args__ = (
        # An unchanged payload seen again on a later run is stored once. Content
        # addressing keeps retention proportional to actual change.
        Index(
            "uq_source_records_identity_payload",
            "organization_id",
            "source_system",
            "source_entity_type",
            "source_record_id",
            "payload_sha256",
            unique=True,
        ),
        # "What did this external id become locally?" and its inverse.
        Index(
            "ix_source_records_source_lookup",
            "organization_id",
            "source_system",
            "source_record_id",
        ),
        Index(
            "ix_source_records_local_entity",
            "organization_id",
            "local_entity_type",
            "local_entity_id",
            postgresql_where=text("local_entity_id is not null"),
        ),
        Index("ix_source_records_nineyard_sync_run_id", "nineyard_sync_run_id"),
        CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_sha256_is_lowercase_hex"
        ),
        CheckConstraint("length(trim(source_record_id)) > 0", name="source_record_id_not_blank"),
        # A local reference is either fully specified or absent.
        CheckConstraint(
            "(local_entity_type is null) = (local_entity_id is null)",
            name="local_reference_is_complete",
        ),
    )
