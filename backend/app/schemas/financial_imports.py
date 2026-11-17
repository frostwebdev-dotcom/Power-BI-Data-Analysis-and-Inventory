"""Staff-facing financial import records and their immutable review rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class FinancialRowPage(BaseModel):
    items: list[FinancialRowResponse]
    page: int
    page_size: int
    total: int
