"""Explicit purchasing assumptions, immutable versions and reviewed decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    field_validator,
    model_serializer,
    model_validator,
)

from app.schemas.amazon_readiness import AmazonReadinessRequest, Factor, Reference
from app.schemas.commercials import CommercialPolicy
from app.schemas.replenishment import PreviewModel, ReplenishmentPolicy
from app.schemas.supplier_readiness import SupplierPolicy

Reason = Annotated[str, Field(min_length=10, max_length=1000)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Action = Literal["APPROVE", "REVOKE"]


class PurchasingPolicyDefinition(PreviewModel):
    rule_version: Literal["purchasing-policy-v1"] = "purchasing-policy-v1"
    quantity: ReplenishmentPolicy
    amazon: AmazonReadinessRequest
    coverage_policy: Literal["target-plus-lead-plus-safety"]
    coverage_confirmation_reference: Reference | None
    inbound_policy: Literal["CONFIRMED_BEFORE_STOCKOUT_ONLY"]
    vendor_base_units_per_order_unit: Factor | None
    vendor_unit_confirmation_reference: Reference | None
    supplier: SupplierPolicy | None = None
    commercial: CommercialPolicy | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.supplier is None:
            data.pop("supplier", None)
        if self.commercial is None:
            data.pop("commercial", None)
        return data

    @model_validator(mode="after")
    def consistent_units_and_window(self) -> PurchasingPolicyDefinition:
        if self.commercial is not None and self.supplier is None:
            raise ValueError("Commercial checks require explicit supplier source settings.")
        if self.quantity.velocity_window_days != self.amazon.velocity_window_days:
            raise ValueError("Quantity and Amazon demand windows must agree.")
        if max(self.quantity.minimum_order_units, self.quantity.order_multiple_units) > 1000000000:
            raise ValueError("Persisted ordering constraints cannot exceed one billion base units.")
        return self


class ReasonedRequest(PreviewModel):
    reason: Reason

    @field_validator("reason", mode="before")
    @classmethod
    def trimmed_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PolicyCreateRequest(ReasonedRequest):
    vendor_product_id: uuid.UUID
    definition: PurchasingPolicyDefinition


class PolicyDraftRequest(ReasonedRequest):
    expected_head_id: uuid.UUID
    definition: PurchasingPolicyDefinition


class PolicyReviewRequest(ReasonedRequest):
    action: Action
    expected_head_id: uuid.UUID
    reviewed_revision_id: uuid.UUID
    definition_sha256: Digest
    context_sha256: Digest
    acknowledge_review: StrictBool

    @field_validator("acknowledge_review")
    @classmethod
    def acknowledged(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Acknowledge the complete definition and current review evidence.")
        return value


class PolicyRevisionResponse(PreviewModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    product_id: uuid.UUID
    vendor_product_id: uuid.UUID
    marketplace_id: str
    revision_number: int
    supersedes_revision_id: uuid.UUID | None
    reviewed_revision_id: uuid.UUID | None
    kind: Literal["DRAFT", "APPROVED", "REVOKED"]
    definition: PurchasingPolicyDefinition
    definition_sha256: str
    review_context: dict[str, Any] | None
    context_sha256: str | None
    recorded_by_user_id: uuid.UUID
    recorded_at: datetime
    reason: str
    purchasing_eligible: Literal[False] = False


class PolicyIdentity(PreviewModel):
    catalog_item_number: str
    product_name: str
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_code: str
    vendor_sku: str


class PolicySummary(PreviewModel):
    identity: PolicyIdentity
    policy_id: uuid.UUID
    head: PolicyRevisionResponse
    approved: PolicyRevisionResponse | None
    # An approval records assumptions, never current stock/arrival readiness.
    current_data_verified: Literal[False] = False
    purchasing_eligible: Literal[False] = False


class PolicyPage(PreviewModel):
    items: tuple[PolicySummary, ...]
    page: int
    page_size: int
    total: int


class PolicyRevisionPage(PreviewModel):
    items: tuple[PolicyRevisionResponse, ...]
    page: int
    page_size: int
    total: int


class PolicyReviewPreview(PreviewModel):
    action: Action
    policy_id: uuid.UUID
    expected_head_id: uuid.UUID
    reviewed_revision_id: uuid.UUID
    definition: PurchasingPolicyDefinition
    definition_sha256: str
    review_context: dict[str, Any]
    context_sha256: str
    blocking_reasons: tuple[str, ...]
    review_allowed: bool
    purchasing_eligible: Literal[False] = False
