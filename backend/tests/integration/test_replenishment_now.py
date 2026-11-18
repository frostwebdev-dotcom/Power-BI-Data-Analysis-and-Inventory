"""Real scoped fresh evaluations, policy choices and supplier-independent urgency."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import User
from app.models.enums import (
    AvailabilityStatus,
    ImportJobStatus,
    ListingStatus,
    MappingStatus,
    MatchMethod,
)
from app.schemas.purchasing_policies import PolicyCreateRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import CommercialRecommendationSnapshot
from app.schemas.replenishment_now import CurrentCheckRequest
from app.services import purchasing_policies as policies
from app.services.replenishment_now import current_check, current_choices
from tests.integration import factories
from tests.integration import test_purchasing_policies as managers
from tests.integration import test_recommendation_commercials as commercial
from tests.integration import test_recommendations as evaluation
from tests.integration.test_replenishment_review import counts, second_offer

pytestmark = pytest.mark.integration


def request(*contexts: evaluation.Context) -> CurrentCheckRequest:
    return CurrentCheckRequest.model_validate(
        {
            "choices": [
                {"policy_id": c.policy_id, "expected_approved_revision_id": c.approval}
                for c in contexts
            ]
        }
    )


def test_real_current_check_has_one_time_stable_selection_and_no_writes_or_provider_calls(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = commercial.setup(db_session)
    second = second_offer(db_session, first)
    ctx = first.calculation
    saved = evaluation.record(
        db_session, ctx, evaluation.request(evaluation.preview(db_session, ctx))
    )
    before = counts(db_session)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No external source calls")

    monkeypatch.setattr("app.services.amazon_orders.AmazonClient", forbidden)
    at = second.calculation.at
    result = current_check(
        db_session, ctx.policy.operator, request(ctx, second.calculation), now=lambda: at
    )
    assert result.summary.checked_policies == result.summary.replenish == 2
    assert result.summary.review_opportunities == 2
    assert {i.snapshot.evaluated_at for i in result.items} == {at}
    assert (
        result.freshness_rechecked
        and not result.saved
        and not result.live_sync_performed
        and not result.stock_reserved
        and not result.purchasing_eligible
    )
    assert counts(db_session) == before
    reverse = current_check(
        db_session, ctx.policy.operator, request(second.calculation, ctx), now=lambda: at
    )
    assert [i.policy_id for i in reverse.items] == [i.policy_id for i in result.items]
    stale = current_check(
        db_session, ctx.policy.operator, request(ctx), now=lambda: at + timedelta(days=40)
    )
    assert stale.items[0].urgency == "UNKNOWN" and stale.items[0].opportunity == "UNCONFIRMED"
    assert stale.items[0].evidence_sha256 != saved.evidence_sha256
    assert counts(db_session) == before


def test_failed_supplier_run_preserves_current_amazon_shortfall(db_session: Session) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    good = current_check(db_session, ctx.policy.operator, request(ctx), now=lambda: ctx.at)
    source.job.status = ImportJobStatus.FAILED
    db_session.commit()
    result = current_check(db_session, ctx.policy.operator, request(ctx), now=lambda: ctx.at)
    assert result.items[0].snapshot.status == "BLOCKED"
    assert result.items[0].urgency == good.items[0].urgency == "REPLENISH"
    assert result.items[0].opportunity == "UNCONFIRMED"
    assert (
        result.items[0].pools[0].net_shortfall_units == good.items[0].pools[0].net_shortfall_units
    )


@pytest.mark.parametrize("consistent", [False, True])
def test_zero_supplier_positions_require_coherent_availability_evidence(
    db_session: Session,
    consistent: bool,
) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    ctx.stock.fulfillable = 0
    ctx.stock.raw = {**ctx.stock.raw, "fulfillable": 0}
    source.row.quantity = 0
    source.row.normalized_data = {**source.row.normalized_data, "quantity": 0}
    source.stock.quantity_available = Decimal(0)
    if consistent:
        source.profile.availability_rules = {"available_when": "quantity_gt_zero"}
        source.row.normalized_data = {**source.row.normalized_data, "availability": "OUT_OF_STOCK"}
        source.row.availability_status = AvailabilityStatus.OUT_OF_STOCK
        source.stock.availability_status = AvailabilityStatus.OUT_OF_STOCK
        commercial.approve(db_session, source)
    db_session.commit()
    item = current_check(db_session, ctx.policy.operator, request(ctx), now=lambda: ctx.at).items[0]
    assert item.urgency == "URGENT"
    assert item.opportunity == ("UNAVAILABLE" if consistent else "UNCONFIRMED")
    assert isinstance(item.snapshot, CommercialRecommendationSnapshot)
    assert item.snapshot.supplier.status == ("VERIFIED" if consistent else "BLOCKED")
    assert item.pools[0].on_hand_base_units == 0 and item.pools[0].supply_days_numerator == "0"


def test_choices_search_filters_pagination_scope_and_latest_non_draft_decision(
    db_session: Session,
) -> None:
    first = commercial.setup(db_session)
    second = second_offer(db_session, first)
    ctx = first.calculation
    ctx.policy.vendor.name = "Literal %_ current choice"
    db_session.commit()
    actor = ctx.policy.operator
    page = current_choices(db_session, actor, 1, 1)
    assert page.total == 2 and len(page.items) == 1
    assert current_choices(db_session, actor, 2, 1).items[0].policy_id != page.items[0].policy_id
    assert current_choices(db_session, actor, 1, 25, q="%_").items[0].policy_id == ctx.policy_id
    assert current_choices(db_session, actor, 1, 25, q="%_").total == 1
    assert current_choices(db_session, actor, 1, 25, product_id=ctx.policy.product.id).total == 2
    assert (
        current_choices(
            db_session, actor, 1, 25, vendor_id=second.calculation.policy.vendor.id
        ).total
        == 1
    )
    original = policies.get_policy(db_session, actor, ctx.policy_id)
    managers.draft(db_session, ctx.policy, original, 21)
    assert (
        current_choices(db_session, actor, 1, 25, q="%_").items[0].approved_revision_id
        == ctx.approval
    )
    review = policies.preview_review(db_session, ctx.policy.manager, ctx.policy_id, "REVOKE")
    policies.record_review(db_session, ctx.policy.manager, ctx.policy_id, managers.request(review))
    assert current_choices(db_session, actor, 1, 25).total == 1
    assert current_choices(db_session, actor, 1, 25, q="%_").total == 0
    foreign = managers.setup(db_session)
    assert current_choices(db_session, foreign.operator, 1, 25).total == 0


@pytest.mark.parametrize("fault", ["foreign", "missing", "stale"])
def test_full_selection_preflight_and_stale_approval_refuse_partial_results(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    payload = request(ctx).model_dump(mode="json")
    if fault == "foreign":
        other = commercial.setup(db_session)
        payload["choices"].append(
            {
                "policy_id": str(other.calculation.policy_id),
                "expected_approved_revision_id": str(other.calculation.approval),
            }
        )
    elif fault == "missing":
        payload["choices"].append(
            {"policy_id": str(uuid.uuid4()), "expected_approved_revision_id": str(uuid.uuid4())}
        )
    else:
        payload["choices"][0]["expected_approved_revision_id"] = str(uuid.uuid4())
    before = counts(db_session)
    if fault != "stale":

        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("Preflight must precede all evaluations")

        monkeypatch.setattr("app.services.replenishment_now.preview_recommendation", forbidden)
    with pytest.raises(policies.PolicyConflict if fault == "stale" else policies.PolicyNotFound):
        current_check(db_session, ctx.policy.operator, CurrentCheckRequest.model_validate(payload))
    assert counts(db_session) == before


def test_viewer_api_permissions_request_bounds_and_no_forged_signals(db_session: Session) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    viewer = Principal(
        ctx.policy.operator.user_id,
        ctx.policy.org.id,
        "viewer@example.test",
        "Viewer",
        frozenset({RoleCode.VIEWER}),
    )
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: viewer
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/replenishment-now/preview", json=request(ctx).model_dump(mode="json")
        )
        assert response.status_code == 200, response.text
        assert response.json()["saved"] is False
        assert client.get("/api/v1/replenishment-now/policies").json()["total"] == 1
        assert client.get("/api/v1/replenishment-now/policies?page_size=101").status_code == 422
        assert (
            client.post("/api/v1/replenishment-now/preview", json={"choices": []}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/replenishment-now/preview",
                json={**request(ctx).model_dump(mode="json"), "urgency": "URGENT"},
            ).status_code
            == 422
        )
        nobody = replace(viewer, roles=frozenset())
        app.dependency_overrides[get_current_principal] = lambda: nobody
        assert client.get("/api/v1/replenishment-now/policies").status_code == 403
        assert (
            client.post(
                "/api/v1/replenishment-now/preview", json=request(ctx).model_dump(mode="json")
            ).status_code
            == 403
        )
    with pytest.raises(AuthorizationError):
        current_check(db_session, nobody, request(ctx))


def test_inactive_actor_cannot_recheck(db_session: Session) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    user = db_session.get(User, ctx.policy.operator.user_id)
    assert user is not None
    user.is_active = False
    db_session.commit()
    with pytest.raises(AuthorizationError):
        current_check(db_session, ctx.policy.operator, request(ctx))


def test_batch_spans_scoped_products_without_guessing_missing_product_sources(
    db_session: Session,
) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    owner = ctx.policy
    product = factories.make_product(db_session, owner.org)
    line = factories.make_vendor_product(
        db_session,
        owner.org,
        owner.vendor,
        product_id=product.id,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        mapping_approved_by_user_id=owner.operator.user_id,
        mapping_approved_at=owner.vendor_product.mapping_approved_at,
    )
    listing = factories.make_marketplace_listing(
        db_session,
        owner.org,
        product,
        asin="B000000002",
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=owner.operator.user_id,
        approved_at=owner.listing.approved_at,
    )
    current = policies.get_policy(db_session, owner.operator, ctx.policy_id)
    assert current.approved is not None
    definition = current.approved.definition.model_dump(mode="json")
    definition["amazon"]["product_id"] = str(product.id)
    definition["amazon"]["listing_units"][0]["listing_id"] = str(listing.id)
    definition["amazon"]["stock_pools"][0].update(
        listing_ids=[str(listing.id)], stock_source_listing_id=str(listing.id)
    )
    payload = PolicyCreateRequest(
        vendor_product_id=line.id,
        definition=PurchasingPolicyDefinition.model_validate(definition),
        reason="Synthetic separate product with no ingested inventory",
    )
    other = replace(owner, product=product, vendor_product=line, listing=listing, payload=payload)
    approved = managers.approve(
        db_session, other, policies.create_policy(db_session, owner.operator, payload)
    )
    second = replace(
        ctx,
        policy=other,
        policy_id=approved.policy_id,
        approval=approved.approved.id if approved.approved else approved.head.id,
    )
    result = current_check(db_session, owner.operator, request(ctx, second))
    assert {i.product_id for i in result.items} == {owner.product.id, product.id}
    assert result.summary.checked_policies == 2 and result.summary.unknown == 1
    assert next(i for i in result.items if i.product_id == product.id).urgency == "UNKNOWN"
