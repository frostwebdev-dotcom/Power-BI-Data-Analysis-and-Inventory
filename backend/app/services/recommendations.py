"""Audited historical evaluations of approved policy and observed source inputs."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.security import Principal
from app.db.transaction import transaction
from app.models import ReplenishmentRecommendation
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.repositories.recommendations import RecommendationRepository
from app.schemas.amazon_readiness import AmazonReadinessResponse
from app.schemas.purchasing_policies import PolicyRevisionResponse
from app.schemas.recommendations import (
    EvaluationSnapshot,
    EvaluationStatus,
    PoolEvaluation,
    RecommendationPage,
    RecommendationPreview,
    RecommendationPreviewRequest,
    RecommendationRecordRequest,
    RecommendationResponse,
    RecommendationSnapshot,
    SupplierRecommendationSnapshot,
)
from app.schemas.replenishment import ReplenishmentPreviewInput
from app.schemas.supplier_readiness import SupplierReadinessResponse
from app.services import audit
from app.services import purchasing_policies as policies
from app.services.amazon_readiness import check_amazon_readiness
from app.services.replenishment import calculate_replenishment
from app.services.supplier_readiness import check_supplier_readiness

READ = policies.READ
WRITE = policies.WRITE


class RecommendationNotFound(NotFoundError):  # noqa: N818
    code = "replenishment_recommendation_not_found"
    message = "No such saved evaluation in this organization."


class RecommendationConflict(ConflictError):  # noqa: N818
    code = "replenishment_recommendation_conflict"


def build_snapshot(
    approved: PolicyRevisionResponse,
    identity: dict[str, Any],
    policy_blocks: tuple[str, ...],
    readiness: AmazonReadinessResponse,
) -> RecommendationSnapshot:
    """Pure integration: no caller-supplied readiness flags or quantity guesses."""
    pools: list[PoolEvaluation] = []
    for pool in readiness.pools:
        inputs = ReplenishmentPreviewInput(
            policy=approved.definition.quantity,
            sales_units=pool.sales_base_units,
            on_hand_units=pool.on_hand_base_units,
            inbound_units=pool.usable_inbound_base_units,
            vendor_availability="UNKNOWN",
            vendor_stock_units=None,
            mapping_approved=not policy_blocks,
            units_confirmed=not policy_blocks,
            source_data_current=readiness.status == "DATA_AVAILABLE" and pool.quantity_input_ready,
        )
        result = calculate_replenishment(inputs)
        # Conservatively block every pool if any product-level readiness issue
        # remains. No partial configuration becomes a complete recommendation.
        blocks = {
            *policy_blocks,
            *(issue.code for issue in readiness.issues),
            *(result.reasons if result.status != "NO_REORDER" else ()),
        }
        if result.calculation.net_shortfall_units:
            blocks.add("SUPPLIER_INPUTS_NOT_VERIFIED")
        pools.append(
            PoolEvaluation(
                pool_id=pool.pool_id,
                inputs=inputs,
                result=result,
                blocking_reasons=tuple(sorted(blocks)),
            )
        )
    return RecommendationSnapshot(
        policy_id=approved.policy_id,
        product_id=approved.product_id,
        vendor_product_id=approved.vendor_product_id,
        marketplace_id=approved.marketplace_id,
        evaluated_at=readiness.evaluated_at,
        approved_policy=approved,
        current_identity_evidence=identity,
        policy_blocking_reasons=policy_blocks,
        readiness=readiness,
        pools=tuple(pools),
        status="NO_REORDER"
        if pools and all(pool.result.status == "NO_REORDER" for pool in pools)
        else "BLOCKED",
    )


def build_supplier_snapshot(
    approved: PolicyRevisionResponse,
    identity: dict[str, Any],
    policy_blocks: tuple[str, ...],
    readiness: AmazonReadinessResponse,
    supplier: SupplierReadinessResponse,
) -> SupplierRecommendationSnapshot:
    legacy = build_snapshot(approved, identity, policy_blocks, readiness)
    pools: list[PoolEvaluation] = []
    for pool in legacy.pools:
        inputs = pool.inputs.model_copy(
            update={
                "vendor_availability": supplier.availability,
                "vendor_stock_units": supplier.stock_base_units,
                "source_data_current": pool.inputs.source_data_current
                and supplier.status == "VERIFIED",
            }
        )
        result = calculate_replenishment(inputs)
        pools.append(
            PoolEvaluation(
                pool_id=pool.pool_id,
                inputs=inputs,
                result=result,
                blocking_reasons=tuple(
                    sorted(
                        {
                            *policy_blocks,
                            *supplier.issues,
                            *(issue.code for issue in readiness.issues),
                            *(result.reasons if result.status != "NO_REORDER" else ()),
                        }
                    )
                ),
            )
        )
    positive = [pool for pool in pools if pool.result.calculation.net_shortfall_units]
    review = set(supplier.review_reasons)
    if positive:
        review.add("PROFITABILITY_NOT_INTEGRATED")
    if len(positive) > 1:
        review.add("SUPPLIER_STOCK_SHARED_ACROSS_POOLS")
    status: EvaluationStatus = "BLOCKED"
    if pools and all(pool.result.status == "NO_REORDER" for pool in pools):
        status = "NO_REORDER"
    elif pools and all(pool.result.status != "BLOCKED" for pool in pools):
        status = "REVIEW_REQUIRED"
    data = legacy.model_dump(mode="json")
    data.update(
        rule_version="replenishment-evaluation-v2",
        supplier=supplier.model_dump(mode="json"),
        supplier_inputs=supplier.status,
        pools=[pool.model_dump(mode="json") for pool in pools],
        status=status,
        review_reasons=sorted(review),
    )
    return SupplierRecommendationSnapshot.model_validate(data)


def evidence_digest(snapshot: EvaluationSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    data.pop("evaluated_at")
    data["readiness"].pop("evaluated_at")
    if isinstance(snapshot, SupplierRecommendationSnapshot):
        data["supplier"].pop("evaluated_at")
    if not snapshot.readiness.order_coverage_run_ids:
        # With no baseline the readiness adapter reports a clock-derived window.
        # Its quantities stay unknown; that display clock is not source evidence.
        data["readiness"].pop("demand_window_start")
        data["readiness"].pop("demand_window_end")
    return policies.digest(data)


def _evaluate(
    session: Session,
    actor: Principal,
    identifier: uuid.UUID,
    approval: uuid.UUID,
    now: Callable[[], datetime],
) -> RecommendationPreview:
    approved, identity, blocks = policies.calculation_policy(session, actor, identifier, approval)
    readiness = check_amazon_readiness(
        session, actor, approved.definition.amazon, now=now, lock_sources=True
    )
    snapshot = build_snapshot(approved, identity, blocks, readiness)
    if approved.definition.supplier is not None:
        supplier = check_supplier_readiness(
            session,
            actor.organization_id,
            approved.product_id,
            approved.vendor_product_id,
            approved.definition.supplier,
            at=readiness.evaluated_at,
        )
        enriched = build_supplier_snapshot(approved, identity, blocks, readiness, supplier)
        return RecommendationPreview(snapshot=enriched, evidence_sha256=evidence_digest(enriched))
    return RecommendationPreview(snapshot=snapshot, evidence_sha256=evidence_digest(snapshot))


def preview_recommendation(
    session: Session,
    actor: Principal,
    identifier: uuid.UUID,
    payload: RecommendationPreviewRequest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RecommendationPreview:
    actor.require_roles(READ)
    return _evaluate(session, actor, identifier, payload.expected_approved_revision_id, now)


def _response(row: ReplenishmentRecommendation) -> RecommendationResponse:
    return RecommendationResponse(
        id=row.id,
        request_id=row.request_id,
        policy_id=row.policy_id,
        approved_revision_id=row.approved_revision_id,
        product_id=row.product_id,
        vendor_product_id=row.vendor_product_id,
        marketplace_id=row.marketplace_id,
        status=row.status,
        snapshot=TypeAdapter(EvaluationSnapshot).validate_python(row.snapshot),
        evidence_sha256=row.evidence_sha256,
        snapshot_sha256=row.snapshot_sha256,
        recorded_by_user_id=row.recorded_by_user_id,
        recorded_at=row.recorded_at,
        reason=row.reason,
    )


def record_recommendation(
    session: Session,
    actor: Principal,
    identifier: uuid.UUID,
    payload: RecommendationRecordRequest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RecommendationResponse:
    actor.require_roles(WRITE)
    repository = RecommendationRepository(session, actor.organization_id)
    fingerprint = policies.digest({"policy_id": str(identifier), **payload.model_dump(mode="json")})
    with transaction(session):
        repository.lock_request(payload.request_id)
        policies.validate_actor(PurchasingPolicyRepository(session, actor.organization_id), actor)
        existing = repository.by_request(payload.request_id)
        if existing:
            if (
                existing.request_sha256 != fingerprint
                or existing.recorded_by_user_id != actor.user_id
            ):
                raise RecommendationConflict(details={"code": "REQUEST_ID_REUSED"})
            return _response(existing)
        preview = _evaluate(session, actor, identifier, payload.expected_approved_revision_id, now)
        if preview.evidence_sha256 != payload.expected_evidence_sha256:
            raise RecommendationConflict(details={"code": "RECOMMENDATION_EVIDENCE_CHANGED"})
        snapshot = preview.snapshot.model_dump(mode="json")
        row = ReplenishmentRecommendation(
            id=uuid.uuid4(),
            organization_id=actor.organization_id,
            request_id=payload.request_id,
            request_sha256=fingerprint,
            policy_id=identifier,
            approved_revision_id=payload.expected_approved_revision_id,
            product_id=preview.snapshot.product_id,
            vendor_product_id=preview.snapshot.vendor_product_id,
            marketplace_id=preview.snapshot.marketplace_id,
            status=preview.snapshot.status,
            snapshot=snapshot,
            evidence_sha256=preview.evidence_sha256,
            snapshot_sha256=policies.digest(snapshot),
            recorded_by_user_id=actor.user_id,
            recorded_at=datetime.now(UTC),
            reason=payload.reason,
            purchasing_eligible=False,
        )
        session.add(row)
        session.flush()
        audit.record(
            session,
            organization_id=actor.organization_id,
            actor=actor,
            action="replenishment_recommendation.recorded",
            entity_type="replenishment_recommendation",
            entity_id=row.id,
            after={
                "policy_id": str(identifier),
                "approved_revision_id": str(row.approved_revision_id),
                "request_id": str(payload.request_id),
                "status": row.status,
                "evidence_sha256": row.evidence_sha256,
                "snapshot_sha256": row.snapshot_sha256,
                "reason": row.reason,
            },
        )
        result = _response(row)
    return result


def get_recommendation(
    session: Session, actor: Principal, identifier: uuid.UUID
) -> RecommendationResponse:
    actor.require_roles(READ)
    row = RecommendationRepository(session, actor.organization_id).get(identifier)
    if row is None:
        raise RecommendationNotFound()
    return _response(row)


def recommendation_page(
    session: Session,
    actor: Principal,
    page: int,
    size: int,
    *,
    policy_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: EvaluationStatus | None = None,
) -> RecommendationPage:
    actor.require_roles(READ)
    rows, total = RecommendationRepository(session, actor.organization_id).page(
        page, size, policy_id=policy_id, product_id=product_id, status=status
    )
    return RecommendationPage(
        items=tuple(_response(row) for row in rows), page=page, page_size=size, total=total
    )
