"""Reviewed inbound evidence with immutable, audited, source-bound revisions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.security import Principal, RoleCode
from app.db.transaction import transaction
from app.models.inbound import InboundEvidenceRevision
from app.repositories.amazon_readiness import AmazonReadinessRepository
from app.repositories.inbound import InboundRepository
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.schemas.amazon_readiness import AmazonReadinessResponse, ReadinessIssue
from app.schemas.inbound import (
    InboundAssessment,
    InboundContext,
    InboundContextRequest,
    InboundPage,
    InboundPosition,
    InboundPreview,
    InboundProposal,
    InboundRecordRequest,
    InboundRevision,
    InboundRevokeRequest,
    InboundSelection,
    ReviewedReadinessResponse,
)
from app.schemas.purchasing_policies import PolicyRevisionResponse
from app.services import audit
from app.services import purchasing_policies as policies
from app.services.amazon_readiness import check_amazon_readiness
from app.services.inbound_math import qualify_arrivals

MANAGE = (RoleCode.PURCHASING_MANAGER,)
INBOUND_CODES = {"INBOUND_ARRIVAL_UNCONFIRMED", "FBM_INBOUND_NOT_INGESTED"}


class InboundNotFound(NotFoundError):  # noqa: N818
    code = "inbound_evidence_not_found"
    message = "No such inbound evidence or policy pool in this organization."


class InboundConflict(ConflictError):  # noqa: N818
    code = "inbound_evidence_conflict"


def response(row: InboundEvidenceRevision) -> InboundRevision:
    return InboundRevision.model_validate(
        {name: getattr(row, name) for name in InboundRevision.model_fields}
    )


def _context(
    session: Session,
    actor: Principal,
    policy_id: uuid.UUID,
    payload: InboundContextRequest,
    at: datetime,
    prepared: tuple[
        PolicyRevisionResponse, dict[str, Any], tuple[str, ...], AmazonReadinessResponse
    ]
    | None = None,
) -> tuple[InboundContext, AmazonReadinessResponse, PolicyRevisionResponse]:
    if prepared is None:
        approved, identity, blocks = policies.calculation_policy(
            session, actor, policy_id, payload.expected_approved_revision_id
        )
        readiness = check_amazon_readiness(
            session, actor, approved.definition.amazon, now=lambda: at, lock_sources=True
        )
    else:
        approved, identity, blocks, readiness = prepared
    choice = next(
        (p for p in approved.definition.amazon.stock_pools if p.pool_id == payload.pool_id), None
    )
    pool = next((p for p in readiness.pools if p.pool_id == payload.pool_id), None)
    if choice is None or pool is None:
        raise InboundNotFound()
    listings = {listing.listing_id: listing for listing in readiness.listings}
    source = listings.get(choice.stock_source_listing_id)
    repository = AmazonReadinessRepository(session, actor.organization_id, lock_sources=True)
    snapshots = (
        repository.snapshots(
            readiness.inventory_run.id,
            {listings[i].seller_sku for i in choice.listing_ids if i in listings},
        )
        if readiness.inventory_run
        else {}
    )
    context = {
        "policy_id": str(policy_id),
        "approved_revision_id": str(approved.id),
        "definition_sha256": approved.definition_sha256,
        "current_identity_sha256": policies.digest(identity),
        "product_id": str(approved.product_id),
        "marketplace_id": approved.marketplace_id,
        "pool": choice.model_dump(mode="json"),
        "inventory_run": readiness.inventory_run.model_dump(mode="json")
        if readiness.inventory_run
        else None,
        "stock_source_listing_id": str(choice.stock_source_listing_id),
        "stock_snapshot_id": str(source.snapshot_id) if source and source.snapshot_id else None,
        "source_capture_at": source.captured_at.isoformat()
        if source and source.captured_at
        else None,
        "on_hand_base_units": pool.on_hand_base_units,
        "observed_inbound_base_units": pool.observed_inbound_base_units,
        "members": [
            {
                "listing": listings[i].model_dump(
                    mode="json", exclude={"fba_sold_listing_units", "fbm_sold_listing_units"}
                ),
                "snapshot_sha256": policies.digest(
                    audit.snapshot(
                        snapshots[listings[i].seller_sku],
                        exclude=frozenset({"created_at", "updated_at"}),
                    )
                ),
            }
            for i in sorted(choice.listing_ids, key=str)
            if i in listings and listings[i].seller_sku in snapshots
        ],
    }
    failures = {*blocks, *(i.code for i in readiness.issues if i.code not in INBOUND_CODES)}
    if (
        source is None
        or source.snapshot_id is None
        or source.captured_at is None
        or readiness.inventory_run is None
        or pool.on_hand_base_units is None
    ):
        failures.add("INBOUND_STOCK_SOURCE_UNCONFIRMED")
    if choice.channel == "FBA" and pool.observed_inbound_base_units is None:
        failures.add("FBA_INBOUND_UNKNOWN")
    head = InboundRepository(session, actor.organization_id).head(policy_id, choice.pool_id)
    return (
        InboundContext(
            policy_id=policy_id,
            approved_revision_id=approved.id,
            pool_id=choice.pool_id,
            channel=choice.channel,
            evaluated_at=at,
            head_id=head.id if head else None,
            source_context=context,
            source_sha256=policies.digest(context),
            blockers=tuple(sorted(failures)),
        ),
        readiness,
        approved,
    )


def context(
    session: Session, actor: Principal, policy_id: uuid.UUID, payload: InboundContextRequest
) -> InboundContext:
    actor.require_roles(policies.READ)
    with transaction(session):
        policies.validate_actor(PurchasingPolicyRepository(session, actor.organization_id), actor)
        return _context(session, actor, policy_id, payload, datetime.now(UTC))[0]


def _preview(
    session: Session, actor: Principal, policy_id: uuid.UUID, payload: InboundProposal, at: datetime
) -> InboundPreview:
    ctx, ready, approved = _context(
        session,
        actor,
        policy_id,
        InboundContextRequest(
            expected_approved_revision_id=payload.expected_approved_revision_id,
            pool_id=payload.pool_id,
        ),
        at,
    )
    if (
        ctx.head_id != payload.expected_head_id
        or ctx.source_sha256 != payload.expected_source_sha256
    ):
        raise InboundConflict(details={"code": "INBOUND_HEAD_OR_SOURCE_CHANGED"})
    position = payload.position
    pool = next(p for p in ready.pools if p.pool_id == ctx.pool_id)
    captured = (
        datetime.fromisoformat(ctx.source_context["source_capture_at"])
        if ctx.source_context["source_capture_at"]
        else None
    )
    _, _, reasons = qualify_arrivals(
        position,
        captured,
        pool.sales_base_units,
        pool.on_hand_base_units,
        approved.definition.quantity.velocity_window_days,
        approved.definition.quantity.target_days
        + approved.definition.quantity.lead_time_days
        + approved.definition.quantity.safety_days,
        at,
    )
    blocks = {*ctx.blockers, *reasons}
    if (
        ctx.channel == "FBA"
        and position.declared_total_base_units != pool.observed_inbound_base_units
    ):
        blocks.add("FBA_INBOUND_TOTAL_MISMATCH")
    references = {a.reference.casefold() for a in position.arrivals}
    for head in InboundRepository(session, actor.organization_id).heads(policy_id):
        if (
            head.pool_id != ctx.pool_id
            and head.kind == "CONFIRMED"
            and head.approved_revision_id == approved.id
            and str(head.inventory_run_id) == (ctx.source_context["inventory_run"] or {}).get("id")
            and head.expires_at > at
            and references
            & {
                a.reference.casefold()
                for a in InboundPosition.model_validate(head.position).arrivals
            }
        ):
            blocks.add("INBOUND_SHIPMENT_REFERENCED_BY_ANOTHER_POOL")
    digest = policies.digest(
        {"proposal": payload.model_dump(mode="json"), "blockers": sorted(blocks)}
    )
    return InboundPreview(
        context=ctx,
        proposal=payload,
        can_confirm=not blocks,
        blockers=tuple(sorted(blocks)),
        preview_sha256=digest,
    )


def preview(
    session: Session, actor: Principal, policy_id: uuid.UUID, payload: InboundProposal
) -> InboundPreview:
    actor.require_roles(policies.READ)
    with transaction(session):
        policies.validate_actor(PurchasingPolicyRepository(session, actor.organization_id), actor)
        return _preview(session, actor, policy_id, payload, datetime.now(UTC))


def _write(
    session: Session,
    actor: Principal,
    repository: InboundRepository,
    policy_id: uuid.UUID,
    request_id: uuid.UUID,
    fingerprint: str,
    reason: str,
    at: datetime,
    *,
    checked: InboundPreview | None = None,
    revoke: InboundEvidenceRevision | None = None,
) -> InboundRevision:
    if checked:
        ctx = checked.context
        position = checked.proposal.position
        previous = repository.head(policy_id, ctx.pool_id)
        source = ctx.source_context
        row = InboundEvidenceRevision(
            policy_id=policy_id,
            approved_revision_id=ctx.approved_revision_id,
            product_id=uuid.UUID(source["product_id"]),
            marketplace_id=source["marketplace_id"],
            pool_id=ctx.pool_id,
            channel=ctx.channel,
            inventory_run_id=uuid.UUID(source["inventory_run"]["id"]),
            stock_snapshot_id=uuid.UUID(source["stock_snapshot_id"]),
            stock_source_listing_id=uuid.UUID(source["stock_source_listing_id"]),
            source_context=source,
            source_sha256=ctx.source_sha256,
            position=position.model_dump(mode="json"),
            declared_total_base_units=position.declared_total_base_units,
            observed_at=position.observed_at,
            expires_at=position.expires_at,
            kind="CONFIRMED",
        )
    else:
        assert revoke is not None
        previous = revoke
        row = InboundEvidenceRevision(
            **{
                k: getattr(revoke, k)
                for k in (
                    "policy_id",
                    "approved_revision_id",
                    "product_id",
                    "marketplace_id",
                    "pool_id",
                    "channel",
                    "inventory_run_id",
                    "stock_snapshot_id",
                    "stock_source_listing_id",
                    "source_context",
                    "source_sha256",
                    "position",
                    "declared_total_base_units",
                    "observed_at",
                    "expires_at",
                )
            },
            kind="REVOKED",
        )
    row.organization_id = actor.organization_id
    row.revision_number = previous.revision_number + 1 if previous else 1
    row.previous_revision_id = previous.id if previous else None
    row.request_id = request_id
    row.request_sha256 = fingerprint
    row.recorded_by_user_id = actor.user_id
    row.recorded_at = at
    row.reason = reason.strip()
    row.purchasing_eligible = False
    session.add(row)
    session.flush()
    audit.record(
        session,
        organization_id=actor.organization_id,
        actor=actor,
        action="inbound_evidence." + row.kind.lower(),
        entity_type="inbound_evidence_revision",
        entity_id=row.id,
        after={
            "policy_id": str(policy_id),
            "pool_id": row.pool_id,
            "revision_number": row.revision_number,
            "kind": row.kind,
            "source_sha256": row.source_sha256,
            "request_id": str(request_id),
            "reason": row.reason,
        },
    )
    return response(row)


def record(
    session: Session, actor: Principal, policy_id: uuid.UUID, payload: InboundRecordRequest
) -> InboundRevision:
    actor.require_roles(MANAGE)
    repo = InboundRepository(session, actor.organization_id)
    fingerprint = policies.digest(
        {"policy_id": str(policy_id), "action": "CONFIRM", **payload.model_dump(mode="json")}
    )
    with transaction(session):
        repo.lock_request(payload.request_id)
        policies.validate_actor(PurchasingPolicyRepository(session, actor.organization_id), actor)
        existing = repo.by_request(payload.request_id)
        if existing:
            if (
                existing.request_sha256 != fingerprint
                or existing.recorded_by_user_id != actor.user_id
            ):
                raise InboundConflict(details={"code": "REQUEST_ID_REUSED"})
            return response(existing)
        at = datetime.now(UTC)
        checked = _preview(
            session,
            actor,
            policy_id,
            InboundProposal.model_validate(
                payload.model_dump(
                    exclude={
                        "request_id",
                        "expected_preview_sha256",
                        "reason",
                        "acknowledge_review",
                    }
                )
            ),
            at,
        )
        if (
            not payload.acknowledge_review
            or not checked.can_confirm
            or checked.preview_sha256 != payload.expected_preview_sha256
        ):
            raise InboundConflict(
                details={
                    "code": "INBOUND_PREVIEW_CHANGED_OR_UNCONFIRMED",
                    "blockers": checked.blockers,
                }
            )
        return _write(
            session,
            actor,
            repo,
            policy_id,
            payload.request_id,
            fingerprint,
            payload.reason,
            at,
            checked=checked,
        )


def revoke(
    session: Session, actor: Principal, policy_id: uuid.UUID, payload: InboundRevokeRequest
) -> InboundRevision:
    actor.require_roles(MANAGE)
    repo = InboundRepository(session, actor.organization_id)
    pol = PurchasingPolicyRepository(session, actor.organization_id)
    fingerprint = policies.digest(
        {"policy_id": str(policy_id), "action": "REVOKE", **payload.model_dump(mode="json")}
    )
    with transaction(session):
        repo.lock_request(payload.request_id)
        policies.validate_actor(pol, actor)
        existing = repo.by_request(payload.request_id)
        if existing:
            if (
                existing.request_sha256 != fingerprint
                or existing.recorded_by_user_id != actor.user_id
            ):
                raise InboundConflict(details={"code": "REQUEST_ID_REUSED"})
            return response(existing)
        root = pol.root(policy_id)
        if root is None:
            raise InboundNotFound()
        pol.lock(root.product_id, root.vendor_product_id, root.marketplace_id)
        row = repo.get(payload.expected_head_id)
        if row is None or row.policy_id != policy_id:
            raise InboundNotFound()
        head = repo.head(policy_id, row.pool_id)
        if (
            not payload.acknowledge_review
            or head is None
            or head.id != row.id
            or row.kind != "CONFIRMED"
        ):
            raise InboundConflict(details={"code": "INBOUND_HEAD_CHANGED_OR_NOT_CONFIRMED"})
        return _write(
            session,
            actor,
            repo,
            policy_id,
            payload.request_id,
            fingerprint,
            payload.reason,
            datetime.now(UTC),
            revoke=row,
        )


def page(
    session: Session,
    actor: Principal,
    policy_id: uuid.UUID,
    page: int,
    size: int,
    pool_id: str | None = None,
    heads_only: bool = True,
) -> InboundPage:
    actor.require_roles(policies.READ)
    if PurchasingPolicyRepository(session, actor.organization_id).root(policy_id) is None:
        raise InboundNotFound()
    rows, total = InboundRepository(session, actor.organization_id).page(
        policy_id, page, size, pool_id, heads_only
    )
    return InboundPage(
        items=tuple(response(r) for r in rows), page=page, page_size=size, total=total
    )


def apply_selected(
    session: Session,
    actor: Principal,
    approved: PolicyRevisionResponse,
    original: AmazonReadinessResponse,
    selection: InboundSelection,
    at: datetime,
    identity: dict[str, Any],
    policy_blocks: tuple[str, ...],
) -> tuple[ReviewedReadinessResponse, tuple[InboundAssessment, ...]]:
    repository = InboundRepository(session, actor.organization_id)
    assessments = []
    by_pool = {}
    issues = list(original.issues)
    for identifier in selection.revision_ids:
        row = repository.get(identifier)
        if row is None or row.policy_id != approved.policy_id:
            raise InboundNotFound()
        head = repository.head(approved.policy_id, row.pool_id)
        if (
            row.approved_revision_id != approved.id
            or row.kind != "CONFIRMED"
            or head is None
            or head.id != row.id
        ):
            raise InboundConflict(details={"code": "INBOUND_SELECTION_CHANGED"})
        if row.pool_id in by_pool:
            raise InboundConflict(details={"code": "INBOUND_POOL_SELECTED_TWICE"})
        ctx, _, _ = _context(
            session,
            actor,
            approved.policy_id,
            InboundContextRequest(expected_approved_revision_id=approved.id, pool_id=row.pool_id),
            at,
            prepared=(approved, identity, policy_blocks, original),
        )
        pool = next(p for p in original.pools if p.pool_id == row.pool_id)
        captured = (
            datetime.fromisoformat(ctx.source_context["source_capture_at"])
            if ctx.source_context["source_capture_at"]
            else None
        )
        quantity = approved.definition.quantity
        usable, arrivals, reasons = qualify_arrivals(
            InboundPosition.model_validate(row.position),
            captured,
            pool.sales_base_units,
            pool.on_hand_base_units,
            quantity.velocity_window_days,
            quantity.target_days + quantity.lead_time_days + quantity.safety_days,
            at,
        )
        blockers = {*ctx.blockers, *reasons}
        if row.source_sha256 != ctx.source_sha256:
            blockers.add("INBOUND_SOURCE_CONTEXT_CHANGED")
        if (
            row.channel == "FBA"
            and row.declared_total_base_units != pool.observed_inbound_base_units
        ):
            blockers.add("FBA_INBOUND_TOTAL_MISMATCH")
        if blockers:
            usable = None
            arrivals = tuple(
                a.model_copy(update={"disposition": "UNCONFIRMED", "credited_units": None})
                for a in arrivals
            )
            issues.append(ReadinessIssue(code="INBOUND_REVIEW_UNCONFIRMED", pool_id=row.pool_id))
        else:
            issues = [
                i for i in issues if not (i.pool_id == row.pool_id and i.code in INBOUND_CODES)
            ]
        assessment = InboundAssessment(
            revision=response(row),
            status="BLOCKED" if blockers else "APPLIED",
            source_capture_at=captured,
            usable_base_units=usable,
            arrivals=arrivals,
            reasons=tuple(sorted(blockers)),
        )
        assessments.append(assessment)
        by_pool[row.pool_id] = assessment
    pools = []
    for pool in original.pools:
        updated = (
            pool.model_copy(
                update={"usable_inbound_base_units": by_pool[pool.pool_id].usable_base_units}
            )
            if pool.pool_id in by_pool
            else pool
        )
        ready = all(
            v is not None
            for v in (
                updated.sales_base_units,
                updated.on_hand_base_units,
                updated.usable_inbound_base_units,
            )
        ) and not any(
            i.pool_id == pool.pool_id
            or i.listing_id in pool.listing_ids
            or (i.pool_id is None and i.listing_id is None)
            for i in issues
        )
        pools.append(updated.model_copy(update={"quantity_input_ready": ready}))
    data = original.model_dump(mode="json")
    data.update(
        rule_version="amazon-readiness-v2",
        confirmations_persisted=True,
        original_inbound_issues=[
            i.model_dump(mode="json") for i in original.issues if i.code in INBOUND_CODES
        ],
        inbound_revision_ids=[str(i) for i in sorted(selection.revision_ids, key=str)],
        pools=[p.model_dump(mode="json") for p in pools],
        issues=[i.model_dump(mode="json") for i in issues],
        status="DATA_AVAILABLE"
        if pools and not issues and all(p.quantity_input_ready for p in pools)
        else "BLOCKED",
    )
    return ReviewedReadinessResponse.model_validate(data), tuple(
        sorted(assessments, key=lambda a: a.revision.pool_id)
    )
