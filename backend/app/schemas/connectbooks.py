"""Signed ConnectBooks XLSX preview; never an approved purchasing input."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.schemas.profitability import FinancialPreviewModel, ProfitabilityIssue

ConnectBooksField = Literal[
    "sales_qty",
    "refund_qty",
    "total_units",
    "sales",
    "refunds",
    "adjustments",
    "net_sales",
    "fees",
    "ad_spend",
    "cost",
    "profit",
]


class ConnectBooksXlsxProfile(FinancialPreviewModel):
    layout_version: Literal["connectbooks-signed-pnl-xlsx-v1"] = "connectbooks-signed-pnl-xlsx-v1"
    sheet_name: str = Field(min_length=1, max_length=100)
    store_name: str = Field(min_length=1, max_length=200)
    source_store_name: str = Field(min_length=1, max_length=200)
    marketplace_id: str = Field(min_length=1, max_length=64)
    currency: Literal["USD"]
    reporting_month: Annotated[int, Field(strict=True, ge=1, le=12)]
    reporting_year: Annotated[int, Field(strict=True, ge=1900, le=9998)] | None = None
    reporting_timezone: str | None = None
    advertising_coverage_confirmed: Annotated[bool, Field(strict=True)] = False

    @field_validator("sheet_name", "store_name", "source_store_name", "marketplace_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("context names must be nonblank without surrounding whitespace")
        return value

    @field_validator("reporting_timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("reporting_timezone must be an IANA timezone") from None
        return value


class ConnectBooksPreviewRow(FinancialPreviewModel):
    row_number: int
    source_item: str | None
    seller_sku: str | None
    asin: str | None
    source_store: str | None
    row_kind: Literal["SKU", "UNALLOCATED"]
    identity_status: Literal["UNRESOLVED", "UNALLOCATED"]
    purchasing_eligible: Literal[False] = False
    status: Literal["RECONCILED", "REVIEW_REQUIRED", "INVALID"]
    amounts: dict[ConnectBooksField, Decimal | None]
    computed_profit: Decimal | None
    computed_margin_percent: Decimal | None
    computed_roi_percent: Decimal | None
    reported_margin_percent: Decimal | None
    reported_roi_percent: Decimal | None
    issues: tuple[ProfitabilityIssue, ...]


class ConnectBooksControl(FinancialPreviewModel):
    field: ConnectBooksField
    detail_total: Decimal | None
    reported_total: Decimal | None
    difference: Decimal | None
    status: Literal["MATCH", "MISMATCH", "INCOMPLETE"]


class ConnectBooksXlsxPreview(FinancialPreviewModel):
    preview_only: Literal[True] = True
    persistence_performed: Literal[False] = False
    identity_resolution_performed: Literal[False] = False
    purchasing_eligible: Literal[False] = False
    status: Literal["RECONCILED_PREVIEW", "REVIEW_REQUIRED"]
    profile: ConnectBooksXlsxProfile
    raw_sha256: str
    source_columns: dict[str, str]
    summary_row_number: int
    blank_rows: int
    sku_rows: int
    unallocated_rows: int
    rows: tuple[ConnectBooksPreviewRow, ...]
    controls: tuple[ConnectBooksControl, ...]
    issues: tuple[ProfitabilityIssue, ...]
