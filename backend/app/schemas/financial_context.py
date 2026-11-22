"""Explicit historical report evidence, never a current cost or profit forecast."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.schemas.connectbooks import ConnectBooksControl
from app.schemas.replenishment import PreviewModel


class FinancialSelection(PreviewModel):
    import_id: uuid.UUID
    expected_binding_revision_id: uuid.UUID | None


class HistoricalFinancialRow(PreviewModel):
    listing_id: uuid.UUID
    seller_sku: str
    source_row_id: uuid.UUID | None
    source_row_number: int | None
    mapping_snapshot: dict[str, Any] | None
    current_mapping_snapshot: dict[str, Any] | None
    included_in_totals: bool
    amounts: dict[str, Decimal | None]
    computed_profit: Decimal | None
    computed_margin_percent: Decimal | None
    computed_roi_percent: Decimal | None
    review_reasons: tuple[str, ...]


class HistoricalFinancialContext(PreviewModel):
    basis: Literal["HISTORICAL_REPORT_ONLY"] = "HISTORICAL_REPORT_ONLY"
    import_id: uuid.UUID
    binding_revision_id: uuid.UUID | None
    original_filename: str
    raw_sha256: str
    profile_sha256: str
    profile_snapshot: dict[str, Any]
    marketplace_id: str
    store_name: str
    currency: Literal["USD"]
    period_start: datetime
    period_end: datetime
    received_at: datetime
    report_status: str
    status: Literal["AVAILABLE", "REVIEW_REQUIRED", "UNAVAILABLE"]
    controls: tuple[ConnectBooksControl, ...]
    unallocated_rows: int
    rows: tuple[HistoricalFinancialRow, ...]
    totals: dict[str, Decimal] | None
    review_reasons: tuple[str, ...]
    purchasing_eligible: Literal[False] = False
