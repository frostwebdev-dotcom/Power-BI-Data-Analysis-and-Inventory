"""PostgreSQL evidence, pool conversion, tenant isolation and read-only behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AmazonInventorySnapshot,
    AmazonOrderLine,
    AmazonSyncRun,
    AuditEvent,
    MarketplaceListing,
    Organization,
    Product,
)
from app.models.enums import (
    AmazonSyncJobType,
    ListingStatus,
    MappingStatus,
    MatchMethod,
    SyncStatus,
)
from app.schemas.amazon_readiness import AmazonReadinessRequest, AmazonReadinessResponse
from app.services.amazon_orders import ORDER_DATE_REPORT_TYPE, ORDER_EVIDENCE_VERSION, REPORT_TYPE
from app.services.amazon_readiness import ReadinessProductNotFound, check_amazon_readiness
from tests.integration import factories

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
CUTOFF = NOW - timedelta(hours=1)


def evidence(run: AmazonSyncRun, *, aggregated: bool = False) -> dict[str, Any]:
    return {
        "_prms_readiness": {
            "version": ORDER_EVIDENCE_VERSION,
            "run_id": str(run.id),
            "aggregated": aggregated,
        }
    }


def contract(basis: str = "ORDER_DATE") -> dict[str, Any]:
    return {
        "readiness": {
            "version": ORDER_EVIDENCE_VERSION,
            "report_basis": basis,
            "report_type": ORDER_DATE_REPORT_TYPE if basis == "ORDER_DATE" else REPORT_TYPE,
        }
    }


@dataclass
class Context:
    organization: Organization
    actor: Principal
    product: Product
    listing: MarketplaceListing
    orders: AmazonSyncRun
    inventory: AmazonSyncRun
    line: AmazonOrderLine
    snapshot: AmazonInventorySnapshot
    request: AmazonReadinessRequest


@pytest.fixture
def context(db_session: Session) -> Context:
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    actor = Principal(
        user.id, organization.id, user.email, user.display_name, frozenset({RoleCode.VIEWER})
    )
    product = factories.make_product(db_session, organization)
    listing = factories.make_marketplace_listing(
        db_session,
        organization,
        product,
        seller_sku="single",
        asin="B000000001",
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=user.id,
        approved_at=NOW - timedelta(days=2),
    )
    orders = factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.COMPLETED,
        started_at=NOW - timedelta(minutes=15),
        completed_at=NOW - timedelta(minutes=14),
        window_start=CUTOFF - timedelta(days=14),
        window_end=CUTOFF,
        error_details=contract(),
    )
    inventory = factories.make_amazon_sync_run(
        db_session,
        organization,
        status=SyncStatus.COMPLETED,
        started_at=NOW - timedelta(minutes=2),
        completed_at=NOW - timedelta(minutes=1),
    )
    snapshot = factories.make_amazon_inventory_snapshot(
        db_session,
        organization,
        inventory,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        captured_at=inventory.started_at,
        fnsku="FNSKU1",
        condition="NewItem",
        fulfillable=10,
        raw={
            "source": "fba_inventory",
            "seller_sku": listing.seller_sku,
            "asin": listing.asin,
            "fulfillable": 10,
            "inbound_working": 0,
            "inbound_shipped": 0,
            "inbound_receiving": 0,
        },
    )
    line = factories.make_amazon_order_line(
        db_session,
        organization,
        orders,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        quantity_ordered=3,
        purchase_date=CUTOFF - timedelta(days=1),
        last_updated_at=NOW - timedelta(minutes=30),
        item_status="Shipped",
        fulfillment_channel="Amazon",
        sales_channel="Amazon.com",
        raw=evidence(orders),
    )
    request = AmazonReadinessRequest.model_validate(
        {
            "product_id": product.id,
            "marketplace_id": "ATVPDKIKX0DER",
            "sales_channel": "Amazon.com",
            "demand_basis": "FULLY_SHIPPED_ONLY",
            "velocity_window_days": 14,
            "orders_max_age_seconds": 7200,
            "inventory_max_age_seconds": 3600,
            "listing_units": [
                {
                    "listing_id": listing.id,
                    "base_units_per_listing_unit": 6,
                    "confirmation_reference": "Synthetic six base units per listing",
                }
            ],
            "stock_pools": [
                {
                    "pool_id": "fba",
                    "channel": "FBA",
                    "listing_ids": [listing.id],
                    "stock_source_listing_id": listing.id,
                    "distinct_stock_reference": "Synthetic independent FBA position",
                }
            ],
        }
    )
    return Context(
        organization, actor, product, listing, orders, inventory, line, snapshot, request
    )


def check(session: Session, context: Context, **changes: Any) -> AmazonReadinessResponse:
    session.flush()
    request = AmazonReadinessRequest.model_validate(
        {**context.request.model_dump(mode="json"), **changes}
    )
    return check_amazon_readiness(session, context.actor, request, now=lambda: NOW)


def codes(result: AmazonReadinessResponse) -> set[str]:
    return {issue.code for issue in result.issues}


def test_complete_inputs_convert_units_without_mutation(
    db_session: Session,
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Readiness must not construct an Amazon client")

    monkeypatch.setattr("app.services.amazon_orders.AmazonClient", forbidden)
    before = db_session.scalar(select(func.count()).select_from(AuditEvent))
    result = check(db_session, context)
    assert result.status == "DATA_AVAILABLE" and result.issues == ()
    [pool] = result.pools
    assert (pool.sales_base_units, pool.on_hand_base_units, pool.usable_inbound_base_units) == (
        18,
        60,
        0,
    )
    assert pool.quantity_input_ready
    assert not result.purchasing_eligible and not result.confirmations_persisted
    assert (result.demand_window_start, result.demand_window_end) == (
        CUTOFF - timedelta(days=14),
        CUTOFF,
    )
    assert result.order_coverage_run_ids == (context.orders.id,)
    assert result.inventory_run is not None and result.inventory_run.id == context.inventory.id
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == before
    assert "amazon_order_id" not in result.model_dump_json()


@pytest.mark.parametrize("basis", ["legacy", "LAST_UPDATE"])
def test_update_only_and_legacy_cannot_prove_zero_sales(
    db_session: Session,
    context: Context,
    basis: str,
) -> None:
    db_session.delete(context.line)
    context.orders.error_details = {} if basis == "legacy" else contract(basis)
    result = check(db_session, context)
    assert "PURCHASE_WINDOW_BASELINE_UNPROVEN" in codes(result)
    assert result.pools[0].sales_base_units is None
    assert not result.pools[0].quantity_input_ready


def test_zero_sales_with_complete_baseline(db_session: Session, context: Context) -> None:
    db_session.delete(context.line)
    result = check(db_session, context)
    assert result.status == "DATA_AVAILABLE" and result.pools[0].sales_base_units == 0


@pytest.mark.parametrize("gap", [False, True])
def test_adjacent_baselines_vs_one_second_gap(
    db_session: Session,
    context: Context,
    gap: bool,
) -> None:
    context.orders.window_start = CUTOFF - timedelta(days=7)
    factories.make_amazon_sync_run(
        db_session,
        context.organization,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.COMPLETED,
        started_at=NOW - timedelta(minutes=20),
        completed_at=NOW - timedelta(minutes=19),
        window_start=CUTOFF - timedelta(days=14),
        window_end=CUTOFF - timedelta(days=7, seconds=int(gap)),
        error_details=contract(),
    )
    result = check(db_session, context)
    assert ("PURCHASE_WINDOW_COVERAGE_GAP" in codes(result)) is gap
    assert result.status == ("BLOCKED" if gap else "DATA_AVAILABLE")


@pytest.mark.parametrize("target", ["orders", "inventory"])
@pytest.mark.parametrize("fault", ["FAILED", "RUNNING", "PARTIAL", "FUTURE", "TIED", "MISSING"])
def test_latest_bad_run_never_falls_back(
    db_session: Session,
    context: Context,
    target: str,
    fault: str,
) -> None:
    run = context.orders if target == "orders" else context.inventory
    if fault == "MISSING":
        run.marketplace_id = "OTHER"
    elif fault == "TIED":
        factories.make_amazon_sync_run(
            db_session,
            context.organization,
            job_type=run.job_type,
            status=SyncStatus.COMPLETED,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
    else:
        factories.make_amazon_sync_run(
            db_session,
            context.organization,
            job_type=run.job_type,
            status=SyncStatus.RUNNING
            if fault == "RUNNING"
            else (SyncStatus.FAILED if fault == "FAILED" else SyncStatus.COMPLETED),
            started_at=NOW + timedelta(minutes=1)
            if fault == "FUTURE"
            else NOW - timedelta(seconds=30),
            completed_at=None
            if fault == "RUNNING"
            else (NOW + timedelta(minutes=2) if fault == "FUTURE" else NOW - timedelta(seconds=20)),
            rows_failed=1 if fault == "PARTIAL" else 0,
        )
    result = check(db_session, context)
    assert result.status == "BLOCKED" and not result.pools[0].quantity_input_ready
    assert any(code.startswith(target.upper() + "_RUN_") for code in codes(result))


@pytest.mark.parametrize("target", ["orders", "inventory"])
def test_freshness_limits_are_explicit(db_session: Session, context: Context, target: str) -> None:
    result = check(db_session, context, **{target + "_max_age_seconds": 30})
    assert result.status == "BLOCKED"
    assert any(code.startswith(target.upper() + "_DATA_STALE") for code in codes(result))


def test_missing_latest_sku_has_no_old_fallback(db_session: Session, context: Context) -> None:
    factories.make_amazon_sync_run(
        db_session,
        context.organization,
        status=SyncStatus.COMPLETED,
        started_at=NOW - timedelta(seconds=30),
        completed_at=NOW - timedelta(seconds=20),
    )
    result = check(db_session, context)
    assert "LATEST_RUN_SKU_MISSING" in codes(result)
    assert result.pools[0].on_hand_base_units is None and result.listings[0].snapshot_id is None


@pytest.mark.parametrize("fault", ["missing", "mismatch", "aggregated", "foreign", "omitted"])
def test_uncertain_line_provenance_blocks_demand(
    db_session: Session,
    context: Context,
    fault: str,
) -> None:
    if fault == "missing":
        context.line.raw = {}
    elif fault == "mismatch":
        context.line.raw = evidence(context.inventory)
    elif fault == "aggregated":
        context.line.raw = evidence(context.orders, aggregated=True)
    elif fault == "foreign":
        other = factories.make_organization(db_session)
        foreign = factories.make_amazon_sync_run(
            db_session,
            other,
            job_type=AmazonSyncJobType.ORDERS_REPORT,
            status=SyncStatus.COMPLETED,
            started_at=context.orders.started_at,
            completed_at=context.orders.completed_at,
            window_start=context.orders.window_start,
            window_end=context.orders.window_end,
            error_details=contract(),
        )
        context.line.last_seen_sync_run_id = foreign.id
        context.line.raw = evidence(foreign)
    else:
        factories.make_amazon_sync_run(
            db_session,
            context.organization,
            job_type=AmazonSyncJobType.ORDERS_REPORT,
            status=SyncStatus.COMPLETED,
            started_at=NOW - timedelta(minutes=10),
            completed_at=NOW - timedelta(minutes=9),
            window_start=context.orders.window_start,
            window_end=context.orders.window_end,
            error_details=contract(),
        )
    result = check(db_session, context)
    assert "ORDER_LINE_PROVENANCE_OR_AGGREGATION_UNCONFIRMED" in codes(result)
    assert result.pools[0].sales_base_units is None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("fulfillment_channel", "Unexpected", "ORDER_FULFILLMENT_CHANNEL_UNCONFIRMED"),
        ("item_status", None, "ORDER_SHIPPED_STATUS_UNCONFIRMED"),
        ("order_status", "Unknown", "ORDER_SHIPPED_STATUS_UNCONFIRMED"),
        ("sales_channel", None, "ORDER_MARKETPLACE_UNCONFIRMED"),
        ("asin", "OTHER", "ORDER_ASIN_CONFLICT_OR_MISSING"),
    ],
)
def test_unconfirmed_order_fields_remain_unknown(
    db_session: Session,
    context: Context,
    field: str,
    value: str | None,
    code: str,
) -> None:
    setattr(context.line, field, value)
    result = check(db_session, context)
    assert code in codes(result) and result.pools[0].sales_base_units is None


@pytest.mark.parametrize("status", ["Canceled", "Pending", "Unshipped", "PartiallyShipped"])
def test_shipped_basis_excludes_nonshipped_orders(
    db_session: Session,
    context: Context,
    status: str,
) -> None:
    context.line.order_status = status
    result = check(db_session, context)
    assert result.status == "DATA_AVAILABLE" and result.pools[0].sales_base_units == 0


def test_other_sales_channel_and_cutoff_excluded(db_session: Session, context: Context) -> None:
    context.line.sales_channel = "Amazon.ca"
    factories.make_amazon_order_line(
        db_session,
        context.organization,
        context.orders,
        seller_sku=context.listing.seller_sku,
        purchase_date=CUTOFF,
        last_updated_at=NOW - timedelta(minutes=30),
        quantity_ordered=999,
    )
    result = check(db_session, context)
    assert result.status == "DATA_AVAILABLE" and result.pools[0].sales_base_units == 0


@pytest.mark.parametrize(
    "fault,code",
    [
        ("mapping", "MAPPING_NOT_ACTIVE_APPROVED"),
        ("approval", "MAPPING_EVIDENCE_UNCONFIRMED"),
        ("product", "PRODUCT_INACTIVE"),
        ("units", "UNIT_CONVERSION_UNCONFIRMED"),
        ("pool", "STOCK_POOL_DISTINCTNESS_UNCONFIRMED"),
        ("configuration", "LISTING_CONFIGURATION_INCOMPLETE"),
        ("snapshot_asin", "INVENTORY_ASIN_CONFLICT_OR_MISSING"),
        ("capture_time", "SNAPSHOT_TIME_STALE_FUTURE_OR_INVALID"),
    ],
)
def test_unconfirmed_policy_and_snapshot_evidence_block(
    db_session: Session,
    context: Context,
    fault: str,
    code: str,
) -> None:
    changes: dict[str, Any] = {}
    if fault == "mapping":
        context.listing.mapping_status = MappingStatus.PENDING
    elif fault == "approval":
        context.listing.approved_at = NOW + timedelta(seconds=1)
    elif fault == "product":
        context.product.is_active = False
    elif fault == "units":
        changes["listing_units"] = [
            {
                "listing_id": context.listing.id,
                "base_units_per_listing_unit": None,
                "confirmation_reference": None,
            }
        ]
    elif fault == "pool":
        changes["stock_pools"] = [
            {**context.request.stock_pools[0].model_dump(), "distinct_stock_reference": None}
        ]
    elif fault == "configuration":
        changes["listing_units"] = []
    elif fault == "snapshot_asin":
        context.snapshot.asin = "OTHER"
    else:
        context.snapshot.captured_at = NOW + timedelta(seconds=1)
    result = check(db_session, context, **changes)
    assert code in codes(result) and not result.pools[0].quantity_input_ready


@pytest.mark.parametrize("value", [None, 0, 4])
def test_unknown_zero_and_positive_inbound_distinct(
    db_session: Session,
    context: Context,
    value: int | None,
) -> None:
    context.snapshot.inbound_shipped = value or 0
    context.snapshot.raw = {**context.snapshot.raw, "inbound_shipped": value}
    result = check(db_session, context)
    pool = result.pools[0]
    assert pool.observed_inbound_base_units == (value * 6 if value is not None else None)
    assert pool.usable_inbound_base_units == (0 if value == 0 else None)
    assert pool.quantity_input_ready is (value == 0)


def alias(session: Session, context: Context) -> tuple[MarketplaceListing, AmazonInventorySnapshot]:
    listing = factories.make_marketplace_listing(
        session,
        context.organization,
        context.product,
        seller_sku="alias",
        asin=context.listing.asin,
        listing_status=ListingStatus.ACTIVE,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=context.actor.user_id,
        approved_at=context.listing.approved_at,
    )
    snapshot = factories.make_amazon_inventory_snapshot(
        session,
        context.organization,
        context.inventory,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        captured_at=context.snapshot.captured_at,
        fnsku=context.snapshot.fnsku,
        condition=context.snapshot.condition,
        fulfillable=10,
        raw={**context.snapshot.raw, "seller_sku": listing.seller_sku},
    )
    return listing, snapshot


def test_fba_alias_dedup_and_split_pool_blocking(db_session: Session, context: Context) -> None:
    listing, _ = alias(db_session, context)
    units = [
        *context.request.model_dump()["listing_units"],
        {
            "listing_id": listing.id,
            "base_units_per_listing_unit": 6,
            "confirmation_reference": "Synthetic confirmed matching FBA units",
        },
    ]
    pool = context.request.stock_pools[0].model_dump()
    result = check(
        db_session,
        context,
        listing_units=units,
        stock_pools=[{**pool, "listing_ids": [context.listing.id, listing.id]}],
    )
    assert result.status == "DATA_AVAILABLE" and result.pools[0].on_hand_base_units == 60
    result = check(
        db_session,
        context,
        listing_units=units,
        stock_pools=[
            pool,
            {
                **pool,
                "pool_id": "fake-distinct",
                "listing_ids": [listing.id],
                "stock_source_listing_id": listing.id,
            },
        ],
    )
    assert "FBA_STOCK_POOL_OVERLAP" in codes(result)
    assert all(not pool.quantity_input_ready for pool in result.pools)


def test_shared_fbm_stock_not_summed(db_session: Session, context: Context) -> None:
    listing, snapshot = alias(db_session, context)
    for position, quantity in [(context.snapshot, 100), (snapshot, 16)]:
        position.fbm_quantity = quantity
        position.raw = {
            "source": "listings_report",
            "listing": {
                "fulfillment-channel": "DEFAULT",
                "seller-sku": position.seller_sku,
                "asin1": position.asin,
                "quantity": str(quantity),
            },
        }
    context.line.fulfillment_channel = "Merchant"
    units = [
        {
            "listing_id": context.listing.id,
            "base_units_per_listing_unit": 1,
            "confirmation_reference": "Synthetic confirmed single base units",
        },
        {
            "listing_id": listing.id,
            "base_units_per_listing_unit": 6,
            "confirmation_reference": "Synthetic confirmed six pack units",
        },
    ]
    pool = {
        **context.request.stock_pools[0].model_dump(),
        "channel": "FBM",
        "listing_ids": [context.listing.id, listing.id],
    }
    result = check(db_session, context, listing_units=units, stock_pools=[pool])
    assert result.pools[0].on_hand_base_units == 100  # not 100 + 16*6
    assert result.pools[0].sales_base_units == 3
    assert codes(result) == {"FBM_INBOUND_NOT_INGESTED"}
    assert result.pools[0].usable_inbound_base_units is None


def test_read_bound_blocks_partial_demand(
    db_session: Session,
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factories.make_amazon_order_line(
        db_session,
        context.organization,
        context.orders,
        seller_sku=context.listing.seller_sku,
        purchase_date=context.line.purchase_date,
        last_updated_at=context.line.last_updated_at,
        asin=context.listing.asin,
        item_status="Shipped",
        fulfillment_channel="Amazon",
        sales_channel="Amazon.com",
        raw=evidence(context.orders),
    )
    monkeypatch.setattr("app.repositories.amazon_readiness.MAX_ORDER_LINES", 1)
    monkeypatch.setattr("app.services.amazon_readiness.MAX_ORDER_LINES", 1)
    result = check(db_session, context)
    assert "ORDER_LINE_READ_LIMIT_EXCEEDED" in codes(result)
    assert result.pools[0].sales_base_units is None


def test_api_scope_validation_and_roles(
    db_session: Session,
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def evaluate(
        session: Session, actor: Principal, payload: AmazonReadinessRequest
    ) -> AmazonReadinessResponse:
        return check_amazon_readiness(session, actor, payload, now=lambda: NOW)

    monkeypatch.setattr("app.api.v1.routes.amazon.check_amazon_readiness", evaluate)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: context.actor
    url = "/api/v1/amazon/replenishment-readiness"
    with TestClient(app) as client:
        response = client.post(url, json=context.request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "DATA_AVAILABLE"
        invalid = {**context.request.model_dump(mode="json"), "marketplace_id": "OTHER"}
        assert client.post(url, json=invalid).status_code == 422
        foreign = factories.make_organization(db_session)
        foreign_actor = Principal(
            context.actor.user_id,
            foreign.id,
            "foreign@example.test",
            "Other",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: foreign_actor
        assert client.post(url, json=context.request.model_dump(mode="json")).status_code == 404
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            context.actor.user_id, context.organization.id, "none@example.test", "None", frozenset()
        )
        assert client.post(url, json=context.request.model_dump(mode="json")).status_code == 403
    with pytest.raises(ReadinessProductNotFound):
        check_amazon_readiness(db_session, foreign_actor, context.request, now=lambda: NOW)
