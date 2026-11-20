"""Explicit commercial assumptions and bounded, non-reserving stock allocation."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.schemas.amazon_readiness import Factor, Reference
from app.schemas.replenishment import PreviewModel


class CommercialPolicy(PreviewModel):
    currency: Literal["USD"]
    base_units_per_price_unit: Factor | None
    price_confirmation_reference: Reference | None
    minimum_quantity_scope: Literal["THIS_SKU_SUPPLIER_ORDER_UNITS"] | None
    minimum_value_scope: Literal["THIS_SKU_GOODS_LINE_AT_REPORTED_PRICE"] | None
    minimum_confirmation_reference: Reference | None
    terms_review_reference: Reference | None
    pool_priority: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")], ...], Field(max_length=200)
    ]
    allocation_reference: Reference | None

    @model_validator(mode="after")
    def distinct_priorities(self) -> CommercialPolicy:
        if len(set(self.pool_priority)) != len(self.pool_priority):
            raise ValueError("Every pool can occur only once in the explicit priority sequence.")
        return self


class AllocationRow(PreviewModel):
    pool_id: str
    priority: int
    requested_units: int | None
    allocated_units: int | None
    remaining_supplier_units: int | None
    reasons: tuple[str, ...]


class SharedStockAllocation(PreviewModel):
    rule_version: Literal["approved-pool-priority-v1"] = "approved-pool-priority-v1"
    scope: Literal["THIS_PRODUCT_SUPPLIER_SKU_EVALUATION"] = "THIS_PRODUCT_SUPPLIER_SKU_EVALUATION"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED", "UNCONFIRMED"]
    supplier_stock_units: int | None
    total_allocated_units: int | None
    remaining_supplier_units: int | None
    rows: tuple[AllocationRow, ...]
    reasons: tuple[str, ...]
    stock_reserved: Literal[False] = False
    purchasing_eligible: Literal[False] = False


class CurrentSupplierPrice(PreviewModel):
    status: Literal["VERIFIED", "UNAVAILABLE"]
    reported_price: Decimal | None
    currency: str | None
    base_units_per_price_unit: int | None
    evidence: dict[str, Any]
    issues: tuple[str, ...]


class MinimumCheck(PreviewModel):
    name: Literal["SUPPLIER_ORDER_UNITS", "GOODS_LINE_VALUE"]
    scope: str | None
    minimum: Decimal | None
    observed: Decimal | None
    status: Literal["MET", "NOT_MET", "UNCONFIRMED", "NO_ALLOCATION"]


class CommercialAssessment(PreviewModel):
    basis: Literal["CURRENT_SUPPLIER_REPORTED_PRICE"] = "CURRENT_SUPPLIER_REPORTED_PRICE"
    policy: CommercialPolicy
    price: CurrentSupplierPrice
    goods_line_value: Decimal | None
    currency: Literal["USD"]
    supplier_order_units: int | None
    price_units: int | None
    checks: tuple[MinimumCheck, ...]
    status: Literal["ASSESSED", "INCOMPLETE", "CONSTRAINT_NOT_MET"]
    review_reasons: tuple[str, ...]
    landed_cost_verified: Literal[False] = False
    purchasing_eligible: Literal[False] = False
