"""Financial CSV preview contract. Values and source semantics are explicit."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.imports.profile_rules import normalize_header

ExpenseField = Literal["cost_of_goods", "amazon_fees", "shipping", "advertising", "other_costs"]
MoneyField = Literal[
    "net_revenue", "cost_of_goods", "amazon_fees", "shipping", "advertising", "other_costs"
]
CsvField = Literal[
    "seller_sku",
    "units_sold",
    "net_revenue",
    "cost_of_goods",
    "amazon_fees",
    "shipping",
    "advertising",
    "other_costs",
    "currency",
]


class FinancialPreviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ProfitabilityCsvProfile(FinancialPreviewModel):
    name: str = Field(min_length=1, max_length=200)
    version: Annotated[int, Field(strict=True, ge=1)]
    source_name: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: datetime
    period_end: datetime
    values_basis: Literal["period_totals"]
    revenue_basis: Literal["net_after_refunds_excluding_tax"]
    expense_basis: Literal["positive_expense_totals"]
    quantity_basis: Literal["seller_sku_units"]
    columns: dict[CsvField, str]
    confirmed_zero_costs: tuple[ExpenseField, ...] = ()
    encoding: str = Field(min_length=1, max_length=64)
    delimiter: Literal[",", ";", "\t", "|"]
    decimal_separator: Literal[".", ","]
    thousands_separator: Literal["", ",", "."]
    header_row_index: Annotated[int, Field(strict=True, ge=0, le=1000)] = 0

    @field_validator("period_start", "period_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reporting bounds must include their UTC offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _explicit_profile(self) -> Self:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start (exclusive end)")
        if not self.name.strip() or not self.source_name.strip() or not self.marketplace_id.strip():
            raise ValueError("profile, source and marketplace must not be blank")
        if "seller_sku" not in self.columns:
            raise ValueError("an explicit seller_sku column is required")
        normalized = [normalize_header(value) for value in self.columns.values()]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("mapped columns must be nonblank and distinct")
        if len(set(self.confirmed_zero_costs)) != len(self.confirmed_zero_costs):
            raise ValueError("confirmed_zero_costs must not contain duplicates")
        if any(field in self.columns for field in self.confirmed_zero_costs):
            raise ValueError("an expense cannot be both mapped and confirmed zero")
        if self.thousands_separator and self.thousands_separator == self.decimal_separator:
            raise ValueError("thousands and decimal separators must differ")
        return self


class ProfitabilityIssue(FinancialPreviewModel):
    code: str
    field: str
    severity: Literal["ERROR", "REVIEW", "NOTICE"]


class ProfitabilityMetrics(FinancialPreviewModel):
    rule_version: Literal["period-net-revenue-cogs-roi-v1"] = "period-net-revenue-cogs-roi-v1"
    net_profit: Decimal | None = None
    margin_percent: Decimal | None = None
    roi_percent: Decimal | None = None
    net_revenue_per_sku_unit: Decimal | None = None
    cost_per_sku_unit: Decimal | None = None
    profit_per_sku_unit: Decimal | None = None


class ProfitabilityPreviewRow(FinancialPreviewModel):
    row_number: int
    seller_sku: str | None
    identity_status: Literal["UNRESOLVED"] = "UNRESOLVED"
    units_sold: int | None
    amounts: dict[MoneyField, Decimal | None]
    status: Literal["CALCULATED", "INCOMPLETE", "INVALID"]
    metrics: ProfitabilityMetrics
    issues: tuple[ProfitabilityIssue, ...]


class ProfitabilityCsvPreview(FinancialPreviewModel):
    preview_only: Literal[True] = True
    persistence_performed: Literal[False] = False
    identity_resolution_performed: Literal[False] = False
    status: Literal["VALIDATED_PREVIEW", "REVIEW_REQUIRED"]
    profile: ProfitabilityCsvProfile
    raw_sha256: str
    headers: tuple[str, ...]
    blank_rows: int
    rows: tuple[ProfitabilityPreviewRow, ...]
