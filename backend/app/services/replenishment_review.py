"""Latest saved evidence is historical even when its policy approval is current."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.core.security import Principal
from app.repositories.replenishment_review import ReplenishmentReviewRepository
from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import (
    CommercialRecommendationSnapshot,
    EvaluationSnapshot,
    EvaluationStatus,
    FinancialRecommendationSnapshot,
    InboundRecommendationSnapshot,
)
from app.schemas.replenishment_review import SavedReviewItem, SavedReviewPage, SavedReviewSummary
from app.services.purchasing_policies import READ


def historical_shortfall(snapshot: EvaluationSnapshot) -> bool | None:
    values = [pool.result.calculation.net_shortfall_units for pool in snapshot.pools]
    if any(value is not None and value > 0 for value in values):
        return True
    return False if values and all(value == 0 for value in values) else None


def historical_stockout_risk(snapshot: EvaluationSnapshot) -> bool | None:
    if (
        not snapshot.pools
        or snapshot.policy_blocking_reasons
        or snapshot.readiness.status != "DATA_AVAILABLE"
    ):
        return None
    for pool in snapshot.pools:
        i = pool.inputs
        if (
            not (i.mapping_approved and i.units_confirmed and i.source_data_current)
            or i.sales_units is None
            or i.on_hand_units is None
            or i.inbound_units is None
            or pool.result.calculation.net_shortfall_units is None
        ):
            return None
    return any(
        pool.inputs.sales_units is not None
        and pool.inputs.sales_units > 0
        and pool.inputs.on_hand_units is not None
        and pool.inputs.inbound_units is not None
        and (pool.result.calculation.net_shortfall_units or 0) > 0
        and (pool.inputs.on_hand_units + pool.inputs.inbound_units)
        * pool.inputs.policy.velocity_window_days
        <= pool.inputs.sales_units
        * (pool.inputs.policy.lead_time_days + pool.inputs.policy.safety_days)
        for pool in snapshot.pools
    )


def review_page(
    session: Session,
    actor: Principal,
    page: int,
    size: int,
    *,
    q: str | None = None,
    product_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    status: EvaluationStatus | None = None,
    approval: Literal["ALL", "CURRENT", "CHANGED"] = "CURRENT",
    shortfall_only: bool = False,
) -> SavedReviewPage:
    actor.require_roles(READ)
    rows, total = ReplenishmentReviewRepository(session, actor.organization_id).page(
        page,
        size,
        q=q,
        product_id=product_id,
        vendor_id=vendor_id,
        policy_id=policy_id,
        status=status,
        approval=approval,
        shortfall_only=shortfall_only,
    )
    items = []
    for row in rows:
        saved = row[0]
        snapshot: EvaluationSnapshot = TypeAdapter(EvaluationSnapshot).validate_python(
            saved.snapshot
        )
        reasons = set(snapshot.policy_blocking_reasons) | {
            issue.code for issue in snapshot.readiness.issues
        }
        reasons.update(getattr(snapshot, "review_reasons", ()))
        for pool in snapshot.pools:
            reasons.update(pool.blocking_reasons)
        financial = (
            snapshot.financial
            if isinstance(
                snapshot,
                (
                    FinancialRecommendationSnapshot,
                    CommercialRecommendationSnapshot,
                    InboundRecommendationSnapshot,
                ),
            )
            else None
        )
        commercial = (
            snapshot.commercial
            if isinstance(
                snapshot, (CommercialRecommendationSnapshot, InboundRecommendationSnapshot)
            )
            else None
        )
        identity = PolicyIdentity(
            catalog_item_number=row.catalog_item_number,
            product_name=row.product_name,
            vendor_id=row.vendor_id,
            vendor_name=row.vendor_name,
            vendor_code=row.vendor_code,
            vendor_sku=row.vendor_sku,
        )
        items.append(
            SavedReviewItem(
                id=saved.id,
                product_id=saved.product_id,
                policy_id=saved.policy_id,
                approved_revision_id=saved.approved_revision_id,
                identity=identity,
                status=saved.status,
                approval_current=bool(row.approval_current),
                recorded_at=saved.recorded_at,
                evaluated_at=snapshot.evaluated_at,
                demand_window_end=snapshot.readiness.demand_window_end,
                positive_shortfall=historical_shortfall(snapshot),
                stockout_risk_at_evaluation=historical_stockout_risk(snapshot),
                supplier_inputs=snapshot.supplier_inputs,
                commercial_status=commercial.status if commercial else None,
                financial_import_id=financial.import_id if financial else None,
                reasons=tuple(sorted(reasons)[:50]),
                reason_count=len(reasons),
                reasons_truncated=len(reasons) > 50,
                reason=saved.reason,
            )
        )
    return SavedReviewPage(items=tuple(items), page=page, page_size=size, total=total)


def review_summary(session: Session, actor: Principal) -> SavedReviewSummary:
    actor.require_roles(READ)
    return SavedReviewSummary.model_validate(
        {
            "generated_at": datetime.now(UTC),
            **ReplenishmentReviewRepository(session, actor.organization_id).counts(),
        }
    )
