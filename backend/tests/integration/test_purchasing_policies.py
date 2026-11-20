"""Policy ledger, immutable review history, isolation, stale reviews and atomic audit."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.core.context import reset_request_id, set_request_id
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuditEvent,
    MarketplaceListing,
    Organization,
    Product,
    PurchasingPolicyRevision,
    Vendor,
    VendorProduct,
)
from app.models.enums import ListingStatus, MappingStatus, MatchMethod
from app.schemas.purchasing_policies import (
    PolicyCreateRequest,
    PolicyDraftRequest,
    PolicyReviewPreview,
    PolicyReviewRequest,
    PolicySummary,
    PurchasingPolicyDefinition,
)
from app.services import purchasing_policies as service
from tests.integration import factories
from tests.unit.test_purchasing_policies import definition

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC) - timedelta(minutes=5)


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    token = set_request_id(request_id)
    try:
        yield
    finally:
        reset_request_id(token)


@dataclass
class Context:
    org: Organization
    operator: Principal
    manager: Principal
    product: Product
    vendor: Vendor
    vendor_product: VendorProduct
    listing: MarketplaceListing
    payload: PolicyCreateRequest


def setup(session: Session) -> Context:
    org = factories.make_organization(session)
    user = factories.make_user(session, org)
    operator = Principal(
        user.id, org.id, user.email, user.display_name, frozenset({RoleCode.DATA_OPERATOR})
    )
    manager = Principal(
        user.id, org.id, user.email, user.display_name, frozenset({RoleCode.PURCHASING_MANAGER})
    )
    product = factories.make_product(session, org)
    vendor = factories.make_vendor(session, org)
    vendor_product = factories.make_vendor_product(
        session,
        org,
        vendor,
        product_id=product.id,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        mapping_approved_by_user_id=user.id,
        mapping_approved_at=NOW,
    )
    listing = factories.make_marketplace_listing(
        session,
        org,
        product,
        asin="B000000001",
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=user.id,
        approved_at=NOW,
    )
    payload = PolicyCreateRequest(
        vendor_product_id=vendor_product.id,
        definition=PurchasingPolicyDefinition.model_validate(definition(product.id, listing.id)),
        reason="Synthetic manager policy proposed for review",
    )
    session.flush()
    return Context(org, operator, manager, product, vendor, vendor_product, listing, payload)


@pytest.fixture
def context(db_session: Session) -> Context:
    return setup(db_session)


def draft(
    session: Session, context: Context, current: PolicySummary, target: int = 21
) -> PolicySummary:
    data = current.head.definition.model_dump(mode="json")
    data["quantity"]["target_days"] = target
    return service.record_draft(
        session,
        context.operator,
        current.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(data),
            reason="Synthetic coverage policy revised for review",
        ),
    )


def request(preview: PolicyReviewPreview, **changes: Any) -> PolicyReviewRequest:
    return PolicyReviewRequest.model_validate(
        {
            "action": preview.action,
            "expected_head_id": preview.expected_head_id,
            "reviewed_revision_id": preview.reviewed_revision_id,
            "definition_sha256": preview.definition_sha256,
            "context_sha256": preview.context_sha256,
            "reason": "Synthetic manager reviewed the complete definition",
            "acknowledge_review": True,
            **changes,
        }
    )


def approve(session: Session, context: Context, current: PolicySummary) -> PolicySummary:
    preview = service.preview_review(session, context.manager, current.policy_id, "APPROVE")
    assert preview.review_allowed, preview.blocking_reasons
    return service.record_review(session, context.manager, current.policy_id, request(preview))


def count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(PurchasingPolicyRevision)) or 0


def test_complete_draft_review_replace_and_revoke_history(
    db_session: Session, context: Context
) -> None:
    with request_context(request_id="policy-history-fixture"):
        first = service.create_policy(db_session, context.operator, context.payload)
        assert (
            first.head.kind == "DRAFT"
            and first.head.revision_number == 1
            and first.approved is None
        )
        approved = approve(db_session, context, first)
        assert approved.approved is not None and approved.approved.id == approved.head.id
        assert approved.head.reviewed_revision_id == first.head.id
        changed = draft(db_session, context, approved)
        assert changed.head.kind == "DRAFT" and changed.approved is not None
        assert changed.approved.id == approved.head.id  # drafting never revokes the active approval
        replacement = approve(db_session, context, changed)
        assert replacement.approved is not None and replacement.approved.id == replacement.head.id
        pending = draft(db_session, context, replacement, 28)
        preview = service.preview_review(db_session, context.manager, first.policy_id, "REVOKE")
        assert preview.reviewed_revision_id == replacement.head.id
        assert preview.expected_head_id == pending.head.id
        revoked = service.record_review(
            db_session, context.manager, first.policy_id, request(preview)
        )
    assert revoked.approved is None and revoked.head.kind == "REVOKED"
    assert revoked.head.definition == replacement.head.definition
    assert not revoked.purchasing_eligible and not revoked.current_data_verified
    history = service.revision_page(db_session, context.manager, first.policy_id, 1, 100)
    assert [row.revision_number for row in history.items] == [6, 5, 4, 3, 2, 1]
    assert history.items[-1].definition == first.head.definition
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "purchasing_policy_revision")
            .order_by(AuditEvent.created_at)
        )
    )
    assert len(events) == 6
    assert all(event.actor_user_id == context.operator.user_id for event in events)
    assert all(event.request_id == "policy-history-fixture" for event in events)
    assert all(not row.purchasing_eligible for row in history.items)


def test_revoked_policy_can_be_resubmitted_unchanged(db_session: Session, context: Context) -> None:
    first = approve(
        db_session, context, service.create_policy(db_session, context.operator, context.payload)
    )
    preview = service.preview_review(db_session, context.manager, first.policy_id, "REVOKE")
    revoked = service.record_review(db_session, context.manager, first.policy_id, request(preview))
    new = service.record_draft(
        db_session,
        context.operator,
        first.policy_id,
        PolicyDraftRequest(
            expected_head_id=revoked.head.id,
            definition=revoked.head.definition,
            reason="Synthetic policy resubmitted after revocation",
        ),
    )
    assert approve(db_session, context, new).approved is not None


@pytest.mark.parametrize(
    "fault,code",
    [
        ("coverage", "COVERAGE_INTERPRETATION_UNCONFIRMED"),
        ("units", "UNIT_CONVERSION_UNCONFIRMED"),
        ("vendor_units", "VENDOR_UNITS_UNCONFIRMED"),
        ("multiple", "ORDER_MULTIPLE_CONFLICTS_WITH_VENDOR_UNITS"),
        ("pools", "STOCK_POOLS_UNCONFIRMED"),
        ("pool_members", "STOCK_POOL_MEMBERSHIP_INCOMPLETE"),
        ("listing_set", "LISTING_CONFIGURATION_INCOMPLETE"),
        ("amazon_mapping", "AMAZON_MAPPING_UNCONFIRMED"),
        ("vendor_mapping", "VENDOR_MAPPING_UNCONFIRMED"),
        ("foreign_approver", "AMAZON_MAPPING_UNCONFIRMED"),
        ("product", "PRODUCT_INACTIVE"),
        ("vendor", "VENDOR_INACTIVE"),
    ],
)
def test_unknown_drafts_are_retained_but_cannot_be_approved(
    db_session: Session,
    context: Context,
    fault: str,
    code: str,
) -> None:
    data = context.payload.definition.model_dump(mode="json")
    if fault == "coverage":
        data["coverage_confirmation_reference"] = None
    elif fault == "units":
        data["amazon"]["listing_units"][0]["base_units_per_listing_unit"] = None
    elif fault == "vendor_units":
        data["vendor_base_units_per_order_unit"] = None
    elif fault == "multiple":
        data["quantity"]["order_multiple_units"] = 5
    elif fault == "pools":
        data["amazon"]["stock_pools"][0]["distinct_stock_reference"] = None
    elif fault == "pool_members":
        foreign = str(uuid.uuid4())
        data["amazon"]["stock_pools"][0].update(
            listing_ids=[foreign], stock_source_listing_id=foreign
        )
    elif fault == "listing_set":
        data["amazon"]["listing_units"] = []
    elif fault == "amazon_mapping":
        context.listing.mapping_status = MappingStatus.PENDING
    elif fault == "vendor_mapping":
        context.vendor_product.mapping_status = MappingStatus.PENDING
    elif fault == "foreign_approver":
        context.listing.approved_by_user_id = setup(db_session).operator.user_id
    elif fault == "product":
        context.product.is_active = False
    else:
        context.vendor.is_active = False
    payload = PolicyCreateRequest(
        vendor_product_id=context.vendor_product.id,
        definition=PurchasingPolicyDefinition.model_validate(data),
        reason=context.payload.reason,
    )
    first = service.create_policy(db_session, context.operator, payload)
    preview = service.preview_review(db_session, context.manager, first.policy_id, "APPROVE")
    assert not preview.review_allowed and code in preview.blocking_reasons
    with pytest.raises(service.PolicyConflict):
        service.record_review(db_session, context.manager, first.policy_id, request(preview))
    assert service.get_policy(db_session, context.manager, first.policy_id).approved is None


@pytest.mark.parametrize(
    "fault", ["head", "definition", "context", "target", "mapping", "new_listing"]
)
def test_changed_review_cannot_be_recorded(
    db_session: Session, context: Context, fault: str
) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    preview = service.preview_review(db_session, context.manager, first.policy_id, "APPROVE")
    changes: dict[str, Any] = {}
    if fault == "head":
        draft(db_session, context, first)
    elif fault == "definition":
        changes["definition_sha256"] = "0" * 64
    elif fault == "context":
        changes["context_sha256"] = "0" * 64
    elif fault == "target":
        changes["reviewed_revision_id"] = uuid.uuid4()
    elif fault == "mapping":
        context.listing.asin = "B000CHANGED"
        db_session.commit()
    else:
        factories.make_marketplace_listing(db_session, context.org, context.product)
        db_session.commit()
    with pytest.raises(service.PolicyConflict):
        service.record_review(
            db_session, context.manager, first.policy_id, request(preview, **changes)
        )
    assert service.get_policy(db_session, context.manager, first.policy_id).approved is None


def test_duplicate_noop_identity_change_and_stale_draft_rejected(
    db_session: Session, context: Context
) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    with pytest.raises(service.PolicyConflict):
        service.create_policy(db_session, context.operator, context.payload)
    with pytest.raises(service.PolicyConflict):
        service.record_draft(
            db_session,
            context.operator,
            first.policy_id,
            PolicyDraftRequest(
                expected_head_id=first.head.id,
                definition=first.head.definition,
                reason="Synthetic unchanged policy is a no-op",
            ),
        )
    data = first.head.definition.model_dump(mode="json")
    data["amazon"]["product_id"] = str(uuid.uuid4())
    with pytest.raises(service.PolicyConflict):
        service.record_draft(
            db_session,
            context.operator,
            first.policy_id,
            PolicyDraftRequest(
                expected_head_id=first.head.id,
                definition=PurchasingPolicyDefinition.model_validate(data),
                reason="Synthetic identity cannot change",
            ),
        )
    draft(db_session, context, first)
    with pytest.raises(service.PolicyConflict):
        draft(db_session, context, first, 28)


def test_revoke_remains_available_for_inactive_mapping(
    db_session: Session, context: Context
) -> None:
    first = approve(
        db_session, context, service.create_policy(db_session, context.operator, context.payload)
    )
    context.product.is_active = False
    context.vendor.is_active = False
    context.vendor_product.mapping_status = MappingStatus.PENDING
    context.listing.is_active = False
    db_session.commit()
    preview = service.preview_review(db_session, context.manager, first.policy_id, "REVOKE")
    assert preview.review_allowed
    assert (
        service.record_review(
            db_session, context.manager, first.policy_id, request(preview)
        ).approved
        is None
    )
    with pytest.raises(service.PolicyConflict):
        service.preview_review(db_session, context.manager, first.policy_id, "REVOKE")


def test_audit_failure_rolls_back_the_complete_revision(
    db_session: Session,
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    preview = service.preview_review(db_session, context.manager, first.policy_id, "APPROVE")
    before = count(db_session)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.purchasing_policies.audit.record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        service.record_review(db_session, context.manager, first.policy_id, request(preview))
    assert count(db_session) == before
    assert service.get_policy(db_session, context.manager, first.policy_id).head.id == first.head.id


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_postgres_refuses_history_mutation(
    db_session: Session, context: Context, operation: str
) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    statement = (
        update(PurchasingPolicyRevision).values(reason="Tampered historical reason")
        if operation == "update"
        else delete(PurchasingPolicyRevision)
    )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(statement.where(PurchasingPolicyRevision.id == first.head.id))
    assert (
        service.get_policy(db_session, context.manager, first.policy_id).head.reason
        == first.head.reason
    )


@pytest.mark.parametrize(
    "fault",
    [
        "actor",
        "product",
        "vendor_product",
        "root",
        "sequence",
        "approval_copy",
        "review_context",
        "purchasing_flag",
    ],
)
def test_postgres_guard_rejects_inconsistent_rows(
    db_session: Session, context: Context, fault: str
) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    original = db_session.get(PurchasingPolicyRevision, first.head.id)
    assert original is not None
    values = {
        column.name: getattr(original, column.name)
        for column in original.__table__.columns
        if column.name not in {"created_at", "updated_at"}
    }
    values.update(
        id=uuid.uuid4(),
        revision_number=2,
        supersedes_revision_id=first.head.id,
        recorded_at=datetime.now(UTC),
    )
    if fault in {"actor", "product", "vendor_product"}:
        other = setup(db_session)
        key = {
            "actor": "recorded_by_user_id",
            "product": "product_id",
            "vendor_product": "vendor_product_id",
        }[fault]
        values[key] = {
            "actor": other.operator.user_id,
            "product": other.product.id,
            "vendor_product": other.vendor_product.id,
        }[fault]
        db_session.flush()
    elif fault == "root":
        values["policy_id"] = uuid.uuid4()
    elif fault == "sequence":
        values["revision_number"] = 4
    elif fault in {"approval_copy", "review_context"}:
        preview = service.preview_review(db_session, context.manager, first.policy_id, "APPROVE")
        values.update(
            kind="APPROVED",
            reviewed_revision_id=first.head.id,
            review_context=preview.review_context,
            context_sha256=preview.context_sha256,
        )
        if fault == "approval_copy":
            values["definition_sha256"] = "0" * 64
        else:
            values["review_context"] = {**preview.review_context, "head_id": str(uuid.uuid4())}
    else:
        values["purchasing_eligible"] = True
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(PurchasingPolicyRevision(**values))
        db_session.flush()


def test_scope_roles_pagination_and_api(db_session: Session, context: Context) -> None:
    first = service.create_policy(db_session, context.operator, context.payload)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: context.operator
    base = "/api/v1/purchasing-policies"
    with TestClient(app) as client:
        assert (
            client.post(base + f"/{first.policy_id}/review-preview?action=APPROVE").status_code
            == 403
        )
        app.dependency_overrides[get_current_principal] = lambda: context.manager
        preview = client.post(base + f"/{first.policy_id}/review-preview?action=APPROVE")
        assert preview.status_code == 200, preview.text
        data = PolicyReviewPreview.model_validate(preview.json())
        recorded = client.post(
            base + f"/{first.policy_id}/reviews", json=request(data).model_dump(mode="json")
        )
        assert recorded.status_code == 201, recorded.text
        assert (
            client.post(
                base + f"/{first.policy_id}/reviews", json=request(data).model_dump(mode="json")
            ).status_code
            == 409
        )
        assert client.get(base + "?page_size=1").json()["total"] == 1
        assert (
            len(client.get(base + f"/{first.policy_id}/revisions?page_size=1").json()["items"]) == 1
        )
        other = setup(db_session)
        db_session.commit()
        app.dependency_overrides[get_current_principal] = lambda: other.manager
        assert client.get(base + f"/{first.policy_id}").status_code == 404
        assert client.get(base).json()["total"] == 0
        assert client.post(base, json=context.payload.model_dump(mode="json")).status_code == 404


def test_inactive_actor_cannot_mutate(db_session: Session, context: Context) -> None:
    from app.models import User

    user = db_session.get(User, context.operator.user_id)
    assert user is not None
    user.is_active = False
    db_session.commit()
    with pytest.raises(AuthorizationError):
        service.create_policy(db_session, context.operator, context.payload)


@pytest.fixture
def isolated_engine(migrated_engine: Engine) -> Iterator[Engine]:
    # Independent connections need committed fixtures in a disposable database.
    from tests.integration.conftest import create_database, drop_database, run_migrations

    url = migrated_engine.url.set(database=f"policy_{uuid.uuid4().hex}_test")
    create_database(url)
    run_migrations(url)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        drop_database(url)


def test_concurrent_approvals_have_one_successor(isolated_engine: Engine) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        context = setup(session)
        first = service.create_policy(session, context.operator, context.payload)
        preview = service.preview_review(session, context.manager, first.policy_id, "APPROVE")
        session.commit()  # release preview locks before independent writers

    def review(_: int) -> str:
        with Session(isolated_engine) as session:
            try:
                service.record_review(session, context.manager, first.policy_id, request(preview))
                return "recorded"
            except service.PolicyConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(review, [1, 2])) == ["conflict", "recorded"]
    with Session(isolated_engine) as session:
        assert count(session) == 2


def test_downgrade_refuses_recorded_policy_history(isolated_engine: Engine) -> None:
    from tests.integration.conftest import downgrade_migrations

    with Session(isolated_engine) as session:
        context = setup(session)
        service.create_policy(session, context.operator, context.payload)
    with pytest.raises(DBAPIError, match="Cannot discard recorded purchasing policy history"):
        downgrade_migrations(isolated_engine.url, "a61117202603")
