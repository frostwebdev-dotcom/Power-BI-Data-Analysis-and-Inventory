"""Read-only selected-offer comparison and explicitly historical review queues."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.financial_context import FinancialSelection
from app.schemas.inbound import InboundSelection
from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import EvaluationSnapshot, EvaluationStatus
from app.schemas.replenishment import PreviewModel


class ComparisonChoice(PreviewModel):
    policy_id: uuid.UUID
    expected_approved_revision_id: uuid.UUID
    inbound: InboundSelection | None = None


class VendorComparisonRequest(PreviewModel):
    product_id: uuid.UUID
    choices: Annotated[tuple[ComparisonChoice, ...], Field(min_length=1, max_length=10)]
    financial: FinancialSelection | None = None
    order_by: Literal["VENDOR_NAME", "BASE_PRICE"] = "VENDOR_NAME"

    @model_validator(mode="after")
    def distinct_choices(self) -> VendorComparisonRequest:
        if len({choice.policy_id for choice in self.choices}) != len(self.choices):
            raise ValueError("Select each policy only once.")
        return self


class BasePrice(PreviewModel):
    currency: Literal["USD"] = "USD"
    reported_price: Decimal
    base_units_per_price_unit: int
    includes_tax: bool
    exact_numerator: str
    exact_denominator: str
    display_per_base_unit: Decimal
    display_is_rounded: bool


class ComparedOffer(PreviewModel):
    identity: PolicyIdentity
    policy_id: uuid.UUID
    approved_revision_id: uuid.UUID
    base_price: BasePrice | None
    price_comparable: bool
    comparison_reasons: tuple[str, ...]
    evidence_sha256: str
    snapshot: EvaluationSnapshot
    purchasing_eligible: Literal[False] = False


class VendorComparisonResponse(PreviewModel):
    product_id: uuid.UUID
    catalog_item_number: str
    product_name: str
    evaluated_at: datetime
    scope: Literal["EXPLICITLY_SELECTED_POLICIES"] = "EXPLICITLY_SELECTED_POLICIES"
    selected_count: int
    comparable_price_count: int
    price_comparison_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    requested_order: Literal["VENDOR_NAME", "BASE_PRICE"]
    applied_order: Literal["VENDOR_NAME", "BASE_PRICE"]
    issues: tuple[str, ...]
    offers: tuple[ComparedOffer, ...]
    saved: Literal[False] = False
    stock_reserved: Literal[False] = False
    purchasing_eligible: Literal[False] = False


class SavedReviewItem(PreviewModel):
    id: uuid.UUID
    product_id: uuid.UUID
    policy_id: uuid.UUID
    approved_revision_id: uuid.UUID
    identity: PolicyIdentity
    status: EvaluationStatus
    approval_current: bool
    recorded_at: datetime
    evaluated_at: datetime
    demand_window_end: datetime
    positive_shortfall: bool | None
    stockout_risk_at_evaluation: bool | None
    supplier_inputs: str
    commercial_status: str | None
    financial_import_id: uuid.UUID | None
    reasons: tuple[str, ...]
    reason_count: int
    reasons_truncated: bool
    reason: str
    historical: Literal[True] = True
    source_freshness_rechecked: Literal[False] = False
    purchasing_eligible: Literal[False] = False


class SavedReviewPage(PreviewModel):
    items: tuple[SavedReviewItem, ...]
    page: int
    page_size: int
    total: int
    historical: Literal[True] = True


class SavedReviewSummary(PreviewModel):
    generated_at: datetime
    latest_saved_total: int
    current_approval_total: int
    approval_changed_total: int
    current_approval_blocked: int
    current_approval_review_required: int
    current_approval_no_reorder: int
    current_approval_positive_shortfall: int
    historical: Literal[True] = True
    source_freshness_rechecked: Literal[False] = False
    purchasing_eligible: Literal[False] = False
