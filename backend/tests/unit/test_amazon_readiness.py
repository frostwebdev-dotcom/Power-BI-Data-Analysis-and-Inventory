"""Strict assumptions, exact stock normalization and purchase-window coverage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.cli.amazon_poc import Outcome, build_parser, dispatch
from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.core.security import Principal
from app.models.amazon import AmazonInventorySnapshot
from app.models.catalog import MarketplaceListing
from app.schemas.amazon_readiness import AmazonReadinessRequest, StockPoolChoice
from app.services.amazon_orders import parse_orders_report
from app.services.amazon_readiness import _pool, check_amazon_readiness, covers_window
from tests.unit.test_amazon_orders_parse import FIXTURE_UTF8

NOW = datetime(2026, 11, 17, tzinfo=UTC)


def request(**changes: Any) -> AmazonReadinessRequest:
    values = {
        "product_id": uuid.uuid4(),
        "marketplace_id": "ATVPDKIKX0DER",
        "sales_channel": "Amazon.com",
        "demand_basis": "FULLY_SHIPPED_ONLY",
        "velocity_window_days": 14,
        "orders_max_age_seconds": 7200,
        "inventory_max_age_seconds": 3600,
        "listing_units": [],
        "stock_pools": [],
    }
    values.update(changes)
    return AmazonReadinessRequest.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"velocity_window_days": 7.0},
        {"velocity_window_days": "14"},
        {"velocity_window_days": True},
        {"orders_max_age_seconds": 0},
        {"inventory_max_age_seconds": True},
        {"orders_max_age_seconds": 2678401},
        {"sales_channel": "Amazon.ca"},
        {"marketplace_id": "OTHER"},
        {"demand_basis": "ALL_ORDERS"},
        {"mapping_approved": True},
    ],
)
def test_invalid_or_unapproved_assumptions_are_not_coerced(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        request(**changes)


def test_definitions_require_unique_members_and_sources_and_explicit_factors() -> None:
    identifier = uuid.uuid4()
    listing = {
        "listing_id": identifier,
        "base_units_per_listing_unit": 1,
        "confirmation_reference": "Manager confirmed single units",
    }
    pool = {
        "pool_id": "warehouse",
        "channel": "FBM",
        "listing_ids": [identifier],
        "stock_source_listing_id": identifier,
        "distinct_stock_reference": "Confirmed independent warehouse",
    }
    for changes in (
        {"listing_units": [listing, listing]},
        {"stock_pools": [pool, pool]},
        {"stock_pools": [{**pool, "listing_ids": [identifier, identifier]}]},
        {"stock_pools": [{**pool, "stock_source_listing_id": uuid.uuid4()}]},
        {"stock_pools": [pool, {**pool, "pool_id": "alias"}]},
    ):
        with pytest.raises(ValidationError):
            request(**changes)
    for factor in (0, -1, True, 1.5, "6", 1000001):
        with pytest.raises(ValidationError):
            request(listing_units=[{**listing, "base_units_per_listing_unit": factor}])
    assert (
        request(
            listing_units=[
                {**listing, "base_units_per_listing_unit": None, "confirmation_reference": None}
            ]
        )
        .listing_units[0]
        .base_units_per_listing_unit
        is None
    )


@pytest.mark.parametrize(
    "offsets,expected",
    [
        ([(0, 7), (7, 14)], True),
        ([(7, 14), (0, 8)], True),
        ([(-1, 15)], True),
        ([(0, 6), (7, 14)], False),
        ([(0, 13)], False),
        ([(1, 14)], False),
        ([], False),
    ],
)
def test_interval_union_requires_complete_half_open_coverage(
    offsets: list[tuple[int, int]], expected: bool
) -> None:
    intervals = [(NOW + timedelta(days=start), NOW + timedelta(days=end)) for start, end in offsets]
    assert covers_window(intervals, NOW, NOW + timedelta(days=14)) is expected


def pool_fixture(
    channel: str = "FBA",
) -> tuple[
    dict[uuid.UUID, MarketplaceListing],
    dict[str, AmazonInventorySnapshot],
    dict[uuid.UUID, int | None],
    dict[tuple[uuid.UUID, str], int | None],
    StockPoolChoice,
]:
    one, two = uuid.uuid4(), uuid.uuid4()
    listings = {
        one: MarketplaceListing(id=one, seller_sku="single"),
        two: MarketplaceListing(id=two, seller_sku="pack"),
    }
    snapshots = {}
    for identifier, listing in listings.items():
        quantity = 100 if identifier == one else 16
        raw = {
            "source": "fba_inventory" if channel == "FBA" else "listings_report",
            "seller_sku": listing.seller_sku,
            "asin": "ASIN",
            "fulfillable": 10,
            "inbound_working": 0,
            "inbound_shipped": 0,
            "inbound_receiving": 0,
            "listing": {
                "fulfillment-channel": "DEFAULT",
                "seller-sku": listing.seller_sku,
                "asin1": "ASIN",
                "quantity": str(quantity),
            },
        }
        snapshots[listing.seller_sku] = AmazonInventorySnapshot(
            seller_sku=listing.seller_sku,
            asin="ASIN",
            fnsku="FNSKU",
            condition="NewItem",
            captured_at=NOW,
            fulfillable=10,
            inbound_working=0,
            inbound_shipped=0,
            inbound_receiving=0,
            fbm_quantity=quantity,
            raw=raw,
        )
    factors: dict[uuid.UUID, int | None] = {one: 1, two: 1 if channel == "FBA" else 6}
    demand: dict[tuple[uuid.UUID, str], int | None] = {(one, channel): 2, (two, channel): 3}
    pool = StockPoolChoice(
        pool_id="physical-stock",
        channel=channel,
        listing_ids=(one, two),
        stock_source_listing_id=one,
        distinct_stock_reference="Warehouse stock confirmed distinct",
    )
    return listings, snapshots, factors, demand, pool


def test_shared_merchant_stock_is_taken_once_and_pack_demand_is_converted() -> None:
    listings, snapshots, factors, demand, pool = pool_fixture("FBM")
    issues: list[str] = []
    result = _pool(
        pool,
        listings,
        snapshots,
        factors,
        demand,
        lambda code, *args, **kwargs: issues.append(code),
    )
    assert result.sales_base_units == 20
    assert result.on_hand_base_units == 100  # not 100 + 16*6
    assert result.usable_inbound_base_units is None and "FBM_INBOUND_NOT_INGESTED" in issues


def test_fba_aliases_do_not_double_stock_and_zero_inbound_is_known() -> None:
    listings, snapshots, factors, demand, pool = pool_fixture()
    issues: list[str] = []
    result = _pool(
        pool,
        listings,
        snapshots,
        factors,
        demand,
        lambda code, *args, **kwargs: issues.append(code),
    )
    assert result.sales_base_units == 5 and result.on_hand_base_units == 10
    assert result.observed_inbound_base_units == result.usable_inbound_base_units == 0
    assert issues == []


@pytest.mark.parametrize("value", [None, True, 0.0, -1, "0"])
def test_coerced_or_invalid_fba_zero_stays_unknown(value: object) -> None:
    listings, snapshots, factors, demand, pool = pool_fixture()
    source = snapshots[listings[pool.stock_source_listing_id].seller_sku]
    source.fulfillable = 0
    source.raw["fulfillable"] = value
    result = _pool(pool, listings, snapshots, factors, demand, lambda *args, **kwargs: None)
    assert result.on_hand_base_units is None


def test_positive_and_unknown_inbound_are_not_credited_without_arrival_evidence() -> None:
    listings, snapshots, factors, demand, pool = pool_fixture()
    source = snapshots[listings[pool.stock_source_listing_id].seller_sku]
    source.inbound_shipped = 7
    source.raw["inbound_shipped"] = 7
    issues: list[str] = []
    result = _pool(
        pool,
        listings,
        snapshots,
        factors,
        demand,
        lambda code, *args, **kwargs: issues.append(code),
    )
    assert result.observed_inbound_base_units == 7 and result.usable_inbound_base_units is None
    assert "INBOUND_ARRIVAL_UNCONFIRMED" in issues
    source.raw["inbound_working"] = None
    assert (
        _pool(
            pool, listings, snapshots, factors, demand, lambda *args, **kwargs: None
        ).observed_inbound_base_units
        is None
    )


def test_parser_marks_aggregated_lines_without_changing_raw_whitelist() -> None:
    rows = parse_orders_report(FIXTURE_UTF8).lines
    aggregate = next(row for row in rows if row.amazon_order_id == "111-1000000-0000005")
    assert aggregate.aggregated and aggregate.quantity_ordered == 5
    assert all(
        not row.aggregated for row in rows if row.amazon_order_id != aggregate.amazon_order_id
    )
    assert all("_prms_readiness" not in row.raw for row in rows)


def test_baseline_cli_is_opt_in_and_dispatches_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = build_parser()
    assert not parser.parse_args(["sync-orders"]).order_date_baseline
    args = parser.parse_args(["sync-orders", "--days", "30", "--order-date-baseline"])
    calls: list[tuple[int, bool]] = []

    def fake(settings: Settings, days: int, *, order_date_baseline: bool = False) -> Outcome:
        calls.append((days, order_date_baseline))
        return Outcome(0, [])

    monkeypatch.setattr("app.cli.amazon_poc.command_sync_orders", fake)
    assert dispatch(args, Settings(app_env="test")).exit_code == 0
    assert calls == [(30, True)]


def test_endpoint_requires_authentication(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/amazon/replenishment-readiness", json=request().model_dump(mode="json")
        ).status_code
        == 401
    )


def test_service_requires_staff_role_before_reading_database() -> None:
    actor = Principal(uuid.uuid4(), uuid.uuid4(), "none@example.test", "None", frozenset())
    with pytest.raises(AuthorizationError):
        check_amazon_readiness(cast(Session, object()), actor, request())
