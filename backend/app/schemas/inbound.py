"""Explicit reviewed inbound positions; no receiving or purchase authorization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.schemas.amazon_readiness import (
    AmazonReadinessRequest,
    ReadinessIssue,
    ReadinessListing,
    ReadinessPool,
    ReadinessRun,
)
from app.schemas.purchasing_policies import Digest, ReasonedRequest
from app.schemas.replenishment import PreviewModel

MAX_UNITS = 9223372036854775807
Units = Annotated[int, Field(strict=True, ge=0, le=MAX_UNITS)]


class Arrival(PreviewModel):
    reference: Annotated[str, Field(min_length=1, max_length=100)]
    base_units: Annotated[int, Field(strict=True, ge=1, le=MAX_UNITS)]
    expected_at: datetime

    @field_validator("reference")
    @classmethod
    def clean_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Shipment reference is required.")
        return value.strip()

    @field_validator("expected_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("An explicit timezone is required.")
        return value.astimezone(UTC)


class InboundPosition(PreviewModel):
    declared_total_base_units: Units
    arrivals: Annotated[tuple[Arrival, ...], Field(max_length=100)]
    observed_at: datetime
    expires_at: datetime
    evidence_reference: Annotated[str, Field(min_length=10, max_length=500)]
    complete_position_confirmed: StrictBool
    no_on_hand_overlap_confirmed: StrictBool

    @field_validator("observed_at", "expires_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return Arrival.aware(value)

    @field_validator("evidence_reference")
    @classmethod
    def reference(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("Provide the source evidence reference.")
        return value.strip()

    @model_validator(mode="after")
    def complete(self) -> InboundPosition:
        if sum(a.base_units for a in self.arrivals) != self.declared_total_base_units:
            raise ValueError("Arrivals must reconcile exactly to the declared position.")
        if len({a.reference.casefold() for a in self.arrivals}) != len(self.arrivals):
            raise ValueError("Shipment references must be unique within a position.")
        if not self.observed_at < self.expires_at <= self.observed_at + timedelta(days=31):
            raise ValueError("Expiry must be after observation and within 31 days.")
        return self


class InboundSelection(PreviewModel):
    revision_ids: Annotated[tuple[uuid.UUID, ...], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def distinct(self) -> InboundSelection:
        if len(set(self.revision_ids)) != len(self.revision_ids):
            raise ValueError("Select each revision once.")
        return self


class InboundContextRequest(PreviewModel):
    expected_approved_revision_id: uuid.UUID
    pool_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")]


class InboundProposal(InboundContextRequest):
    expected_head_id: uuid.UUID | None
    expected_source_sha256: Digest
    position: InboundPosition


class InboundRecordRequest(InboundProposal):
    request_id: uuid.UUID
    expected_preview_sha256: Digest
    reason: Annotated[str, Field(min_length=10, max_length=1000)]
    acknowledge_review: StrictBool

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("A review reason is required.")
        return value.strip()


class InboundRevokeRequest(ReasonedRequest):
    request_id: uuid.UUID
    expected_head_id: uuid.UUID
    acknowledge_review: StrictBool


class InboundRevision(PreviewModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    approved_revision_id: uuid.UUID
    pool_id: str
    channel: Literal["FBA", "FBM"]
    revision_number: int
    previous_revision_id: uuid.UUID | None
    kind: Literal["CONFIRMED", "REVOKED"]
    inventory_run_id: uuid.UUID
    stock_snapshot_id: uuid.UUID
    source_context: dict[str, Any]
    source_sha256: str
    position: InboundPosition
    recorded_by_user_id: uuid.UUID
    recorded_at: datetime
    reason: str
    purchasing_eligible: Literal[False] = False


class InboundPage(PreviewModel):
    items: tuple[InboundRevision, ...]
    page: int
    page_size: int
    total: int


class InboundContext(PreviewModel):
    policy_id: uuid.UUID
    approved_revision_id: uuid.UUID
    pool_id: str
    channel: Literal["FBA", "FBM"]
    evaluated_at: datetime
    head_id: uuid.UUID | None
    source_context: dict[str, Any]
    source_sha256: str
    blockers: tuple[str, ...]
    purchasing_eligible: Literal[False] = False


class InboundPreview(PreviewModel):
    context: InboundContext
    proposal: InboundProposal
    can_confirm: bool
    blockers: tuple[str, ...]
    preview_sha256: str
    purchasing_eligible: Literal[False] = False


class QualifiedArrival(PreviewModel):
    reference: str
    base_units: int
    expected_at: datetime
    disposition: Literal["BEFORE_STOCKOUT", "AFTER_STOCKOUT", "AFTER_COVERAGE", "UNCONFIRMED"]
    credited_units: int | None


class InboundAssessment(PreviewModel):
    revision: InboundRevision
    basis: Literal["MANAGER_REVIEWED_COMPLETE_POSITION"] = "MANAGER_REVIEWED_COMPLETE_POSITION"
    status: Literal["APPLIED", "BLOCKED"]
    source_capture_at: datetime | None
    usable_base_units: int | None
    arrivals: tuple[QualifiedArrival, ...]
    reasons: tuple[str, ...]
    purchasing_eligible: Literal[False] = False


class ReviewedReadinessResponse(PreviewModel):
    rule_version: Literal["amazon-readiness-v2"] = "amazon-readiness-v2"
    data_source: Literal["ingested_postgresql"] = "ingested_postgresql"
    scope: Literal["known_product_listings"] = "known_product_listings"
    readiness_only: Literal[True] = True
    purchasing_eligible: Literal[False] = False
    confirmations_persisted: Literal[True] = True
    request: AmazonReadinessRequest
    evaluated_at: datetime
    demand_window_start: datetime
    demand_window_end: datetime
    status: Literal["DATA_AVAILABLE", "BLOCKED"]
    issues: tuple[ReadinessIssue, ...]
    original_inbound_issues: tuple[ReadinessIssue, ...]
    inbound_revision_ids: tuple[uuid.UUID, ...]
    latest_orders_run: ReadinessRun | None
    order_coverage_run_ids: tuple[uuid.UUID, ...]
    inventory_run: ReadinessRun | None
    listings: tuple[ReadinessListing, ...]
    pools: tuple[ReadinessPool, ...]
