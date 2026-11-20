"""Explicit reviewed receipt-age and as-stated supplier quantity contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from app.schemas.amazon_readiness import Factor, Reference
from app.schemas.replenishment import PreviewModel


class SupplierPolicy(PreviewModel):
    profile_id: uuid.UUID | None
    inventory_max_age_seconds: Annotated[int, Field(strict=True, ge=1, le=2678400)]
    freshness_basis: Literal["FILE_RECEIPT_TIME"]
    freshness_confirmation_reference: Reference | None
    base_units_per_source_quantity: Factor | None
    quantity_confirmation_reference: Reference | None
    availability_confirmation_reference: Reference | None


class SupplierReadinessResponse(PreviewModel):
    rule_version: Literal["supplier-readiness-v1"] = "supplier-readiness-v1"
    evaluated_at: datetime
    freshness_basis: Literal["FILE_RECEIPT_TIME"] = "FILE_RECEIPT_TIME"
    supplier_effective_time_verified: Literal[False] = False
    status: Literal["VERIFIED", "BLOCKED"]
    availability: Literal["IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"]
    stock_base_units: int | None
    issues: tuple[str, ...]
    review_reasons: tuple[str, ...]
    evidence: dict[str, Any]
    purchasing_eligible: Literal[False] = False
