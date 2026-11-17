"""Audited, immutable purchasing assumptions; no stock or purchase execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.core.security import Principal, RoleCode
from app.db.transaction import transaction
from app.models import Organization, Product, PurchasingPolicyRevision, User, Vendor, VendorProduct
from app.models.enums import ListingStatus, MappingStatus, ProductStatus, VendorStatus
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.schemas.purchasing_policies import (
    Action,
    PolicyCreateRequest,
    PolicyDraftRequest,
    PolicyPage,
    PolicyReviewPreview,
    PolicyReviewRequest,
    PolicyRevisionPage,
    PolicyRevisionResponse,
    PolicySummary,
    PurchasingPolicyDefinition,
)
from app.services import audit

READ = (RoleCode.VIEWER, RoleCode.DATA_OPERATOR, RoleCode.PURCHASING_MANAGER)
WRITE = (RoleCode.DATA_OPERATOR, RoleCode.PURCHASING_MANAGER)
REVIEW = (RoleCode.PURCHASING_MANAGER,)


class PolicyNotFound(NotFoundError):  # noqa: N818
    code = "purchasing_policy_not_found"
    message = "No such policy or linked identity in this organization."


class PolicyConflict(ConflictError):  # noqa: N818
    code = "purchasing_policy_conflict"


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def definition_snapshot(definition: PurchasingPolicyDefinition) -> dict[str, Any]:
    result = definition.model_dump(mode="json")
    amazon = result["amazon"]
    amazon["listing_units"].sort(key=lambda value: value["listing_id"])
    amazon["stock_pools"].sort(key=lambda value: value["pool_id"])
    for pool in amazon["stock_pools"]:
        pool["listing_ids"].sort()
    return result


def _response(row: PurchasingPolicyRevision) -> PolicyRevisionResponse:
    return PolicyRevisionResponse(
        id=row.id,
        policy_id=row.policy_id,
        product_id=row.product_id,
        vendor_product_id=row.vendor_product_id,
        marketplace_id=row.marketplace_id,
        revision_number=row.revision_number,
        supersedes_revision_id=row.supersedes_revision_id,
        reviewed_revision_id=row.reviewed_revision_id,
        kind=row.kind,
        definition=PurchasingPolicyDefinition.model_validate(row.definition_snapshot),
        definition_sha256=row.definition_sha256,
        review_context=row.review_context,
        context_sha256=row.context_sha256,
        recorded_by_user_id=row.recorded_by_user_id,
        recorded_at=row.recorded_at,
        reason=row.reason,
    )


def _root(
    repository: PurchasingPolicyRepository, identifier: uuid.UUID, *, lock: bool = False
) -> PurchasingPolicyRevision:
    root = repository.root(identifier)
    if root is None:
        raise PolicyNotFound()
    if lock:
        repository.lock(root.product_id, root.vendor_product_id, root.marketplace_id)
        root = repository.root(identifier, lock=True)
        assert root is not None
    return root


def _actor(repository: PurchasingPolicyRepository, actor: Principal) -> None:
    user = repository.owned(User, actor.user_id)
    # Organizations are the scope itself, not organization-owned records.
    organization = repository.session.scalar(
        select(Organization)
        .where(Organization.id == actor.organization_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active or organization is None or not organization.is_active:
        raise AuthorizationError("The operator and organization must be active.")


def _identities(
    repository: PurchasingPolicyRepository, product_id: uuid.UUID, vendor_product_id: uuid.UUID
) -> tuple[Product, VendorProduct, Vendor]:
    product = repository.owned(Product, product_id)
    vendor_product = repository.owned(VendorProduct, vendor_product_id)
    vendor = repository.owned(Vendor, vendor_product.vendor_id) if vendor_product else None
    if product is None or vendor_product is None or vendor is None:
        raise PolicyNotFound()
    return product, vendor_product, vendor


def _summary(repository: PurchasingPolicyRepository, identifier: uuid.UUID) -> PolicySummary:
    head = repository.head(identifier)
    decision = repository.decision(identifier, through_revision=head.revision_number)
    return PolicySummary(
        policy_id=identifier,
        head=_response(head),
        approved=_response(decision) if decision and decision.kind == "APPROVED" else None,
    )


def get_policy(session: Session, actor: Principal, identifier: uuid.UUID) -> PolicySummary:
    actor.require_roles(READ)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    _root(repository, identifier)
    return _summary(repository, identifier)


def policy_page(
    session: Session, actor: Principal, page: int, size: int, product_id: uuid.UUID | None = None
) -> PolicyPage:
    actor.require_roles(READ)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    rows, total = repository.page(page, size, product_id=product_id)
    return PolicyPage(
        items=tuple(_summary(repository, row.id) for row in rows),
        page=page,
        page_size=size,
        total=total,
    )


def revision_page(
    session: Session, actor: Principal, identifier: uuid.UUID, page: int, size: int
) -> PolicyRevisionPage:
    actor.require_roles(READ)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    _root(repository, identifier)
    rows, total = repository.page(page, size, policy_id=identifier)
    return PolicyRevisionPage(
        items=tuple(_response(row) for row in rows), page=page, page_size=size, total=total
    )


def _record(
    repository: PurchasingPolicyRepository,
    actor: Principal,
    *,
    head: PurchasingPolicyRevision | None,
    definition: dict[str, Any],
    vendor_product_id: uuid.UUID,
    kind: Literal["DRAFT", "APPROVED", "REVOKED"],
    reason: str,
    preview: PolicyReviewPreview | None = None,
) -> PurchasingPolicyRevision:
    identifier = uuid.uuid4()
    row = PurchasingPolicyRevision(
        id=identifier,
        policy_id=head.policy_id if head else identifier,
        organization_id=actor.organization_id,
        product_id=uuid.UUID(definition["amazon"]["product_id"]),
        vendor_product_id=vendor_product_id,
        marketplace_id=definition["amazon"]["marketplace_id"],
        revision_number=head.revision_number + 1 if head else 1,
        supersedes_revision_id=head.id if head else None,
        reviewed_revision_id=preview.reviewed_revision_id if preview else None,
        kind=kind,
        definition_snapshot=definition,
        definition_sha256=digest(definition),
        review_context=preview.review_context if preview else None,
        context_sha256=preview.context_sha256 if preview else None,
        recorded_by_user_id=actor.user_id,
        recorded_at=datetime.now(UTC),
        reason=reason,
        purchasing_eligible=False,
    )
    repository.session.add(row)
    repository.session.flush()
    audit.record(
        repository.session,
        organization_id=actor.organization_id,
        actor=actor,
        action=f"purchasing_policy.{kind.lower()}",
        entity_type="purchasing_policy_revision",
        entity_id=row.id,
        before={"head_id": str(head.id)} if head else None,
        after={
            "policy_id": str(row.policy_id),
            "revision_number": row.revision_number,
            "kind": row.kind,
            "definition_sha256": row.definition_sha256,
            "context_sha256": row.context_sha256,
            "reason": row.reason,
            "reviewed_revision_id": str(row.reviewed_revision_id) if preview else None,
        },
    )
    return row


def create_policy(
    session: Session, actor: Principal, payload: PolicyCreateRequest
) -> PolicySummary:
    actor.require_roles(WRITE)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    try:
        with transaction(session):
            amazon = payload.definition.amazon
            repository.lock(amazon.product_id, payload.vendor_product_id, amazon.marketplace_id)
            _actor(repository, actor)
            _identities(repository, amazon.product_id, payload.vendor_product_id)
            row = _record(
                repository,
                actor,
                head=None,
                definition=definition_snapshot(payload.definition),
                vendor_product_id=payload.vendor_product_id,
                kind="DRAFT",
                reason=payload.reason,
            )
            result = _summary(repository, row.policy_id)
    except IntegrityError:
        raise PolicyConflict(details={"code": "POLICY_ALREADY_EXISTS_OR_CHANGED"}) from None
    return result


def record_draft(
    session: Session, actor: Principal, identifier: uuid.UUID, payload: PolicyDraftRequest
) -> PolicySummary:
    actor.require_roles(WRITE)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    try:
        with transaction(session):
            root = _root(repository, identifier, lock=True)
            _actor(repository, actor)
            head = repository.head(identifier)
            if payload.expected_head_id != head.id:
                raise PolicyConflict(details={"code": "POLICY_HEAD_CHANGED"})
            definition = definition_snapshot(payload.definition)
            if (
                payload.definition.amazon.product_id != root.product_id
                or payload.definition.amazon.marketplace_id != root.marketplace_id
            ):
                raise PolicyConflict(details={"code": "POLICY_IDENTITY_CANNOT_CHANGE"})
            if head.kind != "REVOKED" and digest(definition) == head.definition_sha256:
                raise PolicyConflict(details={"code": "POLICY_NO_CHANGES"})
            _identities(repository, root.product_id, root.vendor_product_id)
            _record(
                repository,
                actor,
                head=head,
                definition=definition,
                vendor_product_id=root.vendor_product_id,
                kind="DRAFT",
                reason=payload.reason,
            )
            result = _summary(repository, identifier)
    except IntegrityError:
        raise PolicyConflict(details={"code": "POLICY_CHANGED"}) from None
    return result


def _reference(value: str | None) -> bool:
    return value is not None and len(value.strip()) >= 10


def _review(
    repository: PurchasingPolicyRepository, root: PurchasingPolicyRevision, action: Action
) -> PolicyReviewPreview:
    head = repository.head(root.id)
    decision = repository.decision(root.id)
    target = head if action == "APPROVE" else decision
    if target is None or (action == "REVOKE" and target.kind != "APPROVED"):
        raise PolicyConflict(details={"code": "NO_APPROVED_POLICY"})
    definition = PurchasingPolicyDefinition.model_validate(target.definition_snapshot)
    product, vendor_product, vendor = _identities(
        repository, root.product_id, root.vendor_product_id
    )
    listings = repository.listings(root.product_id, root.marketplace_id)
    approvers = repository.approval_owners(
        {
            identifier
            for identifier in (
                vendor_product.mapping_approved_by_user_id,
                *(listing.approved_by_user_id for listing in listings),
            )
            if identifier is not None
        }
    )
    context = {
        "version": "purchasing-policy-review-v1",
        "policy_id": str(root.id),
        "action": action,
        "head_id": str(head.id),
        "reviewed_revision_id": str(target.id),
        "decision_id": str(decision.id) if decision else None,
        "definition_sha256": target.definition_sha256,
        "product": audit.snapshot(product, exclude=frozenset({"raw"})),
        "vendor_product": audit.snapshot(vendor_product, exclude=frozenset({"raw"})),
        "vendor": {
            "id": str(vendor.id),
            "is_active": vendor.is_active,
            "status": vendor.status.value,
        },
        "listings": [audit.snapshot(row, exclude=frozenset({"raw"})) for row in listings],
        "mapping_approver_ids": sorted(str(identifier) for identifier in approvers),
    }
    blocked: set[str] = set()
    if action == "APPROVE":
        if head.kind != "DRAFT":
            blocked.add("CURRENT_HEAD_NOT_DRAFT")
        if not product.is_active or product.status != ProductStatus.ACTIVE:
            blocked.add("PRODUCT_INACTIVE")
        if not vendor.is_active or vendor.status != VendorStatus.ACTIVE:
            blocked.add("VENDOR_INACTIVE")
        if (
            vendor_product.product_id != root.product_id
            or not vendor_product.is_active
            or vendor_product.mapping_status != MappingStatus.APPROVED
            or not vendor_product.mapping_method
            or not vendor_product.mapping_approved_by_user_id
            or vendor_product.mapping_approved_by_user_id not in approvers
            or not vendor_product.mapping_approved_at
            or vendor_product.mapping_approved_at > datetime.now(UTC)
        ):
            blocked.add("VENDOR_MAPPING_UNCONFIRMED")
        if not listings or len(listings) > 200:
            blocked.add("LISTING_CONFIGURATION_UNPROVEN")
        choices = {choice.listing_id: choice for choice in definition.amazon.listing_units}
        known = {row.id for row in listings}
        if set(choices) != known:
            blocked.add("LISTING_CONFIGURATION_INCOMPLETE")
        for listing in listings:
            if (
                not listing.is_active
                or listing.listing_status != ListingStatus.ACTIVE
                or listing.mapping_status != MappingStatus.APPROVED
                or not listing.asin
                or not listing.mapping_method
                or not listing.approved_by_user_id
                or listing.approved_by_user_id not in approvers
                or not listing.approved_at
                or listing.approved_at > datetime.now(UTC)
            ):
                blocked.add("AMAZON_MAPPING_UNCONFIRMED")
        if any(
            choice.base_units_per_listing_unit is None
            or not _reference(choice.confirmation_reference)
            for choice in choices.values()
        ):
            blocked.add("UNIT_CONVERSION_UNCONFIRMED")
        pools = definition.amazon.stock_pools
        if not pools or any(not _reference(pool.distinct_stock_reference) for pool in pools):
            blocked.add("STOCK_POOLS_UNCONFIRMED")
        if {identifier for pool in pools for identifier in pool.listing_ids} != known:
            blocked.add("STOCK_POOL_MEMBERSHIP_INCOMPLETE")
        if not _reference(definition.coverage_confirmation_reference):
            blocked.add("COVERAGE_INTERPRETATION_UNCONFIRMED")
        factor = definition.vendor_base_units_per_order_unit
        if factor is None or not _reference(definition.vendor_unit_confirmation_reference):
            blocked.add("VENDOR_UNITS_UNCONFIRMED")
        elif definition.quantity.order_multiple_units % factor:
            blocked.add("ORDER_MULTIPLE_CONFLICTS_WITH_VENDOR_UNITS")
    return PolicyReviewPreview(
        action=action,
        policy_id=root.id,
        expected_head_id=head.id,
        reviewed_revision_id=target.id,
        definition=definition,
        definition_sha256=target.definition_sha256,
        review_context=context,
        context_sha256=digest(context),
        blocking_reasons=tuple(sorted(blocked)),
        review_allowed=not blocked,
    )


def preview_review(
    session: Session, actor: Principal, identifier: uuid.UUID, action: Action
) -> PolicyReviewPreview:
    actor.require_roles(REVIEW)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    root = _root(repository, identifier, lock=True)
    _actor(repository, actor)
    return _review(repository, root, action)


def record_review(
    session: Session, actor: Principal, identifier: uuid.UUID, payload: PolicyReviewRequest
) -> PolicySummary:
    actor.require_roles(REVIEW)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    try:
        with transaction(session):
            root = _root(repository, identifier, lock=True)
            _actor(repository, actor)
            preview = _review(repository, root, payload.action)
            if (
                payload.expected_head_id != preview.expected_head_id
                or payload.reviewed_revision_id != preview.reviewed_revision_id
                or payload.definition_sha256 != preview.definition_sha256
                or payload.context_sha256 != preview.context_sha256
            ):
                raise PolicyConflict(details={"code": "POLICY_REVIEW_CHANGED"})
            if not preview.review_allowed:
                raise PolicyConflict(
                    details={
                        "code": "POLICY_REVIEW_BLOCKED",
                        "reasons": list(preview.blocking_reasons),
                    }
                )
            _record(
                repository,
                actor,
                head=repository.head(identifier),
                definition=definition_snapshot(preview.definition),
                vendor_product_id=root.vendor_product_id,
                kind="APPROVED" if payload.action == "APPROVE" else "REVOKED",
                reason=payload.reason,
                preview=preview,
            )
            result = _summary(repository, identifier)
    except IntegrityError:
        raise PolicyConflict(details={"code": "POLICY_CHANGED"}) from None
    return result
