"""Exception-queue request and response schemas (AC-8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ExceptionReason, ExceptionStatus, MappingStatus


class ProductSummary(BaseModel):
    """Enough of a product for a reviewer to recognise it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catalog_item_number: str
    name: str
    brand: str | None
    is_active: bool


class VendorLineSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_sku: str
    normalized_vendor_sku: str
    vendor_description: str | None
    normalized_upc: str | None
    product_id: uuid.UUID | None
    mapping_status: MappingStatus


class ListingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seller_sku: str
    asin: str | None
    product_id: uuid.UUID | None
    mapping_status: MappingStatus


class SourceRow(BaseModel):
    """The row as imported (AC-8.2): raw cells and the extractor's values."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    import_job_id: uuid.UUID
    row_number: int
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any]
    vendor_sku: str | None
    raw_upc: str | None
    normalized_upc: str | None
    description: str | None
    quantity: Decimal | None
    unit_cost: Decimal | None


class Candidate(BaseModel):
    """One product the chain found or suggested, with the rule that found it."""

    product_id: uuid.UUID
    rule: str
    priority: int
    identifier: str | None = None
    score: float | None = None
    reason: str | None = None
    product: ProductSummary | None = None


class ExceptionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: ExceptionReason
    status: ExceptionStatus
    vendor_id: uuid.UUID | None
    vendor_product_id: uuid.UUID | None
    marketplace_listing_id: uuid.UUID | None
    import_job_id: uuid.UUID | None
    import_job_row_id: uuid.UUID | None
    suggested_product_id: uuid.UUID | None
    suggestion_score: Decimal | None
    assigned_to_user_id: uuid.UUID | None
    deferred_until: datetime | None
    resolved_product_id: uuid.UUID | None
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime
    #: Denormalised for the queue list: what the reviewer scans by.
    vendor_sku: str | None = None
    description: str | None = None
    row_number: int | None = None
    age_hours: float | None = None


class ExceptionDetailResponse(ExceptionSummary):
    candidates: list[Candidate]
    match_evaluations: dict[str, Any]
    source_row: SourceRow | None
    vendor_line: VendorLineSummary | None
    listing: ListingSummary | None
    suggested_product: ProductSummary | None
    resolved_product: ProductSummary | None


class ExceptionListResponse(BaseModel):
    items: list[ExceptionSummary]
    page: int
    page_size: int
    total: int


class ApproveRequest(BaseModel):
    product_id: uuid.UUID
    #: Required when the vendor line (or listing) already has an APPROVED
    #: mapping to a *different* product (AC-8.6).
    supersede: bool = False
    note: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a rejection needs a reason")
        return stripped


class DeferRequest(BaseModel):
    until: datetime
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("until")
    @classmethod
    def _future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("until must carry a timezone")
        if value <= datetime.now(UTC):
            raise ValueError("until must be in the future")
        return value.astimezone(UTC)


class ResolutionResponse(BaseModel):
    """What an approve / reject / defer did."""

    exception: ExceptionDetailResponse
    #: The product the superseded mapping pointed at, when one was superseded.
    superseded_product_id: uuid.UUID | None = None
