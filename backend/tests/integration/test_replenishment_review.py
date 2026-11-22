"""Real selected-offer evaluation and scoped latest-snapshot dashboard queries."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.extract import normalize_vendor_sku
from app.main import create_app
from app.models import AuditEvent, ReplenishmentRecommendation
from app.models.enums import MappingStatus, MatchMethod
from app.schemas.purchasing_policies import PolicyCreateRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import CommercialRecommendationSnapshot
from app.schemas.replenishment_review import VendorComparisonRequest
from app.services import purchasing_policies as policies
from app.services.replenishment_review import review_page, review_summary
from app.services.vendor_comparisons import preview_vendor_comparison
from tests.integration import factories
from tests.integration import test_purchasing_policies as managers
from tests.integration import test_recommendation_commercials as commercial
from tests.integration import test_recommendations as evaluation
from tests.integration import test_supplier_readiness as suppliers

pytestmark = pytest.mark.integration


def second_offer(
    session: Session, first: suppliers.Context, *, price: Decimal = Decimal(6), legacy: bool = False
) -> suppliers.Context:
    ctx = first.calculation
    vendor = factories.make_vendor(
        session,
        ctx.policy.org,
        name="Zulu comparison supplier",
        minimum_order_quantity=0,
        minimum_order_value=Decimal(0),
    )
    line = factories.make_vendor_product(
        session,
        ctx.policy.org,
        vendor,
        product_id=ctx.policy.product.id,
        vendor_sku="COMPARE-SKU",
        normalized_vendor_sku=normalize_vendor_sku("COMPARE-SKU"),
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        mapping_approved_by_user_id=ctx.policy.manager.user_id,
        mapping_approved_at=ctx.policy.vendor_product.mapping_approved_at,
    )
    profile = factories.make_import_profile(
        session,
        ctx.policy.org,
        vendor,
        **{
            key: getattr(first.profile, key)
            for key in (
                "column_map",
                "normalization_rules",
                "availability_rules",
                "quantity_semantics",
                "price_semantics",
                "pack_size_handling",
            )
        },
    )
    file = factories.make_import_file(
        session, ctx.policy.org, vendor, uploaded_at=first.file.uploaded_at
    )
    job = factories.make_import_job(
        session,
        ctx.policy.org,
        vendor,
        file,
        vendor_import_profile_id=profile.id,
        profile_version=profile.version,
        status=first.job.status,
        total_rows=1,
        processed_rows=1,
        matched_rows=1,
        created_at=first.job.created_at,
        started_at=first.job.started_at,
        completed_at=first.job.completed_at,
    )
    row = factories.make_import_job_row(
        session,
        ctx.policy.org,
        job,
        vendor_sku=line.vendor_sku,
        normalized_vendor_sku=line.normalized_vendor_sku,
        vendor_product_id=line.id,
        product_id=ctx.policy.product.id,
        quantity=first.row.quantity,
        unit_cost=price,
        currency="USD",
        availability_status=first.row.availability_status,
        status=first.row.status,
        match_result=first.row.match_result,
        matched_by=first.row.matched_by,
        match_priority=first.row.match_priority,
        raw_data={**first.row.raw_data, "unit_cost": str(price)},
        normalized_data={**first.row.normalized_data, "unit_cost": str(price)},
    )
    stock = factories.make_snapshot(
        session,
        ctx.policy.org,
        vendor,
        line,
        job,
        product_id=ctx.policy.product.id,
        quantity_available=first.stock.quantity_available,
        unit_cost=price,
        currency="USD",
        effective_at=file.uploaded_at,
        captured_at=first.stock.captured_at,
    )
    approved = policies.get_policy(session, ctx.policy.operator, ctx.policy_id).approved
    assert approved is not None
    data = approved.definition.model_dump(mode="json")
    data["supplier"]["profile_id"] = str(profile.id)
    if legacy:
        data.pop("commercial")
    payload = PolicyCreateRequest(
        vendor_product_id=line.id,
        definition=PurchasingPolicyDefinition.model_validate(data),
        reason="Synthetic second offer for the same canonical product",
    )
    identity = replace(ctx.policy, vendor=vendor, vendor_product=line, payload=payload)
    decision = managers.approve(
        session, identity, policies.create_policy(session, ctx.policy.operator, payload)
    )
    calculation = replace(
        ctx,
        policy=identity,
        policy_id=decision.policy_id,
        approval=decision.head.id,
        at=datetime.now(UTC),
    )
    session.commit()
    return suppliers.Context(calculation, profile, job, file, row, stock)


def request(
    *sources: suppliers.Context, order: str = "BASE_PRICE", **changes: Any
) -> VendorComparisonRequest:
    return VendorComparisonRequest.model_validate(
        {
            "product_id": sources[0].calculation.policy.product.id,
            "choices": [
                {
                    "policy_id": s.calculation.policy_id,
                    "expected_approved_revision_id": s.calculation.approval,
                }
                for s in sources
            ],
            "order_by": order,
            **changes,
        }
    )


def counts(session: Session) -> tuple[int, int]:
    return (
        session.scalar(select(func.count()).select_from(ReplenishmentRecommendation)) or 0,
        session.scalar(select(func.count()).select_from(AuditEvent)) or 0,
    )


def test_two_real_supplier_offers_compare_exactly_without_writes_or_external_calls(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = commercial.setup(db_session)
    first.calculation.policy.vendor.name = "Alpha comparison supplier"
    db_session.commit()
    second = second_offer(db_session, first)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No external source call")

    monkeypatch.setattr("app.services.amazon_orders.AmazonClient", forbidden)
    before = counts(db_session)
    actor = first.calculation.policy.operator
    result = preview_vendor_comparison(db_session, actor, request(first, second))
    assert result.price_comparison_status == "AVAILABLE" and result.comparable_price_count == 2
    assert [offer.identity.vendor_name for offer in result.offers] == [
        "Zulu comparison supplier",
        "Alpha comparison supplier",
    ]
    assert (
        result.offers[0].base_price is not None
        and result.offers[0].base_price.display_per_base_unit == 1
    )
    assert len({offer.snapshot.evaluated_at for offer in result.offers}) == 1
    assert not result.saved and not result.purchasing_eligible and not result.stock_reserved
    assert counts(db_session) == before
    reversed_result = preview_vendor_comparison(db_session, actor, request(second, first))
    assert [o.policy_id for o in reversed_result.offers] == [o.policy_id for o in result.offers]
    assert all(o.snapshot.supplier_inputs == "VERIFIED" for o in result.offers)


@pytest.mark.parametrize(
    "fault", ["foreign_product", "foreign_policy", "mismatched_product", "stale_approval"]
)
def test_whole_selection_fails_closed_without_partial_writes(
    db_session: Session, fault: str
) -> None:
    source = commercial.setup(db_session)
    other = commercial.setup(db_session)
    payload = request(source).model_dump(mode="json")
    if fault == "foreign_product":
        payload["product_id"] = str(other.calculation.policy.product.id)
    if fault == "foreign_policy":
        payload["choices"][0]["policy_id"] = str(other.calculation.policy_id)
    if fault == "mismatched_product":
        payload["product_id"] = str(uuid.uuid4())
    if fault == "stale_approval":
        payload["choices"][0]["expected_approved_revision_id"] = str(uuid.uuid4())
    before = counts(db_session)
    with pytest.raises(
        policies.PolicyConflict if fault == "stale_approval" else policies.PolicyNotFound
    ):
        preview_vendor_comparison(
            db_session,
            source.calculation.policy.operator,
            VendorComparisonRequest.model_validate(payload),
        )
    assert counts(db_session) == before


def test_legacy_offer_has_no_guessed_current_price(db_session: Session) -> None:
    first = commercial.setup(db_session)
    second = second_offer(db_session, first, legacy=True)
    result = preview_vendor_comparison(
        db_session, first.calculation.policy.operator, request(first, second)
    )
    assert result.price_comparison_status == "UNAVAILABLE"
    assert sum(o.base_price is not None for o in result.offers) == 1
    assert not any(o.price_comparable for o in result.offers)


def test_selected_financial_report_stays_historical_and_foreign_report_fails(
    db_session: Session, tmp_path: Path
) -> None:
    from app.services.financial_imports import FinancialImportNotFound
    from tests.integration.test_recommendation_financials import upload

    source = commercial.setup(db_session)
    second = second_offer(db_session, source)
    batch = upload(db_session, source.calculation, tmp_path)
    payload = request(
        source, second, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
    )
    result = preview_vendor_comparison(db_session, source.calculation.policy.operator, payload)
    for offer in result.offers:
        assert isinstance(offer.snapshot, CommercialRecommendationSnapshot)
        assert (
            offer.snapshot.financial is not None and offer.snapshot.financial.import_id == batch.id
        )
    assert (
        result.offers[0].base_price is not None
        and result.offers[0].base_price.display_per_base_unit == 1
    )
    assert payload.financial is not None
    bad = payload.model_copy(
        update={"financial": payload.financial.model_copy(update={"import_id": uuid.uuid4()})}
    )
    with pytest.raises(FinancialImportNotFound):
        preview_vendor_comparison(db_session, source.calculation.policy.operator, bad)


def test_latest_saved_queue_counts_one_record_per_policy_and_preserves_draft_approval(
    db_session: Session,
) -> None:
    source = commercial.setup(db_session)
    ctx = source.calculation
    first = evaluation.record(
        db_session, ctx, evaluation.request(evaluation.preview(db_session, ctx))
    )
    second = evaluation.record(
        db_session, ctx, evaluation.request(evaluation.preview(db_session, ctx))
    )
    actor = ctx.policy.operator
    summary = review_summary(db_session, actor)
    assert summary.latest_saved_total == summary.current_approval_total == 1
    assert (
        summary.current_approval_review_required == summary.current_approval_positive_shortfall == 1
    )
    result = review_page(db_session, actor, 1, 25)
    assert result.total == 1 and result.items[0].id == second.id and result.items[0].id != first.id
    assert result.items[0].historical and not result.items[0].source_freshness_rechecked
    current = policies.get_policy(db_session, actor, ctx.policy_id)
    managers.draft(db_session, ctx.policy, current, 21)
    assert review_summary(db_session, actor).current_approval_total == 1
    preview = policies.preview_review(db_session, ctx.policy.manager, ctx.policy_id, "REVOKE")
    policies.record_review(db_session, ctx.policy.manager, ctx.policy_id, managers.request(preview))
    changed = review_summary(db_session, actor)
    assert changed.current_approval_total == 0 and changed.approval_changed_total == 1
    assert review_page(db_session, actor, 1, 25).total == 0
    assert review_page(db_session, actor, 1, 25, approval="CHANGED").items[0].id == second.id


def test_review_filters_literal_search_pagination_and_tenant_scope(db_session: Session) -> None:
    first = commercial.setup(db_session)
    second = second_offer(db_session, first)
    actor = first.calculation.policy.operator
    for source in (first, second):
        evaluation.record(
            db_session,
            source.calculation,
            evaluation.request(evaluation.preview(db_session, source.calculation)),
        )
    first.calculation.policy.vendor.name = "Literal %_ comparison"
    db_session.commit()
    assert (
        review_page(db_session, actor, 1, 1).total == 2
        and len(review_page(db_session, actor, 2, 1).items) == 1
    )
    literal = review_page(db_session, actor, 1, 25, q="%_")
    assert literal.total == 1 and literal.items[0].identity.vendor_name == "Literal %_ comparison"
    assert (
        review_page(db_session, actor, 1, 25, vendor_id=second.calculation.policy.vendor.id).total
        == 1
    )
    assert (
        review_page(db_session, actor, 1, 25, product_id=first.calculation.policy.product.id).total
        == 2
    )
    assert review_page(db_session, actor, 1, 25, policy_id=first.calculation.policy_id).total == 1
    assert review_page(db_session, actor, 1, 25, status="BLOCKED").total == 0
    assert review_page(db_session, actor, 1, 25, shortfall_only=True).total == 2
    foreign = commercial.setup(db_session)
    assert review_summary(db_session, foreign.calculation.policy.manager).latest_saved_total == 0
    assert review_page(db_session, foreign.calculation.policy.manager, 1, 25).total == 0
    assert counts(db_session)[0] == 2


def test_api_viewers_compare_and_read_history_but_cannot_record(db_session: Session) -> None:
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
            "/api/v1/vendor-comparisons/preview", json=request(source).model_dump(mode="json")
        )
        assert response.status_code == 200, response.text
        assert response.json()["saved"] is False
        assert client.get("/api/v1/replenishment-review/summary").json()["latest_saved_total"] == 0
        assert client.get("/api/v1/replenishment-review").json()["total"] == 0
        assert client.get("/api/v1/replenishment-review?page_size=101").status_code == 422
        preview = evaluation.preview(db_session, ctx)
        assert (
            client.post(
                f"/api/v1/purchasing-policies/{ctx.policy_id}/recommendations",
                json=evaluation.request(preview).model_dump(mode="json"),
            ).status_code
            == 403
        )
        nobody = replace(viewer, roles=frozenset())
        app.dependency_overrides[get_current_principal] = lambda: nobody
        assert (
            client.post(
                "/api/v1/vendor-comparisons/preview", json=request(source).model_dump(mode="json")
            ).status_code
            == 403
        )
        assert client.get("/api/v1/replenishment-review").status_code == 403
    with pytest.raises(AuthorizationError):
        preview_vendor_comparison(db_session, nobody, request(source))
