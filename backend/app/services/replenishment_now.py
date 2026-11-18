"""Recheck explicit approved policies, with supplier-independent Amazon urgency."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy.orm import Session

from app.core.security import Principal
from app.db.transaction import transaction
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.repositories.replenishment_now import CurrentReplenishmentRepository
from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import (
    CommercialRecommendationSnapshot,
    EvaluationSnapshot,
    InboundRecommendationSnapshot,
    RecommendationPreview,
    RecommendationPreviewRequest,
)
from app.schemas.replenishment import ReplenishmentPreviewInput
from app.schemas.replenishment_now import (
    CurrentCheckRequest,
    CurrentCheckResponse,
    CurrentCheckSummary,
    CurrentChoicePage,
    CurrentPolicyCheck,
    CurrentPolicyChoice,
    CurrentPoolSignal,
    Opportunity,
    Urgency,
)
from app.services import purchasing_policies as policies
from app.services.recommendations import preview_recommendation
from app.services.replenishment import calculate_replenishment


def pool_signals(snapshot: EvaluationSnapshot) -> tuple[CurrentPoolSignal, ...]:
    quantity = snapshot.approved_policy.definition.quantity
    signals = []
    for pool in snapshot.readiness.pools:
        inputs = ReplenishmentPreviewInput(
            policy=quantity,
            sales_units=pool.sales_base_units,
            on_hand_units=pool.on_hand_base_units,
            inbound_units=pool.usable_inbound_base_units,
            vendor_availability="UNKNOWN",
            vendor_stock_units=None,
            mapping_approved=not snapshot.policy_blocking_reasons,
            units_confirmed=not snapshot.policy_blocking_reasons,
            source_data_current=snapshot.readiness.status == "DATA_AVAILABLE"
            and pool.quantity_input_ready,
        )
        result = calculate_replenishment(inputs)
        calculation = result.calculation
        shortfall = calculation.net_shortfall_units
        urgency: Urgency = "UNKNOWN"
        ratio = None
        if shortfall is not None:
            assert (
                inputs.sales_units is not None
                and inputs.on_hand_units is not None
                and inputs.inbound_units is not None
            )
            if inputs.sales_units > 0:
                ratio = Fraction(
                    (inputs.on_hand_units + inputs.inbound_units) * quantity.velocity_window_days,
                    inputs.sales_units,
                )
            urgency = (
                "NO_REORDER"
                if shortfall == 0
                else "URGENT"
                if ratio is not None and ratio <= quantity.lead_time_days + quantity.safety_days
                else "REPLENISH"
            )
        reasons = tuple(
            sorted(
                {
                    *snapshot.policy_blocking_reasons,
                    *(i.code for i in snapshot.readiness.issues),
                    *(result.reasons if shortfall is None else ()),
                }
            )
        )
        signals.append(
            CurrentPoolSignal(
                pool_id=pool.pool_id,
                channel=pool.channel,
                urgency=urgency,
                sales_base_units=inputs.sales_units,
                on_hand_base_units=inputs.on_hand_units,
                usable_inbound_base_units=inputs.inbound_units,
                window_days=quantity.velocity_window_days,
                lead_plus_safety_days=quantity.lead_time_days + quantity.safety_days,
                required_units=calculation.required_units,
                net_shortfall_units=shortfall,
                supply_days_numerator=str(ratio.numerator) if ratio is not None else None,
                supply_days_denominator=str(ratio.denominator) if ratio is not None else None,
                reasons=reasons,
            )
        )
    return tuple(signals)


def opportunity(
    snapshot: EvaluationSnapshot, urgency: Urgency
) -> tuple[Opportunity, tuple[str, ...]]:
    if urgency == "UNKNOWN":
        return "UNCONFIRMED", ("CURRENT_AMAZON_INPUTS_UNCONFIRMED",)
    if urgency == "NO_REORDER":
        return "NO_SHORTFALL", ()
    supplier = getattr(snapshot, "supplier", None)
    if supplier is None or supplier.status != "VERIFIED":
        return "UNCONFIRMED", ("CURRENT_SUPPLIER_INPUTS_UNCONFIRMED",)
    if supplier.availability == "OUT_OF_STOCK" or supplier.stock_base_units == 0:
        return "UNAVAILABLE", ("CURRENT_SUPPLIER_STOCK_UNAVAILABLE",)
    if supplier.availability != "IN_STOCK" or supplier.stock_base_units is None:
        return "UNCONFIRMED", ("CURRENT_SUPPLIER_STOCK_UNCONFIRMED",)
    if (
        not isinstance(snapshot, (CommercialRecommendationSnapshot, InboundRecommendationSnapshot))
        or snapshot.commercial is None
        or snapshot.allocation is None
    ):
        return "UNCONFIRMED", ("COMMERCIAL_CHECKS_NOT_CONFIGURED",)
    if snapshot.commercial.status == "CONSTRAINT_NOT_MET":
        return "UNAVAILABLE", ("SUPPLIER_MINIMUM_NOT_MET",)
    if snapshot.allocation.total_allocated_units == 0:
        return "UNAVAILABLE", ("NO_USABLE_SUPPLIER_ALLOCATION",)
    if (
        snapshot.status == "BLOCKED"
        or snapshot.commercial.status != "ASSESSED"
        or snapshot.commercial.price.status != "VERIFIED"
        or snapshot.commercial.goods_line_value is None
        or snapshot.commercial.price.reported_price is None
        or snapshot.commercial.price.reported_price <= 0
        or len(snapshot.commercial.checks) != 2
        or any(c.status != "MET" for c in snapshot.commercial.checks)
        or snapshot.allocation.status not in {"COMPLETE", "PARTIAL"}
        or snapshot.allocation.total_allocated_units is None
        or snapshot.allocation.total_allocated_units <= 0
    ):
        return "UNCONFIRMED", ("CURRENT_COMMERCIAL_REVIEW_INCOMPLETE",)
    return "REVIEW_OPPORTUNITY", (
        "REQUIRES_REVIEW_NOT_PURCHASE_APPROVAL",
        "LANDED_COST_NOT_EVALUATED",
        "SUPPLIER_STOCK_NOT_RESERVED",
    )


def current_policy_check(
    identity: PolicyIdentity, preview: RecommendationPreview
) -> CurrentPolicyCheck:
    snapshot = preview.snapshot
    pools = pool_signals(snapshot)
    urgency: Urgency = "UNKNOWN"
    if pools and all(p.urgency != "UNKNOWN" for p in pools):
        urgency = (
            "URGENT"
            if any(p.urgency == "URGENT" for p in pools)
            else "REPLENISH"
            if any(p.urgency == "REPLENISH" for p in pools)
            else "NO_REORDER"
        )
    available, reasons = opportunity(snapshot, urgency)
    return CurrentPolicyCheck(
        identity=identity,
        policy_id=snapshot.policy_id,
        product_id=snapshot.product_id,
        approved_revision_id=snapshot.approved_policy.id,
        urgency=urgency,
        opportunity=available,
        reasons=tuple(
            sorted(
                {
                    *reasons,
                    *(r for pool in pools for r in pool.reasons),
                    *(("NO_CURRENT_POOLS",) if not pools else ()),
                }
            )
        ),
        pools=pools,
        snapshot=snapshot,
        evidence_sha256=preview.evidence_sha256,
    )


def current_check(
    session: Session,
    actor: Principal,
    payload: CurrentCheckRequest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CurrentCheckResponse:
    actor.require_roles(policies.READ)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    with transaction(session):
        policies.validate_actor(repository, actor)
        roots = []
        for choice in payload.choices:
            root = repository.root(choice.policy_id)
            if root is None:
                raise policies.PolicyNotFound()
            roots.append((root, choice))
        roots.sort(
            key=lambda pair: (
                str(pair[0].product_id),
                str(pair[0].vendor_product_id),
                pair[0].marketplace_id,
                str(pair[0].id),
            )
        )
        for root, _ in roots:
            repository.lock(root.product_id, root.vendor_product_id, root.marketplace_id)
        at = now()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Current check time must be timezone-aware")
        items = []
        for root, choice in roots:
            preview = preview_recommendation(
                session,
                actor,
                root.id,
                RecommendationPreviewRequest(
                    expected_approved_revision_id=choice.expected_approved_revision_id,
                    inbound=choice.inbound,
                ),
                now=lambda: at,
            )
            identity = repository.identity(root.product_id, root.vendor_product_id)
            if identity is None:
                raise policies.PolicyNotFound()
            items.append(current_policy_check(PolicyIdentity.model_validate(identity), preview))
        priority = {"URGENT": 0, "REPLENISH": 1, "UNKNOWN": 2, "NO_REORDER": 3}
        items.sort(
            key=lambda item: (
                priority[item.urgency],
                item.identity.catalog_item_number.casefold(),
                item.identity.vendor_name.casefold(),
                item.identity.vendor_sku.casefold(),
                str(item.policy_id),
            )
        )
        return CurrentCheckResponse(
            evaluated_at=at,
            items=tuple(items),
            summary=CurrentCheckSummary(
                checked_policies=len(items),
                urgent=sum(i.urgency == "URGENT" for i in items),
                replenish=sum(i.urgency == "REPLENISH" for i in items),
                no_reorder=sum(i.urgency == "NO_REORDER" for i in items),
                unknown=sum(i.urgency == "UNKNOWN" for i in items),
                review_opportunities=sum(i.opportunity == "REVIEW_OPPORTUNITY" for i in items),
            ),
        )


def current_choices(
    session: Session,
    actor: Principal,
    page: int,
    size: int,
    *,
    q: str | None = None,
    product_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
) -> CurrentChoicePage:
    actor.require_roles(policies.READ)
    rows, total = CurrentReplenishmentRepository(session, actor.organization_id).page(
        page, size, q=q, product_id=product_id, vendor_id=vendor_id
    )
    return CurrentChoicePage(
        items=tuple(
            CurrentPolicyChoice(
                policy_id=row[0].id,
                product_id=row[0].product_id,
                approved_revision_id=row.approval_id,
                approved_revision_number=row.approved_revision_number,
                identity=PolicyIdentity(
                    catalog_item_number=row.catalog_item_number,
                    product_name=row.product_name,
                    vendor_id=row.vendor_id,
                    vendor_name=row.vendor_name,
                    vendor_code=row.vendor_code,
                    vendor_sku=row.vendor_sku,
                ),
            )
            for row in rows
        ),
        page=page,
        page_size=size,
        total=total,
    )
