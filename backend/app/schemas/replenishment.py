"""Explicit-input Milestone 2 quantity previews; no customer defaults or persistence."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

WholeUnits = Annotated[int, Field(strict=True, ge=0)]
PolicyDays = Annotated[int, Field(strict=True, ge=0, le=3650)]
Decision = Literal["READY", "REVIEW_REQUIRED", "BLOCKED", "NO_REORDER"]
Reason = Literal[
    "MAPPING_UNAPPROVED",
    "UNIT_CONVERSION_UNCONFIRMED",
    "SOURCE_DATA_STALE",
    "SALES_UNITS_MISSING",
    "ON_HAND_MISSING",
    "INBOUND_MISSING",
    "NO_SHORTFALL",
    "VENDOR_OUT_OF_STOCK",
    "VENDOR_AVAILABILITY_UNKNOWN",
    "VENDOR_QUANTITY_UNKNOWN",
    "VENDOR_STOCK_BELOW_MINIMUM",
    "VENDOR_STOCK_UNORDERABLE",
    "PARTIAL_VENDOR_STOCK",
]


class PreviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ReplenishmentPolicy(PreviewModel):
    velocity_window_days: Literal[7, 14, 30]
    target_days: Annotated[int, Field(strict=True, ge=1, le=3650)]
    lead_time_days: PolicyDays
    safety_days: PolicyDays
    minimum_order_units: WholeUnits
    order_multiple_units: Annotated[int, Field(strict=True, ge=1)]

    @field_validator("velocity_window_days", mode="before")
    @classmethod
    def _whole_window(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("velocity window must be a whole number of days")
        return value


class ReplenishmentPreviewInput(PreviewModel):
    policy: ReplenishmentPolicy
    sales_units: WholeUnits | None
    on_hand_units: WholeUnits | None
    inbound_units: WholeUnits | None
    vendor_availability: Literal["IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"]
    vendor_stock_units: WholeUnits | None
    # The future live adapter must establish these facts from source evidence.
    # A local caller stating True is not itself real-data acceptance evidence.
    mapping_approved: StrictBool
    units_confirmed: StrictBool
    source_data_current: StrictBool


class QuantityBreakdown(PreviewModel):
    coverage_policy: Literal["target-plus-lead-plus-safety"] = "target-plus-lead-plus-safety"
    coverage_days: int
    daily_sales: Decimal | None = None
    days_of_supply: Decimal | None = None
    required_units: int | None = None
    net_shortfall_units: int | None = None
    order_quantity_before_vendor_cap: int | None = None


class ReplenishmentPreviewResult(PreviewModel):
    rule_version: Literal["quantity-preview-v1"] = "quantity-preview-v1"
    status: Decision
    reasons: tuple[Reason, ...] = ()
    calculation: QuantityBreakdown
    suggested_units: int | None
