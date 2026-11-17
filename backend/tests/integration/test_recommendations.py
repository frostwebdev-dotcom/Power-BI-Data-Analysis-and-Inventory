"""Saved quantity evidence, policy/source concurrency and immutable tenant history."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AmazonInventorySnapshot,
    AmazonOrderLine,
    AmazonSyncRun,
    AuditEvent,
    ReplenishmentRecommendation,
)
from app.models.enums import (
    AmazonSyncJobType,
    ListingStatus,
    MappingStatus,
    MatchMethod,
    SyncStatus,
)
from app.schemas.purchasing_policies import PolicyDraftRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import (
    RecommendationPreview,
    RecommendationPreviewRequest,
    RecommendationRecordRequest,
    RecommendationResponse,
)
from app.services import purchasing_policies as policies
from app.services import recommendations as service
from app.services.replenishment import calculate_replenishment
from tests.integration import factories
from tests.integration import test_purchasing_policies as policy_fixtures
from tests.integration.test_amazon_readiness import contract, evidence

pytestmark = pytest.mark.integration


@dataclass
class Context:
    policy: policy_fixtures.Context
    policy_id: uuid.UUID
    approval: uuid.UUID
    at: datetime
    stock: AmazonInventorySnapshot
    line: AmazonOrderLine


def setup(session: Session) -> Context:
    ctx = policy_fixtures.setup(session)
    current = policy_fixtures.approve(
        session, ctx, policies.create_policy(session, ctx.operator, ctx.payload)
    )
    at = datetime.now(UTC)
    end = at - timedelta(minutes=3)
    orders = factories.make_amazon_sync_run(
        session,
        ctx.org,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.COMPLETED,
        started_at=at - timedelta(minutes=2),
        completed_at=at - timedelta(minutes=1),
        window_start=end - timedelta(days=14),
        window_end=end,
        error_details=contract(),
    )
    inventory = factories.make_amazon_sync_run(
        session,
        ctx.org,
        job_type=AmazonSyncJobType.FBA_INVENTORY,
        status=SyncStatus.COMPLETED,
        started_at=at - timedelta(seconds=40),
        completed_at=at - timedelta(seconds=30),
    )
    stock = factories.make_amazon_inventory_snapshot(
        session,
        ctx.org,
        inventory,
        seller_sku=ctx.listing.seller_sku,
        asin=ctx.listing.asin,
        captured_at=inventory.started_at,
        fnsku="SYNTHETIC-FNSKU",
        condition="NewItem",
        fulfillable=10,
        raw={
            "source": "fba_inventory",
            "seller_sku": ctx.listing.seller_sku,
            "asin": ctx.listing.asin,
            "fulfillable": 10,
            "inbound_working": 0,
            "inbound_shipped": 0,
            "inbound_receiving": 0,
        },
    )
    line = factories.make_amazon_order_line(
        session,
        ctx.org,
        orders,
        seller_sku=ctx.listing.seller_sku,
        asin=ctx.listing.asin,
        quantity_ordered=14,
        purchase_date=end - timedelta(days=1),
        last_updated_at=orders.started_at,
        item_status="Shipped",
        order_status="Shipped",
        fulfillment_channel="Amazon",
        sales_channel="Amazon.com",
        raw=evidence(orders),
    )
    session.commit()
    return Context(ctx, current.policy_id, current.head.id, at, stock, line)


@pytest.fixture
def context(db_session: Session) -> Context:
    return setup(db_session)


def preview(session: Session, ctx: Context, **changes: Any) -> RecommendationPreview:
    return service.preview_recommendation(
        session,
        ctx.policy.operator,
        ctx.policy_id,
        RecommendationPreviewRequest(expected_approved_revision_id=ctx.approval),
        now=lambda: changes.get("at", ctx.at),
    )


def request(result: RecommendationPreview, **changes: Any) -> RecommendationRecordRequest:
    return RecommendationRecordRequest.model_validate(
        {
            "request_id": str(uuid.uuid4()),
            "expected_approved_revision_id": result.snapshot.approved_policy.id,
            "expected_evidence_sha256": result.evidence_sha256,
            "reason": "Synthetic reviewed source inputs and quantity calculation",
            "acknowledge_review": True,
            **changes,
        }
    )


def record(
    session: Session, ctx: Context, payload: RecommendationRecordRequest
) -> RecommendationResponse:
    return service.record_recommendation(
        session, ctx.policy.operator, ctx.policy_id, payload, now=lambda: ctx.at
    )


def count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ReplenishmentRecommendation)) or 0


def test_real_sources_are_saved_replayable_and_not_orders(
    db_session: Session, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Evaluation must not construct an Amazon client")

    monkeypatch.setattr("app.services.amazon_orders.AmazonClient", forbidden)
    before = count(db_session)
    result = preview(db_session, context)
    assert count(db_session) == before
    [pool] = result.snapshot.pools
    assert result.snapshot.readiness.status == "DATA_AVAILABLE"
    assert (pool.inputs.sales_units, pool.inputs.on_hand_units, pool.inputs.inbound_units) == (
        84,
        60,
        0,
    )
    assert pool.result.calculation.required_units == 102
    assert pool.result.calculation.net_shortfall_units == 42
    assert pool.result.calculation.order_quantity_before_vendor_cap == 42
    assert pool.result.suggested_units is None and result.snapshot.status == "BLOCKED"
    payload = request(result)
    with policy_fixtures.request_context("recommendation-fixture"):
        saved = record(db_session, context, payload)
    assert saved.snapshot_sha256 == policies.digest(saved.snapshot.model_dump(mode="json"))
    assert saved.evidence_sha256 == service.evidence_digest(saved.snapshot)
    assert saved.snapshot.pools[0].result == calculate_replenishment(saved.snapshot.pools[0].inputs)
    assert saved.historical and not saved.purchasing_eligible
    assert saved.snapshot.readiness.listings[0].snapshot_id == context.stock.id
    assert "amazon_order_id" not in saved.model_dump_json()
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == saved.id)))
    assert len(events) == 1 and events[0].request_id == "recommendation-fixture"
    assert events[0].action == "replenishment_recommendation.recorded"
    context.stock.fulfillable = 0
    context.stock.raw = {**context.stock.raw, "fulfillable": 0}
    db_session.commit()
    assert service.get_recommendation(db_session, context.policy.manager, saved.id) == saved
    assert record(db_session, context, payload).id == saved.id  # exact retry, original observation
    assert count(db_session) == before + 1


def test_new_draft_keeps_using_current_approval(db_session: Session, context: Context) -> None:
    first = preview(db_session, context)
    current = policies.get_policy(db_session, context.policy.operator, context.policy_id)
    policy_fixtures.draft(db_session, context.policy, current, 28)
    second = preview(db_session, context)
    assert first.evidence_sha256 == second.evidence_sha256
    assert second.snapshot.approved_policy.id == context.approval
    assert record(db_session, context, request(first)).approved_revision_id == context.approval


def test_unapproved_draft_has_no_calculation(db_session: Session) -> None:
    ctx = policy_fixtures.setup(db_session)
    current = policies.create_policy(db_session, ctx.operator, ctx.payload)
    with pytest.raises(policies.PolicyConflict):
        service.preview_recommendation(
            db_session,
            ctx.operator,
            current.policy_id,
            RecommendationPreviewRequest(expected_approved_revision_id=current.head.id),
        )


@pytest.mark.parametrize("channel", ["FBA", "FBM"])
def test_shared_alias_stock_is_not_summed_in_saved_calculation(
    db_session: Session, context: Context, channel: str
) -> None:
    ctx = context.policy
    listing = factories.make_marketplace_listing(
        db_session,
        ctx.org,
        ctx.product,
        asin=ctx.listing.asin,
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=ctx.operator.user_id,
        approved_at=ctx.listing.approved_at,
    )
    run = db_session.get(AmazonSyncRun, context.stock.sync_run_id)
    assert run is not None
    position = factories.make_amazon_inventory_snapshot(
        db_session,
        ctx.org,
        run,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        captured_at=context.stock.captured_at,
        fnsku=context.stock.fnsku,
        condition=context.stock.condition,
        fulfillable=10,
        raw={**context.stock.raw, "seller_sku": listing.seller_sku},
    )
    if channel == "FBM":
        context.line.fulfillment_channel = "Merchant"
        for stock, sku, quantity in (
            (context.stock, ctx.listing.seller_sku, 10),
            (position, listing.seller_sku, 20),
        ):
            stock.fbm_quantity = quantity
            stock.raw = {
                "source": "listings_report",
                "listing": {
                    "fulfillment-channel": "DEFAULT",
                    "seller-sku": sku,
                    "asin1": stock.asin,
                    "quantity": str(quantity),
                },
            }
    definition = ctx.payload.definition.model_dump(mode="json")
    definition["amazon"]["listing_units"].append(
        {
            **definition["amazon"]["listing_units"][0],
            "listing_id": str(listing.id),
        }
    )
    definition["amazon"]["stock_pools"][0]["listing_ids"].append(str(listing.id))
    definition["amazon"]["stock_pools"][0]["channel"] = channel
    db_session.commit()
    current = policies.get_policy(db_session, ctx.operator, context.policy_id)
    changed = policies.record_draft(
        db_session,
        ctx.operator,
        context.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(definition),
            reason="Synthetic confirmed shared stock aliases",
        ),
    )
    approved = policy_fixtures.approve(db_session, ctx, changed)
    context.approval = approved.head.id
    context.at = datetime.now(UTC)
    saved = record(db_session, context, request(preview(db_session, context)))
    [pool] = saved.snapshot.pools
    assert pool.inputs.on_hand_units == 60  # not two copies, and not 10*6 + 20*6
    assert pool.inputs.sales_units == 84
    assert pool.inputs.inbound_units == (0 if channel == "FBA" else None)
    assert saved.status == "BLOCKED"  # supplier inputs or unknown FBM inbound


@pytest.mark.parametrize("action", ["REVOKE", "REPLACE"])
def test_revoked_or_replaced_approval_cannot_create_new_evaluation(
    db_session: Session, context: Context, action: str
) -> None:
    result = preview(db_session, context)
    saved = record(db_session, context, request(result))
    current = policies.get_policy(db_session, context.policy.manager, context.policy_id)
    if action == "REPLACE":
        current = policy_fixtures.draft(db_session, context.policy, current, 28)
        policy_fixtures.approve(db_session, context.policy, current)
    else:
        review = policies.preview_review(
            db_session, context.policy.manager, context.policy_id, "REVOKE"
        )
        policies.record_review(
            db_session, context.policy.manager, context.policy_id, policy_fixtures.request(review)
        )
    with pytest.raises(policies.PolicyConflict):
        preview(db_session, context)
    db_session.rollback()
    with pytest.raises(policies.PolicyConflict):
        record(db_session, context, request(result))
    assert service.get_recommendation(db_session, context.policy.operator, saved.id) == saved


@pytest.mark.parametrize(
    "fault", ["quantity", "mapping", "new_listing", "source", "freshness", "inbound"]
)
def test_changed_preview_requires_new_review(
    db_session: Session, context: Context, fault: str
) -> None:
    result = preview(db_session, context)
    if fault == "quantity":
        context.line.quantity_ordered += 1
    elif fault == "mapping":
        context.policy.listing.mapping_status = MappingStatus.PENDING
    elif fault == "new_listing":
        factories.make_marketplace_listing(db_session, context.policy.org, context.policy.product)
    elif fault == "source":
        factories.make_amazon_sync_run(
            db_session,
            context.policy.org,
            status=SyncStatus.RUNNING,
            started_at=context.at - timedelta(seconds=1),
        )
    elif fault == "inbound":
        context.stock.inbound_shipped = 2
        context.stock.raw = {**context.stock.raw, "inbound_shipped": 2}
    else:
        run = db_session.get(AmazonSyncRun, context.line.last_seen_sync_run_id)
        assert run is not None
        assert run.window_start is not None and run.window_end is not None
        run.window_start -= timedelta(hours=3)
        run.window_end -= timedelta(hours=3)
    db_session.commit()
    with pytest.raises(service.RecommendationConflict):
        record(db_session, context, request(result))
    assert count(db_session) == 0
    refreshed = preview(db_session, context)
    saved = record(db_session, context, request(refreshed))
    assert saved.status == "BLOCKED" and not saved.purchasing_eligible
    if fault in {"mapping", "new_listing"}:
        assert "POLICY_IDENTITY_EVIDENCE_CHANGED" in saved.snapshot.policy_blocking_reasons


def test_newer_vendor_observation_and_product_label_do_not_invalidate_policy(
    db_session: Session, context: Context
) -> None:
    first = preview(db_session, context)
    context.policy.vendor_product.last_seen_at = datetime.now(UTC)
    context.policy.product.name = "Updated synthetic product label"
    db_session.commit()
    second = preview(db_session, context)
    assert not second.snapshot.policy_blocking_reasons
    assert second.evidence_sha256 == first.evidence_sha256


@pytest.mark.parametrize("inbound", [None, 0, 2])
def test_unknown_zero_and_positive_inbound_remain_distinct(
    db_session: Session, context: Context, inbound: int | None
) -> None:
    context.stock.inbound_shipped = inbound or 0
    context.stock.raw = {**context.stock.raw, "inbound_shipped": inbound}
    context.line.quantity_ordered = 0
    db_session.commit()
    result = preview(db_session, context)
    saved = record(db_session, context, request(result))
    pool = saved.snapshot.pools[0]
    assert pool.inputs.inbound_units == (0 if inbound == 0 else None)
    assert saved.status == ("NO_REORDER" if inbound == 0 else "BLOCKED")


def test_missing_baseline_is_a_recordable_blocked_snapshot(
    db_session: Session, context: Context
) -> None:
    db_session.delete(context.line)
    run = db_session.get(AmazonSyncRun, context.line.last_seen_sync_run_id)
    assert run is not None
    run.error_details = {}
    db_session.commit()
    result = preview(db_session, context)
    context.at = datetime.now(UTC)
    saved = record(db_session, context, request(result))
    assert saved.snapshot.pools[0].inputs.sales_units is None
    assert saved.status == "BLOCKED"


@pytest.mark.parametrize("change", ["reason", "approval", "digest", "actor", "policy"])
def test_request_id_cannot_be_reused_for_different_content(
    db_session: Session, context: Context, change: str
) -> None:
    payload = request(preview(db_session, context))
    record(db_session, context, payload)
    actor = context.policy.operator
    policy_id = context.policy_id
    changes: dict[str, Any] = {}
    if change == "reason":
        changes["reason"] = "Different synthetic reviewed reason"
    elif change == "approval":
        changes["expected_approved_revision_id"] = uuid.uuid4()
    elif change == "digest":
        changes["expected_evidence_sha256"] = "0" * 64
    elif change == "policy":
        policy_id = uuid.uuid4()
    else:
        user = factories.make_user(db_session, context.policy.org)
        db_session.commit()
        actor = Principal(user.id, user.organization_id, user.email, user.display_name, actor.roles)
    with pytest.raises(service.RecommendationConflict):
        service.record_recommendation(
            db_session,
            actor,
            policy_id,
            RecommendationRecordRequest.model_validate({**payload.model_dump(), **changes}),
        )
    assert count(db_session) == 1


def test_audit_failure_rolls_back_the_entire_evaluation(
    db_session: Session, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = request(preview(db_session, context))

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.recommendations.audit.record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        record(db_session, context, payload)
    assert count(db_session) == 0


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_database_prevents_saved_history_changes(
    db_session: Session, context: Context, operation: str
) -> None:
    saved = record(db_session, context, request(preview(db_session, context)))
    statement = (
        update(ReplenishmentRecommendation).values(reason="Tampered history")
        if operation == "update"
        else delete(ReplenishmentRecommendation)
    )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(statement.where(ReplenishmentRecommendation.id == saved.id))
    assert service.get_recommendation(db_session, context.policy.operator, saved.id) == saved


@pytest.mark.parametrize(
    "fault", ["actor", "policy", "product", "vendor", "approval", "snapshot", "purchase", "time"]
)
def test_database_prevents_inconsistent_snapshot_insert(
    db_session: Session, context: Context, fault: str
) -> None:
    saved = record(db_session, context, request(preview(db_session, context)))
    original = db_session.get(ReplenishmentRecommendation, saved.id)
    assert original is not None
    values = {
        column.name: getattr(original, column.name)
        for column in original.__table__.columns
        if column.name not in {"created_at", "updated_at"}
    }
    values.update(id=uuid.uuid4(), request_id=uuid.uuid4())
    if fault == "snapshot":
        values["snapshot"] = {**original.snapshot, "product_id": str(uuid.uuid4())}
    elif fault == "purchase":
        values["purchasing_eligible"] = True
    elif fault == "time":
        values["recorded_at"] = datetime.now(UTC) + timedelta(days=1)
    else:
        foreign = setup(db_session)
        key, value = {
            "actor": ("recorded_by_user_id", foreign.policy.operator.user_id),
            "policy": ("policy_id", foreign.policy_id),
            "product": ("product_id", foreign.policy.product.id),
            "vendor": ("vendor_product_id", foreign.policy.vendor_product.id),
            "approval": ("approved_revision_id", foreign.approval),
        }[fault]
        values[key] = value
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(ReplenishmentRecommendation(**values))
        db_session.flush()


def test_api_scope_permissions_pagination_and_filtering(
    db_session: Session, context: Context
) -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: context.policy.operator
    base = f"/api/v1/purchasing-policies/{context.policy_id}"
    with TestClient(app) as client:
        response = client.post(
            base + "/recommendation-preview",
            json={"expected_approved_revision_id": str(context.approval)},
        )
        assert response.status_code == 200, response.text
        payload = request(RecommendationPreview.model_validate(response.json())).model_dump(
            mode="json"
        )
        response = client.post(base + "/recommendations", json=payload)
        assert response.status_code == 201, response.text
        saved_id = response.json()["id"]
        assert client.post(base + "/recommendations", json=payload).json()["id"] == saved_id
        assert client.get("/api/v1/replenishment-recommendations?page_size=1").json()["total"] == 1
        assert (
            client.get("/api/v1/replenishment-recommendations?status=NO_REORDER").json()["total"]
            == 0
        )
        assert (
            client.get(f"/api/v1/replenishment-recommendations?product_id={uuid.uuid4()}").json()[
                "total"
            ]
            == 0
        )
        viewer = Principal(
            context.policy.operator.user_id,
            context.policy.org.id,
            "viewer@example.test",
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.get(f"/api/v1/replenishment-recommendations/{saved_id}").status_code == 200
        assert (
            client.post(
                base + "/recommendation-preview",
                json={"expected_approved_revision_id": str(context.approval)},
            ).status_code
            == 200
        )
        assert client.post(base + "/recommendations", json=payload).status_code == 403
        other = setup(db_session)
        app.dependency_overrides[get_current_principal] = lambda: other.policy.manager
        assert client.get(f"/api/v1/replenishment-recommendations/{saved_id}").status_code == 404
        assert client.get("/api/v1/replenishment-recommendations").json()["total"] == 0
        assert (
            client.post(
                base + "/recommendation-preview",
                json={"expected_approved_revision_id": str(context.approval)},
            ).status_code
            == 404
        )


def test_inactive_actor_cannot_retry(db_session: Session, context: Context) -> None:
    from app.models import User

    payload = request(preview(db_session, context))
    record(db_session, context, payload)
    user = db_session.get(User, context.policy.operator.user_id)
    assert user is not None
    user.is_active = False
    db_session.commit()
    with pytest.raises(AuthorizationError):
        record(db_session, context, payload)


@pytest.fixture
def isolated_engine(migrated_engine: Engine) -> Iterator[Engine]:
    from tests.integration.conftest import create_database, drop_database, run_migrations

    url = migrated_engine.url.set(database=f"recommendation_{uuid.uuid4().hex}_test")
    create_database(url)
    run_migrations(url)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        drop_database(url)


def test_concurrent_retries_record_one_snapshot(isolated_engine: Engine) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        context = setup(session)
        payload = request(preview(session, context))
        session.commit()

    def save(_: int) -> uuid.UUID:
        with Session(isolated_engine) as session:
            return record(session, context, payload).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(save, [1, 2]))) == 1
    with Session(isolated_engine) as session:
        assert count(session) == 1


def test_downgrade_refuses_saved_evaluations(isolated_engine: Engine) -> None:
    from tests.integration.conftest import downgrade_migrations

    with Session(isolated_engine) as session:
        ctx = setup(session)
        record(session, ctx, request(preview(session, ctx)))
    with pytest.raises(DBAPIError, match="Cannot discard saved replenishment evaluations"):
        downgrade_migrations(isolated_engine.url, "a61117202604")
