"""Exact current urgency, independent supplier qualification and strict selection."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.recommendations import CommercialRecommendationSnapshot, RecommendationPreview
from app.schemas.replenishment_now import CurrentCheckRequest, CurrentPolicyCheck
from app.services.replenishment_now import current_policy_check
from tests.unit.test_replenishment_review import observation


def checked(
    *, sold: int | None = 140, stock: int | None = 20, inbound: int | None = 0, **changes: Any
) -> CurrentPolicyCheck:
    identity, preview = observation()
    data = preview.snapshot.model_dump(mode="json")
    data["approved_policy"]["definition"]["quantity"].update(
        velocity_window_days=14,
        target_days=14,
        lead_time_days=1,
        safety_days=1,
        minimum_order_units=0,
        order_multiple_units=1,
    )
    data["approved_policy"]["definition"]["amazon"]["velocity_window_days"] = 14
    data["readiness"]["request"]["velocity_window_days"] = 14
    for pool in data["readiness"]["pools"]:
        pool.update(
            sales_base_units=sold, on_hand_base_units=stock, usable_inbound_base_units=inbound
        )
    for key, value in changes.items():
        if key == "policy_blocks":
            data["policy_blocking_reasons"] = value
        elif key == "readiness":
            data["readiness"]["status"] = value
        elif key == "pools":
            data["readiness"]["pools"] = value
        elif key == "supplier":
            data["supplier"].update(value)
        elif key == "commercial":
            data["commercial"].update(value)
        elif key == "allocation":
            data["allocation"].update(value)
        elif key == "status":
            data["status"] = value
    snapshot = CommercialRecommendationSnapshot.model_validate(data)
    return current_policy_check(
        identity, RecommendationPreview(snapshot=snapshot, evidence_sha256=preview.evidence_sha256)
    )


@pytest.mark.parametrize(
    "stock,signal",
    [(19, "URGENT"), (20, "URGENT"), (21, "REPLENISH"), (160, "NO_REORDER"), (161, "NO_REORDER")],
)
def test_exact_lead_plus_safety_boundary(stock: int, signal: str) -> None:
    result = checked(stock=stock)
    assert result.urgency == signal
    assert result.pools[0].required_units == 160
    assert result.pools[0].net_shortfall_units == max(0, 160 - stock)
    assert not result.purchasing_eligible


def test_usable_inbound_counts_once_and_zero_demand_has_no_ratio() -> None:
    result = checked(stock=10, inbound=11)
    assert result.urgency == "REPLENISH"
    assert (result.pools[0].supply_days_numerator, result.pools[0].supply_days_denominator) == (
        "21",
        "10",
    )
    zero = checked(sold=0, stock=0)
    assert zero.urgency == "NO_REORDER" and zero.opportunity == "NO_SHORTFALL"
    assert zero.pools[0].supply_days_numerator is None


def test_large_near_threshold_integer_is_not_rounded_to_urgent() -> None:
    demand = 14 * 10**36
    assert checked(sold=demand, stock=2 * 10**36).urgency == "URGENT"
    assert checked(sold=demand, stock=2 * 10**36 + 1).urgency == "REPLENISH"


@pytest.mark.parametrize("missing", ["sold", "stock", "inbound"])
def test_unknown_is_never_zero(missing: str) -> None:
    result = checked(**{missing: None})
    assert result.urgency == "UNKNOWN" and result.opportunity == "UNCONFIRMED"
    assert result.pools[0].net_shortfall_units is None


@pytest.mark.parametrize(
    "changes",
    [{"policy_blocks": ["UNIT_CONVERSION_UNCONFIRMED"]}, {"readiness": "BLOCKED"}, {"pools": []}],
)
def test_incomplete_policy_readiness_or_pools_is_unknown(changes: dict[str, Any]) -> None:
    result = checked(**changes)
    assert result.urgency == "UNKNOWN" and result.opportunity == "UNCONFIRMED"


def test_stale_supplier_cannot_erase_verified_amazon_urgency() -> None:
    result = checked(supplier={"status": "BLOCKED"}, status="BLOCKED")
    assert result.urgency == "URGENT" and result.opportunity == "UNCONFIRMED"
    assert result.pools[0].supply_days_numerator == "2"


@pytest.mark.parametrize("supplier", [{"availability": "OUT_OF_STOCK"}, {"stock_base_units": 0}])
def test_no_supplier_stock_does_not_erase_urgency(supplier: dict[str, Any]) -> None:
    result = checked(supplier=supplier, status="BLOCKED")
    assert result.urgency == "URGENT" and result.opportunity == "UNAVAILABLE"


@pytest.mark.parametrize(
    "changes",
    [
        {"supplier": {"stock_base_units": None}},
        {"commercial": {"status": "INCOMPLETE"}},
        {"commercial": {"goods_line_value": None}},
        {"allocation": {"total_allocated_units": None, "status": "UNCONFIRMED"}},
        {"status": "BLOCKED"},
    ],
)
def test_incomplete_offer_is_not_a_review_opportunity(changes: dict[str, Any]) -> None:
    assert checked(**changes).opportunity == "UNCONFIRMED"


def test_unmet_minimum_or_zero_allocation_is_unavailable() -> None:
    assert checked(commercial={"status": "CONSTRAINT_NOT_MET"}).opportunity == "UNAVAILABLE"
    assert checked(allocation={"total_allocated_units": 0}).opportunity == "UNAVAILABLE"


def test_valid_partial_allocation_is_only_a_review_opportunity() -> None:
    result = checked()
    assert isinstance(result.snapshot, CommercialRecommendationSnapshot)
    assert result.snapshot.allocation.status == "PARTIAL"
    assert result.opportunity == "REVIEW_OPPORTUNITY"
    assert "LANDED_COST_NOT_EVALUATED" in result.reasons
    assert not result.purchasing_eligible


@pytest.mark.parametrize("size", [0, 11])
def test_selection_bounds(size: int) -> None:
    with pytest.raises(ValidationError):
        CurrentCheckRequest.model_validate(
            {
                "choices": [
                    {
                        "policy_id": str(uuid.uuid4()),
                        "expected_approved_revision_id": str(uuid.uuid4()),
                    }
                    for _ in range(size)
                ]
            }
        )


@pytest.mark.parametrize(
    "extra", [{"urgency": "URGENT"}, {"financial": {}}, {"purchasing_eligible": True}]
)
def test_callers_cannot_supply_signals_or_financial_defaults(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CurrentCheckRequest.model_validate(
            {
                "choices": [
                    {
                        "policy_id": str(uuid.uuid4()),
                        "expected_approved_revision_id": str(uuid.uuid4()),
                    }
                ],
                **extra,
            }
        )


def test_duplicate_policy_refused() -> None:
    choice = {"policy_id": str(uuid.uuid4()), "expected_approved_revision_id": str(uuid.uuid4())}
    with pytest.raises(ValidationError):
        CurrentCheckRequest.model_validate({"choices": [choice, choice]})


def test_one_unknown_pool_keeps_policy_unknown_without_hiding_other_pool_evidence() -> None:
    item = checked()
    snapshot = item.snapshot
    pools = snapshot.readiness.pools
    changed = snapshot.model_copy(
        update={
            "readiness": snapshot.readiness.model_copy(
                update={
                    "pools": (pools[0], pools[1].model_copy(update={"quantity_input_ready": False}))
                }
            )
        }
    )
    result = current_policy_check(
        item.identity, RecommendationPreview(snapshot=changed, evidence_sha256=item.evidence_sha256)
    )
    assert result.urgency == "UNKNOWN" and result.opportunity == "UNCONFIRMED"
    assert result.pools[0].urgency == "URGENT" and result.pools[1].urgency == "UNKNOWN"


def test_zero_supplier_price_remains_review_incomplete() -> None:
    item = checked()
    assert isinstance(item.snapshot, CommercialRecommendationSnapshot)
    snapshot = item.snapshot.model_copy(
        update={
            "commercial": item.snapshot.commercial.model_copy(
                update={
                    "price": item.snapshot.commercial.price.model_copy(
                        update={"reported_price": Decimal(0)}
                    )
                }
            )
        }
    )
    result = current_policy_check(
        item.identity,
        RecommendationPreview(snapshot=snapshot, evidence_sha256=item.evidence_sha256),
    )
    assert result.urgency == "URGENT" and result.opportunity == "UNCONFIRMED"
