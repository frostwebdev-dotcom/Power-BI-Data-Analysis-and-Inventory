"""Real scoped supplier rows, approved commercial settings and immutable v4 history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import AuditEvent, ReplenishmentRecommendation
from app.schemas.purchasing_policies import PolicyDraftRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import CommercialRecommendationSnapshot
from app.services import purchasing_policies as policies
from app.services import recommendations as service
from app.services.replenishment import calculate_replenishment
from tests.integration import test_purchasing_policies as managers
from tests.integration import test_recommendations as evaluation
from tests.integration import test_supplier_readiness as suppliers
from tests.integration.test_recommendations import isolated_engine as isolated_engine
from tests.unit.test_recommendation_commercials import settings

pytestmark = pytest.mark.integration


def approve(
    session: Session,
    source: suppliers.Context,
    policy_data: dict[str, Any] | None = None,
    **commercial_changes: Any,
) -> None:
    ctx = source.calculation
    current = policies.get_policy(session, ctx.policy.operator, ctx.policy_id)
    data = policy_data or current.head.definition.model_dump(mode="json")
    data["commercial"] = settings(
        **{
            "pool_priority": [pool["pool_id"] for pool in data["amazon"]["stock_pools"]],
            **commercial_changes,
        },
    ).model_dump(mode="json")
    draft = policies.record_draft(
        session,
        ctx.policy.operator,
        ctx.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(data),
            reason="Synthetic explicit commercial assumptions and pool priority",
        ),
    )
    reviewed = managers.approve(session, ctx.policy, draft)
    ctx.approval = reviewed.head.id
    ctx.at = datetime.now(UTC)
    session.commit()


def setup(
    session: Session,
    *,
    quantity_minimum: int | None = 7,
    value_minimum: Decimal | None = Decimal(84),
    **commercial_changes: Any,
) -> suppliers.Context:
    source = suppliers.setup(session)
    ctx = source.calculation
    source.profile.column_map = {
        "columns": [
            *source.profile.column_map["columns"],
            {"target": "unit_cost", "source": "Price"},
        ]
    }
    source.profile.price_semantics = {
        "currency": "USD",
        "per": "case",
        "includes_tax": False,
        "discount_percent": "0",
    }
    source.row.raw_data = {**source.row.raw_data, "unit_cost": "12"}
    source.row.normalized_data = {
        **source.row.normalized_data,
        "unit_cost": "12",
        "currency": "USD",
    }
    source.row.unit_cost = Decimal(12)
    source.row.currency = "USD"
    source.stock.unit_cost = Decimal(12)
    source.stock.currency = "USD"
    ctx.policy.vendor.minimum_order_quantity = quantity_minimum
    ctx.policy.vendor.minimum_order_value = value_minimum
    approve(session, source, **commercial_changes)
    return source


def test_v4_uses_current_source_price_and_preserves_replay_audit_and_retry(
    db_session: Session,
) -> None:
    source = setup(db_session)
    ctx = source.calculation
    result = evaluation.preview(db_session, ctx)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    snapshot = result.snapshot
    assert snapshot.status == "REVIEW_REQUIRED" and snapshot.commercial.status == "ASSESSED"
    assert snapshot.allocation.total_allocated_units == 42
    assert snapshot.commercial.supplier_order_units == snapshot.commercial.price_units == 7
    assert snapshot.commercial.goods_line_value == Decimal(84)
    assert snapshot.financial is None and snapshot.profitability == "NOT_INTEGRATED"
    assert all(check.status == "MET" for check in snapshot.commercial.checks)
    assert all(pool.result == calculate_replenishment(pool.inputs) for pool in snapshot.pools)
    assert not snapshot.purchasing_eligible and not snapshot.allocation.stock_reserved
    payload = evaluation.request(result)
    saved = evaluation.record(db_session, ctx, payload)
    assert saved.snapshot_sha256 == policies.digest(saved.snapshot.model_dump(mode="json"))
    assert (
        len(list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == saved.id))))
        == 1
    )
    source.row.normalized_data = {**source.row.normalized_data, "unit_cost": "13"}
    db_session.commit()
    assert evaluation.record(db_session, ctx, payload) == saved
    fresh = evaluation.preview(db_session, ctx)
    assert isinstance(fresh.snapshot, CommercialRecommendationSnapshot)
    assert fresh.snapshot.commercial.price.status == "UNAVAILABLE"
    assert fresh.snapshot.commercial.goods_line_value is None
    with pytest.raises(service.RecommendationConflict):
        evaluation.record(db_session, ctx, evaluation.request(result))


@pytest.mark.parametrize(
    "fault", ["missing", "normalized", "staged", "snapshot", "raw", "currency", "warning"]
)
def test_missing_or_mismatched_price_does_not_use_historical_or_guessed_cost(
    db_session: Session, fault: str
) -> None:
    source = setup(db_session)
    if fault == "missing":
        source.row.unit_cost = None
        source.stock.unit_cost = None
        source.row.normalized_data = {**source.row.normalized_data, "unit_cost": None}
    if fault == "normalized":
        source.row.normalized_data = {**source.row.normalized_data, "unit_cost": "13"}
    if fault == "staged":
        source.row.unit_cost = Decimal(13)
    if fault == "snapshot":
        source.stock.unit_cost = Decimal(13)
    if fault == "raw":
        source.row.raw_data = {**source.row.raw_data, "unit_cost": "13"}
    if fault == "currency":
        source.stock.currency = "CAD"
    if fault == "warning":
        source.row.normalized_data = {
            **source.row.normalized_data,
            "issues": [{"field": "unit_cost", "code": "PRICE_INVALID"}],
        }
    db_session.commit()
    result = evaluation.preview(db_session, source.calculation)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert result.snapshot.commercial.goods_line_value is None
    assert result.snapshot.commercial.price.status == "UNAVAILABLE"
    assert result.snapshot.status == "REVIEW_REQUIRED" and not result.snapshot.purchasing_eligible
    evaluation.record(db_session, source.calculation, evaluation.request(result))


@pytest.mark.parametrize(
    "minimum,scope,expected",
    [
        (Decimal("84.01"), True, "BLOCKED"),
        (Decimal(0), True, "REVIEW_REQUIRED"),
        (None, True, "REVIEW_REQUIRED"),
        (Decimal(84), False, "REVIEW_REQUIRED"),
    ],
)
def test_confirmed_minimum_value_blocks_but_null_and_scope_unknown_stay_unknown(
    db_session: Session, minimum: Decimal | None, scope: bool, expected: str
) -> None:
    source = setup(
        db_session,
        value_minimum=minimum,
        minimum_value_scope="THIS_SKU_GOODS_LINE_AT_REPORTED_PRICE" if scope else None,
    )
    result = evaluation.preview(db_session, source.calculation)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert result.snapshot.status == expected
    check = result.snapshot.commercial.checks[1]
    assert check.status == (
        "NOT_MET"
        if expected == "BLOCKED"
        else "UNCONFIRMED"
        if minimum is None or not scope
        else "MET"
    )


def test_changed_vendor_terms_require_policy_review_and_saved_snapshot_stays_fixed(
    db_session: Session,
) -> None:
    source = setup(db_session)
    ctx = source.calculation
    result = evaluation.preview(db_session, ctx)
    saved = evaluation.record(db_session, ctx, evaluation.request(result))
    ctx.policy.vendor.purchasing_terms = {"freight": "Synthetic new freight requirement"}
    db_session.commit()
    changed = evaluation.preview(db_session, ctx)
    assert changed.snapshot.status == "BLOCKED"
    assert "POLICY_IDENTITY_EVIDENCE_CHANGED" in changed.snapshot.policy_blocking_reasons
    assert service.get_recommendation(db_session, ctx.policy.manager, saved.id) == saved


def test_v4_keeps_explicit_historical_financial_selection_separate(
    db_session: Session, tmp_path: Path
) -> None:
    from tests.integration.test_recommendation_financials import preview, upload

    source = setup(db_session)
    ctx = source.calculation
    batch = upload(db_session, ctx, tmp_path)
    result = preview(db_session, ctx, batch)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert (
        result.snapshot.financial is not None and result.snapshot.commercial.goods_line_value == 84
    )
    assert (
        result.snapshot.financial.totals is not None
        and result.snapshot.financial.totals["cost"] == 40
    )
    saved = evaluation.record(
        db_session,
        ctx,
        evaluation.request(
            result, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
        ),
    )
    row = db_session.get(ReplenishmentRecommendation, saved.id)
    assert row is not None and row.financial_import_id == batch.id


@pytest.mark.parametrize("fault", ["legacy", "commercial_policy", "reservation", "landed", "scope"])
def test_database_guards_require_v4_approved_scope_and_non_purchasing_markers(
    db_session: Session, fault: str
) -> None:
    source = setup(db_session)
    ctx = source.calculation
    saved = evaluation.record(
        db_session, ctx, evaluation.request(evaluation.preview(db_session, ctx))
    )
    row = db_session.get(ReplenishmentRecommendation, saved.id)
    assert row is not None
    snapshot = saved.snapshot.model_dump(mode="json")
    if fault == "legacy":
        snapshot["rule_version"] = "replenishment-evaluation-v2"
    if fault == "commercial_policy":
        snapshot["commercial"]["policy"]["pool_priority"] = []
    if fault == "reservation":
        snapshot["allocation"]["stock_reserved"] = True
    if fault == "landed":
        snapshot["commercial"]["landed_cost_verified"] = True
    if fault == "scope":
        snapshot["allocation"]["scope"] = "ALL_PRODUCTS"
    values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    values.update(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        snapshot=snapshot,
        snapshot_sha256=policies.digest(snapshot),
    )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(insert(ReplenishmentRecommendation).values(**values))
    db_session.rollback()


def test_downgrade_refuses_v4_and_its_approved_settings(isolated_engine: Engine) -> None:
    from tests.integration.conftest import downgrade_migrations

    with Session(isolated_engine) as session:
        source = setup(session)
        ctx = source.calculation
        evaluation.record(session, ctx, evaluation.request(evaluation.preview(session, ctx)))
    with pytest.raises(
        DBAPIError, match="Cannot discard commercial evaluation history or policy settings"
    ):
        downgrade_migrations(isolated_engine.url, "a61117202607")


@pytest.mark.parametrize("reverse", [False, True])
def test_two_real_fba_pools_cannot_spend_the_same_supplier_stock_twice(
    db_session: Session, reverse: bool
) -> None:
    from app.models import AmazonSyncRun
    from app.models.enums import ListingStatus, MappingStatus, MatchMethod
    from tests.integration import factories

    source = setup(db_session, quantity_minimum=0, value_minimum=Decimal(0))
    ctx = source.calculation
    listing = factories.make_marketplace_listing(
        db_session,
        ctx.policy.org,
        ctx.policy.product,
        seller_sku="SYNTHETIC-SECOND-FBA",
        asin="B000000002",
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=ctx.policy.manager.user_id,
        approved_at=ctx.policy.listing.approved_at,
    )
    inventory = db_session.get(AmazonSyncRun, ctx.stock.sync_run_id)
    orders = db_session.get(AmazonSyncRun, ctx.line.last_seen_sync_run_id)
    assert inventory is not None and orders is not None
    factories.make_amazon_inventory_snapshot(
        db_session,
        ctx.policy.org,
        inventory,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        captured_at=ctx.stock.captured_at,
        fnsku="SYNTHETIC-SECOND-FNSKU",
        condition="NewItem",
        fulfillable=10,
        raw={**ctx.stock.raw, "seller_sku": listing.seller_sku, "asin": listing.asin},
    )
    factories.make_amazon_order_line(
        db_session,
        ctx.policy.org,
        orders,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        quantity_ordered=14,
        purchase_date=ctx.line.purchase_date,
        last_updated_at=ctx.line.last_updated_at,
        item_status="Shipped",
        order_status="Shipped",
        fulfillment_channel="Amazon",
        sales_channel="Amazon.com",
        raw=ctx.line.raw,
    )
    approved = policies.get_policy(db_session, ctx.policy.operator, ctx.policy_id).approved
    assert approved is not None
    data = approved.definition.model_dump(mode="json")
    first = data["amazon"]["stock_pools"][0]["pool_id"]
    data["amazon"]["listing_units"].append(
        {
            "listing_id": str(listing.id),
            "base_units_per_listing_unit": 6,
            "confirmation_reference": "Synthetic second FBA listing contains six base units",
        }
    )
    data["amazon"]["stock_pools"].append(
        {
            "pool_id": "second-fba",
            "channel": "FBA",
            "listing_ids": [str(listing.id)],
            "stock_source_listing_id": str(listing.id),
            "distinct_stock_reference": "Synthetic second FNSKU is separate physical stock",
        }
    )
    priority = ["second-fba", first] if reverse else [first, "second-fba"]
    approve(db_session, source, policy_data=data, pool_priority=priority)
    result = evaluation.preview(db_session, ctx)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert result.snapshot.status == "REVIEW_REQUIRED", result.snapshot.policy_blocking_reasons
    assert [row.pool_id for row in result.snapshot.allocation.rows] == priority
    assert [row.allocated_units for row in result.snapshot.allocation.rows] == [42, 18]
    assert result.snapshot.allocation.total_allocated_units == 60
    assert result.snapshot.allocation.remaining_supplier_units == 0
    assert result.snapshot.commercial.goods_line_value == Decimal(120)
    saved = evaluation.record(db_session, ctx, evaluation.request(result))
    assert isinstance(saved.snapshot, CommercialRecommendationSnapshot)
    assert saved.snapshot.allocation == result.snapshot.allocation


def test_actual_csv_pipeline_applies_discount_once_before_current_price_checks(
    db_session: Session, tmp_path: Path
) -> None:
    from app.imports.storage import LocalStorageBackend
    from app.services.import_processing import process_import_job
    from app.services.imports import receive_upload
    from app.services.matching import match_import_job
    from app.services.snapshots import snapshot_import_job

    source = setup(db_session)
    ctx = source.calculation
    source.profile.price_semantics = {**source.profile.price_semantics, "discount_percent": "12"}
    approve(db_session, source)
    storage = LocalStorageBackend(tmp_path / "synthetic-supplier")
    outcome = receive_upload(
        db_session,
        storage,
        principal=ctx.policy.operator,
        vendor_id=ctx.policy.vendor.id,
        profile_id=source.profile.id,
        filename="priced-supplier.csv",
        content=f"SKU,Qty,Price\n{ctx.policy.vendor_product.vendor_sku},8,20\n".encode(),
        declared_mime="text/csv",
        max_bytes=10000,
    )
    process_import_job(
        db_session, storage, organization_id=ctx.policy.org.id, job_id=outcome.job.id
    )
    match_import_job(db_session, organization_id=ctx.policy.org.id, job_id=outcome.job.id)
    snapshot_import_job(db_session, organization_id=ctx.policy.org.id, job_id=outcome.job.id)
    ctx.at = datetime.now(UTC)
    result = evaluation.preview(db_session, ctx)
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert result.snapshot.commercial.price.status == "VERIFIED", (
        result.snapshot.commercial.price.issues
    )
    assert result.snapshot.commercial.price.reported_price == Decimal("17.6")
    assert result.snapshot.commercial.goods_line_value == Decimal("123.2")
    assert result.snapshot.commercial.price.evidence["job_id"] == str(outcome.job.id)
    evaluation.record(db_session, ctx, evaluation.request(result))


def test_v4_api_roles_tenant_scope_and_exact_retry(db_session: Session) -> None:
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_principal
    from app.core.config import Settings
    from app.core.security import Principal, RoleCode
    from app.db.session import get_db
    from app.main import create_app
    from app.schemas.recommendations import RecommendationPreview

    source = setup(db_session)
    ctx = source.calculation
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: ctx.policy.operator
    base = f"/api/v1/purchasing-policies/{ctx.policy_id}"
    with TestClient(app) as client:
        response = client.post(
            base + "/recommendation-preview",
            json={"expected_approved_revision_id": str(ctx.approval)},
        )
        assert response.status_code == 200, response.text
        preview = RecommendationPreview.model_validate(response.json())
        assert isinstance(preview.snapshot, CommercialRecommendationSnapshot)
        payload = evaluation.request(preview).model_dump(mode="json")
        response = client.post(base + "/recommendations", json=payload)
        assert response.status_code == 201, response.text
        saved_id = response.json()["id"]
        assert client.post(base + "/recommendations", json=payload).json()["id"] == saved_id
        viewer = Principal(
            ctx.policy.operator.user_id,
            ctx.policy.org.id,
            "viewer@example.test",
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.post(base + "/recommendations", json=payload).status_code == 403
        assert client.get("/api/v1/replenishment-recommendations/" + saved_id).status_code == 200
        foreign = evaluation.setup(db_session)
        app.dependency_overrides[get_current_principal] = lambda: foreign.policy.manager
        assert client.get("/api/v1/replenishment-recommendations/" + saved_id).status_code == 404
