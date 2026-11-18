"""Server-derived, historical quantity evaluations; never purchase authorization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StrictBool, field_validator

from app.schemas.amazon_readiness import AmazonReadinessResponse
from app.schemas.financial_context import FinancialSelection, HistoricalFinancialContext
from app.schemas.purchasing_policies import Digest, PolicyRevisionResponse, ReasonedRequest
from app.schemas.replenishment import (
    PreviewModel,
    ReplenishmentPreviewInput,
    ReplenishmentPreviewResult,
)
from app.schemas.supplier_readiness import SupplierReadinessResponse


class RecommendationPreviewRequest(PreviewModel):
    expected_approved_revision_id: uuid.UUID
    financial: FinancialSelection | None = None


class RecommendationRecordRequest(ReasonedRequest):
    request_id: uuid.UUID
    expected_approved_revision_id: uuid.UUID
    financial: FinancialSelection | None = None
    expected_evidence_sha256: Digest
    acknowledge_review: StrictBool

    @field_validator("acknowledge_review")
    @classmethod
    def acknowledged(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Acknowledge the saved inputs, results and blocking reasons.")
        return value


class PoolEvaluation(PreviewModel):
    pool_id: str
    inputs: ReplenishmentPreviewInput
    result: ReplenishmentPreviewResult
    blocking_reasons: tuple[str, ...]


class RecommendationSnapshot(PreviewModel):
    rule_version: Literal["replenishment-evaluation-v1"] = "replenishment-evaluation-v1"
    data_source: Literal["ingested_postgresql"] = "ingested_postgresql"
    policy_id: uuid.UUID
    product_id: uuid.UUID
    vendor_product_id: uuid.UUID
    marketplace_id: str
    evaluated_at: datetime
    approved_policy: PolicyRevisionResponse
    current_identity_evidence: dict[str, Any]
    policy_blocking_reasons: tuple[str, ...]
    readiness: AmazonReadinessResponse
    pools: tuple[PoolEvaluation, ...]
    status: Literal["BLOCKED", "NO_REORDER"]
    supplier_inputs: Literal["NOT_INTEGRATED"] = "NOT_INTEGRATED"
    profitability: Literal["NOT_INTEGRATED"] = "NOT_INTEGRATED"
    purchasing_eligible: Literal[False] = False


class SupplierRecommendationSnapshot(PreviewModel):
    rule_version: Literal["replenishment-evaluation-v2"] = "replenishment-evaluation-v2"
    data_source: Literal["ingested_postgresql"] = "ingested_postgresql"
    policy_id: uuid.UUID
    product_id: uuid.UUID
    vendor_product_id: uuid.UUID
    marketplace_id: str
    evaluated_at: datetime
    approved_policy: PolicyRevisionResponse
    current_identity_evidence: dict[str, Any]
    policy_blocking_reasons: tuple[str, ...]
    readiness: AmazonReadinessResponse
    pools: tuple[PoolEvaluation, ...]
    status: Literal["BLOCKED", "REVIEW_REQUIRED", "NO_REORDER"]
    supplier_inputs: Literal["VERIFIED", "BLOCKED"]
    supplier: SupplierReadinessResponse
    review_reasons: tuple[str, ...]
    profitability: Literal["NOT_INTEGRATED"] = "NOT_INTEGRATED"
    purchasing_eligible: Literal[False] = False


class FinancialRecommendationSnapshot(PreviewModel):
    rule_version: Literal["replenishment-evaluation-v3"] = "replenishment-evaluation-v3"
    data_source: Literal["ingested_postgresql"] = "ingested_postgresql"
    policy_id: uuid.UUID
    product_id: uuid.UUID
    vendor_product_id: uuid.UUID
    marketplace_id: str
    evaluated_at: datetime
    approved_policy: PolicyRevisionResponse
    current_identity_evidence: dict[str, Any]
    policy_blocking_reasons: tuple[str, ...]
    readiness: AmazonReadinessResponse
    pools: tuple[PoolEvaluation, ...]
    status: Literal["BLOCKED", "REVIEW_REQUIRED", "NO_REORDER"]
    supplier_inputs: Literal["NOT_INTEGRATED", "VERIFIED", "BLOCKED"]
    supplier: SupplierReadinessResponse | None
    financial: HistoricalFinancialContext
    review_reasons: tuple[str, ...]
    profitability: Literal["HISTORICAL_CONTEXT"] = "HISTORICAL_CONTEXT"
    purchasing_eligible: Literal[False] = False


EvaluationSnapshot = Annotated[
    RecommendationSnapshot | SupplierRecommendationSnapshot | FinancialRecommendationSnapshot,
    Field(discriminator="rule_version"),
]
EvaluationStatus = Literal["BLOCKED", "REVIEW_REQUIRED", "NO_REORDER"]


class RecommendationPreview(PreviewModel):
    snapshot: EvaluationSnapshot
    evidence_sha256: str


class RecommendationResponse(PreviewModel):
    id: uuid.UUID
    request_id: uuid.UUID
    policy_id: uuid.UUID
    approved_revision_id: uuid.UUID
    product_id: uuid.UUID
    vendor_product_id: uuid.UUID
    marketplace_id: str
    status: EvaluationStatus
    snapshot: EvaluationSnapshot
    evidence_sha256: str
    snapshot_sha256: str
    recorded_by_user_id: uuid.UUID
    recorded_at: datetime
    reason: str
    historical: Literal[True] = True
    purchasing_eligible: Literal[False] = False


class RecommendationPage(PreviewModel):
    items: tuple[RecommendationResponse, ...]
    page: int
    page_size: int
    total: int
