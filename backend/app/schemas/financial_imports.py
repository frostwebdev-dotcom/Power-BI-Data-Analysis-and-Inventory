"""Staff-facing financial import records and their immutable review rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.connectbooks import ConnectBooksPreviewRow, ConnectBooksXlsxProfile

FinancialMappingStatus = Literal[
    "LINKED", "UNRESOLVED", "UNALLOCATED", "INVALID_SOURCE", "ASIN_CONFLICT"
]


class FinancialImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    original_filename: str
    raw_sha256: str
    profile_sha256: str
    marketplace_id: str
    store_name: str
    currency: str
    period_start: datetime
    period_end: datetime
    received_at: datetime
    uploaded_by_user_id: uuid.UUID
    status: Literal["RECORDED", "REVIEW_REQUIRED"]
    row_count: int
    linked_rows: int
    review_rows: int
    purchasing_eligible: Literal[False]
    profile_snapshot: ConnectBooksXlsxProfile
    report_snapshot: dict[str, Any] = Field(
        description="Original pre-binding preview metadata and financial controls."
    )


class FinancialUploadResponse(BaseModel):
    batch: FinancialImportResponse
    duplicate: bool


class FinancialImportPage(BaseModel):
    items: list[FinancialImportResponse]
    page: int
    page_size: int
    total: int


class FinancialRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    import_id: uuid.UUID
    source_row_number: int
    seller_sku: str | None
    row_kind: Literal["SKU", "UNALLOCATED"]
    mapping_status: FinancialMappingStatus
    listing_id: uuid.UUID | None
    product_id: uuid.UUID | None
    match_priority: Literal[4] | None
    mapping_snapshot: dict[str, Any]
    source_snapshot: ConnectBooksPreviewRow
    review_reasons: list[str]
    review_required: bool
    purchasing_eligible: Literal[False]
    net_sales: Decimal | None
    fees: Decimal | None
    cost: Decimal | None
    ad_spend: Decimal | None
    reported_profit: Decimal | None
    computed_profit: Decimal | None
    binding_revision_id: uuid.UUID | None = None


class FinancialRowPage(BaseModel):
    items: list[FinancialRowResponse]
    page: int
    page_size: int
    total: int


class FinancialBindingEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    mapping_status: FinancialMappingStatus
    listing_id: uuid.UUID | None
    product_id: uuid.UUID | None
    match_priority: Literal[4] | None
    mapping_snapshot: dict[str, Any]
    review_reasons: list[str]
    review_required: bool


class FinancialBindingChange(BaseModel):
    source_row_number: int
    seller_sku: str | None
    before: FinancialBindingEvidence
    after: FinancialBindingEvidence


class FinancialBindingPreview(BaseModel):
    import_id: uuid.UUID
    expected_revision_id: uuid.UUID | None
    bindings_sha256: str
    status: Literal["RECORDED", "REVIEW_REQUIRED"]
    row_count: int
    linked_rows: int
    review_rows: int
    changed_rows: int
    changes: list[FinancialBindingChange]
    page: int
    page_size: int
    purchasing_eligible: Literal[False] = False


class FinancialBindingRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: uuid.UUID | None
    bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Give a reason of 10-1000 characters without control characters.")
        return value


class FinancialBindingRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    import_id: uuid.UUID
    revision_number: int
    supersedes_revision_id: uuid.UUID | None
    recorded_by_user_id: uuid.UUID
    created_at: datetime
    reason: str
    bindings_sha256: str
    status: Literal["RECORDED", "REVIEW_REQUIRED"]
    row_count: int
    linked_rows: int
    review_rows: int
    changed_rows: int
    purchasing_eligible: Literal[False]


class FinancialBindingRevisionPage(BaseModel):
    items: list[FinancialBindingRevisionResponse]
    page: int
    page_size: int
    total: int
