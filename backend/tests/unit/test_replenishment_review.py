"""Exact comparison ordering and historical risk without freshness or purchase claims."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import localcontext
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import Product
from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import (
    CommercialRecommendationSnapshot,
    EvaluationSnapshot,
    RecommendationPreview,
)
from app.schemas.replenishment import ReplenishmentPolicy
from app.schemas.replenishment_review import VendorComparisonRequest, VendorComparisonResponse
from app.services.replenishment import calculate_replenishment
from app.services.replenishment_review import historical_shortfall, historical_stockout_risk
from app.services.vendor_comparisons import compare_observations, normalized_price

FIXTURE = Path(__file__).resolve().parents[3] / "frontend/e2e/fixtures/recommendation-v4.json"
AT = datetime(2026, 11, 17, 20, tzinfo=UTC)


def observation(
    name: str = "Vendor",
    price: str = "88",
    factor: int = 6,
    *,
    tax: bool = False,
    unit_factor: int = 6,
) -> tuple[PolicyIdentity, RecommendationPreview]:
    data = json.loads(FIXTURE.read_text())["snapshot"]
    policy_id, approval_id, vendor_product_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    data.update(policy_id=str(policy_id), vendor_product_id=str(vendor_product_id))
    approved = data["approved_policy"]
    approved.update(
        id=str(approval_id), policy_id=str(policy_id), vendor_product_id=str(vendor_product_id)
    )
    approved["definition"]["amazon"]["listing_units"][0]["base_units_per_listing_unit"] = (
        unit_factor
    )
    approved["definition"]["commercial"]["base_units_per_price_unit"] = factor
    data["commercial"]["policy"]["base_units_per_price_unit"] = factor
    data["commercial"]["price"].update(reported_price=price, base_units_per_price_unit=factor)
    data["commercial"]["price"]["evidence"]["price_semantics"]["includes_tax"] = tax
    snapshot = CommercialRecommendationSnapshot.model_validate(data)
    identity = PolicyIdentity(
        catalog_item_number="SYNTHETIC",
        product_name="Synthetic product",
        vendor_id=uuid.uuid4(),
        vendor_name=name,
        vendor_code=name.upper(),
        vendor_sku="SYNTHETIC-SKU",
    )
    return identity, RecommendationPreview(snapshot=snapshot, evidence_sha256="a" * 64)


def compare(
    *observations: tuple[PolicyIdentity, RecommendationPreview], order: str = "BASE_PRICE"
) -> VendorComparisonResponse:
    product = Product(
        id=observations[0][1].snapshot.product_id,
        catalog_item_number="SYNTHETIC",
        name="Synthetic product",
    )
    return compare_observations(product, tuple(observations), order, AT)


def test_exact_fraction_orders_values_that_share_a_rounded_display() -> None:
    a = observation("A", "1", 3)
    b = observation("B", "0.33333333333333333333333333333333333", 1)
    result = compare(a, b)
    assert result.price_comparison_status == "AVAILABLE"
    assert [offer.identity.vendor_name for offer in result.offers] == ["B", "A"]
    [low, high] = result.offers
    assert low.base_price is not None and high.base_price is not None
    assert low.base_price.display_per_base_unit == high.base_price.display_per_base_unit
    assert high.base_price.exact_numerator == "1" and high.base_price.exact_denominator == "3"
    assert high.base_price.display_is_rounded
    assert not result.saved and not result.stock_reserved and not result.purchasing_eligible


def test_equal_exact_prices_have_deterministic_label_order_and_zero_is_not_unknown() -> None:
    a = observation("Zulu", "12", 6)
    b = observation("Alpha", "6", 3)
    zero = observation("Zero", "0", 6)
    assert [offer.identity.vendor_name for offer in compare(a, b, zero).offers] == [
        "Zero",
        "Alpha",
        "Zulu",
    ]
    assert [offer.identity.vendor_name for offer in compare(zero, b, a).offers] == [
        "Zero",
        "Alpha",
        "Zulu",
    ]
    with localcontext() as ctx:
        ctx.prec = 2
        price = compare(a, b).offers[0].base_price
        assert price is not None and price.display_per_base_unit == 2


@pytest.mark.parametrize(
    "fault,code",
    [("units", "APPROVED_BASE_UNIT_POLICIES_DIFFER"), ("tax", "REPORTED_PRICE_TAX_BASIS_DIFFERS")],
)
def test_incompatible_bases_disable_price_ordering(fault: str, code: str) -> None:
    a = observation("Zulu", "1", 6)
    b = observation("Alpha", "20", 6, tax=fault == "tax", unit_factor=12 if fault == "units" else 6)
    result = compare(a, b)
    assert result.price_comparison_status == "UNAVAILABLE" and result.applied_order == "VENDOR_NAME"
    assert code in result.issues and not any(o.price_comparable for o in result.offers)
    assert [o.identity.vendor_name for o in result.offers] == ["Alpha", "Zulu"]


@pytest.mark.parametrize(
    "fault", ["missing", "currency", "tax", "factor", "policy", "source", "units"]
)
def test_unknown_or_unapproved_current_cost_never_falls_back_to_historical(fault: str) -> None:
    identity, preview = observation()
    data = preview.snapshot.model_dump(mode="json")
    if fault == "missing":
        data["commercial"]["price"]["reported_price"] = None
    if fault == "currency":
        data["commercial"]["price"]["currency"] = "CAD"
    if fault == "tax":
        data["commercial"]["price"]["evidence"]["price_semantics"].pop("includes_tax")
    if fault == "factor":
        data["commercial"]["price"]["base_units_per_price_unit"] = None
    if fault == "policy":
        data["policy_blocking_reasons"] = ["POLICY_IDENTITY_EVIDENCE_CHANGED"]
    if fault == "source":
        data["supplier"]["status"] = "BLOCKED"
    if fault == "units":
        data["approved_policy"]["definition"]["amazon"]["listing_units"][0][
            "base_units_per_listing_unit"
        ] = None
    changed = CommercialRecommendationSnapshot.model_validate(data)
    assert normalized_price(changed) is None
    result = compare((identity, RecommendationPreview(snapshot=changed, evidence_sha256="b" * 64)))
    assert result.offers[0].base_price is None and result.comparable_price_count == 0
    assert changed.financial is not None and changed.financial.rows[0].amounts["cost"] == 40


def test_partial_selection_orders_only_verified_subset_and_keeps_unknown_last() -> None:
    good = observation("Zulu", "6", 6)
    lower = observation("Mid", "3", 6)
    identity, missing = observation("Alpha")
    data = missing.snapshot.model_dump(mode="json")
    data["commercial"]["price"]["status"] = "UNAVAILABLE"
    missing = RecommendationPreview(
        snapshot=CommercialRecommendationSnapshot.model_validate(data), evidence_sha256="a" * 64
    )
    result = compare(good, (identity, missing), lower)
    assert result.price_comparison_status == "PARTIAL" and result.comparable_price_count == 2
    assert [o.identity.vendor_name for o in result.offers] == ["Mid", "Zulu", "Alpha"]
    assert not result.offers[-1].price_comparable


def test_one_verified_offer_never_becomes_a_comparison_winner() -> None:
    result = compare(observation())
    assert result.price_comparison_status == "UNAVAILABLE" and result.applied_order == "VENDOR_NAME"
    assert "TWO_VERIFIED_CURRENT_PRICES_REQUIRED" in result.issues


@pytest.mark.parametrize("count", [0, 11])
def test_comparison_selection_is_explicit_and_bounded(count: int) -> None:
    with pytest.raises(ValidationError):
        VendorComparisonRequest.model_validate(
            {
                "product_id": uuid.uuid4(),
                "choices": [
                    {"policy_id": uuid.uuid4(), "expected_approved_revision_id": uuid.uuid4()}
                    for _ in range(count)
                ],
            }
        )


def test_duplicate_choices_and_client_money_flags_are_refused() -> None:
    choice = {"policy_id": uuid.uuid4(), "expected_approved_revision_id": uuid.uuid4()}
    for changes in (
        {"choices": [choice, choice]},
        {"choices": [choice], "price": "12"},
        {"choices": [choice], "purchasing_eligible": True},
    ):
        with pytest.raises(ValidationError):
            VendorComparisonRequest.model_validate({"product_id": uuid.uuid4(), **changes})


def risk_snapshot(
    on_hand: int, inbound: int = 0, sold: int = 7
) -> CommercialRecommendationSnapshot:
    snapshot = observation()[1].snapshot
    assert isinstance(snapshot, CommercialRecommendationSnapshot)
    policy = ReplenishmentPolicy(
        velocity_window_days=7,
        target_days=14,
        lead_time_days=2,
        safety_days=1,
        minimum_order_units=0,
        order_multiple_units=1,
    )
    pools = tuple(
        pool.model_copy(
            update={
                "inputs": pool.inputs.model_copy(
                    update={
                        "policy": policy,
                        "sales_units": sold,
                        "on_hand_units": on_hand,
                        "inbound_units": inbound,
                    }
                ),
                "result": calculate_replenishment(
                    pool.inputs.model_copy(
                        update={
                            "policy": policy,
                            "sales_units": sold,
                            "on_hand_units": on_hand,
                            "inbound_units": inbound,
                        }
                    )
                ),
            }
        )
        for pool in snapshot.pools
    )
    return snapshot.model_copy(update={"pools": pools})


@pytest.mark.parametrize(
    "on_hand,inbound,expected", [(0, 0, True), (3, 0, True), (4, 0, False), (2, 2, False)]
)
def test_historical_stockout_risk_uses_exact_confirmed_supply(
    on_hand: int, inbound: int, expected: bool
) -> None:
    snapshot = risk_snapshot(on_hand, inbound)
    assert historical_stockout_risk(snapshot) is expected
    assert historical_shortfall(snapshot) is True


@pytest.mark.parametrize("fault", ["inputs", "source", "identity", "pool"])
def test_unknown_historical_risk_is_not_false_or_current(fault: str) -> None:
    snapshot = risk_snapshot(0)
    if fault == "inputs":
        pool = snapshot.pools[0]
        snapshot = snapshot.model_copy(
            update={
                "pools": (
                    pool.model_copy(
                        update={"inputs": pool.inputs.model_copy(update={"inbound_units": None})}
                    ),
                )
            }
        )
    if fault == "source":
        snapshot = snapshot.model_copy(
            update={"readiness": snapshot.readiness.model_copy(update={"status": "BLOCKED"})}
        )
    if fault == "identity":
        snapshot = snapshot.model_copy(update={"policy_blocking_reasons": ("MAPPING_UNAPPROVED",)})
    if fault == "pool":
        snapshot = snapshot.model_copy(update={"pools": ()})
    assert historical_stockout_risk(snapshot) is None


def test_zero_sales_and_no_shortfall_stay_distinct_from_unknown() -> None:
    snapshot = risk_snapshot(0, sold=0)
    assert historical_stockout_risk(snapshot) is False and historical_shortfall(snapshot) is False
    pool = snapshot.pools[0]
    snapshot = snapshot.model_copy(
        update={
            "pools": (
                pool.model_copy(
                    update={
                        "result": pool.result.model_copy(
                            update={
                                "calculation": pool.result.calculation.model_copy(
                                    update={"net_shortfall_units": None}
                                )
                            }
                        )
                    }
                ),
            )
        }
    )
    assert historical_shortfall(snapshot) is None


def test_legacy_snapshot_has_no_current_comparison_price() -> None:
    path = FIXTURE.with_name("recommendation-v3.json")
    snapshot: EvaluationSnapshot = TypeAdapter(EvaluationSnapshot).validate_python(
        json.loads(path.read_text())["snapshot"]
    )
    assert normalized_price(snapshot) is None
