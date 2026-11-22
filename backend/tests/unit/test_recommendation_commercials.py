"""Conservation of stock, exact goods money and explicit commercial assumptions."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

import pytest
from pydantic import ValidationError

from app.imports.profile_rules import ProfileRules
from app.models import VendorInventorySnapshot
from app.schemas.commercials import CommercialPolicy, CurrentSupplierPrice
from app.schemas.purchasing_policies import PurchasingPolicyDefinition
from app.schemas.recommendations import PoolEvaluation
from app.schemas.replenishment import ReplenishmentPolicy, ReplenishmentPreviewInput
from app.services.commercial_policy import commercial_policy_blocks
from app.services.purchasing_policies import definition_snapshot, digest
from app.services.recommendation_commercials import (
    allocate_supplier_stock,
    assess_commercials,
    verified_price,
)
from app.services.replenishment import calculate_replenishment
from tests.unit.test_purchasing_policies import definition
from tests.unit.test_supplier_readiness import supplier, supplier_settings


def settings(**changes: Any) -> CommercialPolicy:
    return CommercialPolicy.model_validate(
        {
            "currency": "USD",
            "base_units_per_price_unit": 6,
            "price_confirmation_reference": "Synthetic confirmed reported price for six base units",
            "minimum_quantity_scope": "THIS_SKU_SUPPLIER_ORDER_UNITS",
            "minimum_value_scope": "THIS_SKU_GOODS_LINE_AT_REPORTED_PRICE",
            "minimum_confirmation_reference": "Synthetic confirmed SKU-level minimums",
            "terms_review_reference": "Synthetic purchasing terms reviewed separately",
            "pool_priority": ["a", "b"],
            "allocation_reference": "Synthetic manager selects a before b",
            **changes,
        }
    )


def quantity(**changes: Any) -> ReplenishmentPolicy:
    return ReplenishmentPolicy.model_validate(
        {
            "velocity_window_days": 7,
            "target_days": 7,
            "lead_time_days": 0,
            "safety_days": 0,
            "minimum_order_units": 12,
            "order_multiple_units": 6,
            **changes,
        }
    )


def pool(name: str, request: int, policy: ReplenishmentPolicy | None = None) -> PoolEvaluation:
    inputs = ReplenishmentPreviewInput(
        policy=policy or quantity(),
        sales_units=request,
        on_hand_units=0,
        inbound_units=0,
        vendor_availability="IN_STOCK",
        vendor_stock_units=None,
        mapping_approved=True,
        units_confirmed=True,
        source_data_current=True,
    )
    return PoolEvaluation(
        pool_id=name, inputs=inputs, result=calculate_replenishment(inputs), blocking_reasons=()
    )


def quote(**changes: Any) -> CurrentSupplierPrice:
    return CurrentSupplierPrice.model_validate(
        {
            "status": "VERIFIED",
            "reported_price": "12.3456",
            "currency": "USD",
            "base_units_per_price_unit": 6,
            "evidence": {},
            "issues": [],
            **changes,
        }
    )


def rules(**price_changes: Any) -> ProfileRules:
    return ProfileRules.model_validate(
        {
            "column_map": {
                "columns": [
                    {"target": "vendor_sku", "source": "SKU"},
                    {"target": "quantity_available", "source": "Qty"},
                    {"target": "unit_cost", "source": "Price"},
                ]
            },
            "quantity_semantics": {"unit": "case", "case_pack_from": "fixed", "fixed_pack_size": 6},
            "price_semantics": {"per": "case", "currency": "USD", **price_changes},
        }
    )


def test_shared_allocation_conserves_stock_and_uses_explicit_priority() -> None:
    pools = (pool("a", 60), pool("b", 60))
    result = allocate_supplier_stock(
        pools, settings(), quantity(), supplier(stock_base_units=90), ()
    )
    assert [row.allocated_units for row in result.rows] == [60, 30]
    assert result.total_allocated_units == 90 and result.remaining_supplier_units == 0
    assert result.status == "PARTIAL" and not result.stock_reserved
    reverse = allocate_supplier_stock(
        pools, settings(pool_priority=["b", "a"]), quantity(), supplier(stock_base_units=90), ()
    )
    assert [(r.pool_id, r.allocated_units) for r in reverse.rows] == [("b", 60), ("a", 30)]
    assert [p.result.suggested_units for p in pools] == [60, 60]  # original replayable ceilings


@pytest.mark.parametrize(
    "stock,expected,remaining",
    [(120, [60, 60], 0), (130, [60, 60], 10), (70, [60, 0], 10), (11, [0, 0], 11), (0, [0, 0], 0)],
)
def test_multiples_and_per_pool_minima_leave_unusable_stock(
    stock: int, expected: list[int], remaining: int
) -> None:
    result = allocate_supplier_stock(
        (pool("a", 60), pool("b", 60)), settings(), quantity(), supplier(stock_base_units=stock), ()
    )
    assert [r.allocated_units for r in result.rows] == expected
    assert result.total_allocated_units == sum(expected)
    assert result.remaining_supplier_units == remaining
    assert sum(expected) + remaining == stock


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_stock",
        "invalid_stock",
        "source",
        "amazon",
        "priority",
        "reference",
        "missing_request",
        "availability",
    ],
)
def test_unproven_evidence_has_no_known_positive_combined_allocation(fault: str) -> None:
    pools = (pool("a", 60), pool("b", 60))
    policy = settings()
    source = supplier(stock_base_units=90)
    blocks: tuple[str, ...] = ()
    if fault == "unknown_stock":
        source = source.model_copy(update={"stock_base_units": None})
    if fault == "invalid_stock":
        source = source.model_copy(update={"stock_base_units": True})
    if fault == "source":
        source = source.model_copy(update={"status": "BLOCKED"})
    if fault == "availability":
        source = source.model_copy(update={"availability": "UNKNOWN"})
    if fault == "amazon":
        blocks = ("AMAZON_INCOMPLETE",)
    if fault == "priority":
        policy = settings(pool_priority=["a"])
    if fault == "reference":
        policy = settings(allocation_reference=None)
    if fault == "missing_request":
        bad = pools[0].inputs.model_copy(update={"sales_units": None})
        pools = (pools[0].model_copy(update={"result": calculate_replenishment(bad)}), pools[1])
    result = allocate_supplier_stock(pools, policy, quantity(), source, blocks)
    assert result.total_allocated_units is None
    assert all(r.allocated_units is None for r in result.rows)


def test_zero_demand_does_not_require_invented_supplier_stock() -> None:
    result = allocate_supplier_stock(
        (pool("a", 0), pool("b", 0)),
        settings(),
        quantity(),
        supplier(stock_base_units=None, availability="UNKNOWN"),
        (),
    )
    assert result.total_allocated_units == 0 and result.remaining_supplier_units is None
    assert result.status == "COMPLETE"


@pytest.mark.parametrize("field", list(settings().model_dump()))
def test_commercial_decisions_are_explicit(field: str) -> None:
    data = settings().model_dump()
    data.pop(field)
    with pytest.raises(ValidationError):
        CommercialPolicy.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"base_units_per_price_unit": True},
        {"base_units_per_price_unit": 1.5},
        {"base_units_per_price_unit": 0},
        {"pool_priority": ["a", "a"]},
        {"currency": "CAD"},
        {"minimum_quantity_scope": "ENTIRE_BASKET"},
    ],
)
def test_unsupported_assumptions_and_coercions_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        settings(**change)


def test_absent_commercial_settings_preserve_legacy_policy_bytes() -> None:
    data = definition()
    first = PurchasingPolicyDefinition.model_validate(data)
    second = PurchasingPolicyDefinition.model_validate({**data, "commercial": None})
    assert definition_snapshot(first) == {**data, "rule_version": "purchasing-policy-v1"}
    assert digest(definition_snapshot(first)) == digest(definition_snapshot(second))
    with pytest.raises(ValidationError):
        PurchasingPolicyDefinition.model_validate({**data, "commercial": settings().model_dump()})
    assert (
        PurchasingPolicyDefinition.model_validate(
            {**data, "supplier": supplier_settings(), "commercial": settings().model_dump()}
        ).commercial
        is not None
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("factor", "COMMERCIAL_PRICE_UNITS_UNCONFIRMED"),
        ("multiple", "COMMERCIAL_PRICE_UNITS_CONFLICT"),
        ("priority", "COMMERCIAL_POOL_PRIORITY_UNCONFIRMED"),
        ("scope", "COMMERCIAL_MINIMUM_SCOPE_UNCONFIRMED"),
        ("currency", "COMMERCIAL_CURRENCY_CONFLICT"),
        ("column", "COMMERCIAL_PRICE_COLUMN_UNCONFIRMED"),
    ],
)
def test_manager_review_refuses_unproven_price_and_allocation_contracts(
    fault: str, code: str
) -> None:
    policy = settings()
    profile = rules().as_columns()
    if fault == "factor":
        policy = settings(base_units_per_price_unit=None)
    if fault == "multiple":
        policy = settings(base_units_per_price_unit=4)
    if fault == "priority":
        policy = settings(pool_priority=[])
    if fault == "scope":
        policy = settings(minimum_confirmation_reference=None)
    if fault == "currency":
        profile["price_semantics"]["currency"] = "CAD"
    if fault == "column":
        profile["column_map"]["columns"] = []
    assert code in commercial_policy_blocks(
        policy, {"profile": profile, "vendor_constraints": {"currency": "USD"}}, ("a", "b"), 6
    )


def test_current_price_replays_discount_once_and_preserves_source_tax_basis() -> None:
    result = verified_price(
        {"unit_cost": "88", "currency": "USD", "issues": []},
        {"unit_cost": "100"},
        Decimal(88),
        "USD",
        VendorInventorySnapshot(unit_cost=Decimal(88), currency="USD"),
        rules(discount_percent=12, includes_tax=True).as_columns(),
        settings(),
        {},
    )
    assert result.status == "VERIFIED" and result.reported_price == 88
    assert result.evidence["price_semantics"]["includes_tax"] is True
    assert result.evidence["price_semantics"]["discount_percent"] == "12"


@pytest.mark.parametrize(
    "fault", ["missing", "raw", "float", "row", "snapshot", "currency", "case", "warning", "nan"]
)
def test_bad_price_never_becomes_a_known_cost(fault: str) -> None:
    normalized: dict[str, Any] = {"unit_cost": "12", "currency": "USD", "issues": []}
    raw: dict[str, Any] = {"unit_cost": "12"}
    staged = Decimal(12)
    stock = VendorInventorySnapshot(unit_cost=Decimal(12), currency="USD")
    policy = settings()
    if fault == "missing":
        normalized["unit_cost"] = None
    if fault == "raw":
        raw = {}
    if fault == "float":
        normalized["unit_cost"] = 12.0
    if fault == "row":
        staged = Decimal(13)
    if fault == "snapshot":
        stock.unit_cost = Decimal(13)
    if fault == "currency":
        stock.currency = "CAD"
    if fault == "case":
        policy = settings(base_units_per_price_unit=1)
    if fault == "warning":
        normalized["issues"] = [{"field": "unit_cost", "code": "PRICE_INVALID"}]
    if fault == "nan":
        normalized["unit_cost"] = "NaN"
    result = verified_price(normalized, raw, staged, "USD", stock, rules().as_columns(), policy, {})
    assert result.status == "UNAVAILABLE" and result.reported_price is None


@pytest.mark.parametrize(
    "minimum_qty,minimum_value,scope,expected",
    [
        (15, "185.1840", True, "ASSESSED"),
        (16, "185.1840", True, "CONSTRAINT_NOT_MET"),
        (15, "185.1841", True, "CONSTRAINT_NOT_MET"),
        (0, "0", True, "ASSESSED"),
        (None, None, True, "INCOMPLETE"),
        (0, "0", False, "INCOMPLETE"),
    ],
)
def test_exact_minimum_boundaries_and_unknown_are_distinct(
    minimum_qty: int | None, minimum_value: str | None, scope: bool, expected: str
) -> None:
    policy = settings(
        minimum_quantity_scope="THIS_SKU_SUPPLIER_ORDER_UNITS" if scope else None,
        minimum_value_scope="THIS_SKU_GOODS_LINE_AT_REPORTED_PRICE" if scope else None,
    )
    source = supplier(
        stock_base_units=90,
        evidence={
            "vendor_constraints": {
                "minimum_order_quantity": minimum_qty,
                "minimum_order_value": minimum_value,
            }
        },
    )
    allocation = allocate_supplier_stock(
        (pool("a", 60), pool("b", 60)), policy, quantity(), source, ()
    )
    result = assess_commercials(policy, allocation, quote(), source, 6)
    assert result.goods_line_value == Decimal("185.1840") and result.status == expected
    assert result.supplier_order_units == result.price_units == 15
    assert not result.purchasing_eligible and not result.landed_cost_verified


def test_huge_integer_money_is_independent_of_ambient_decimal_precision() -> None:
    q = quantity(minimum_order_units=0, order_multiple_units=1)
    total = 10**90 + 7
    source = supplier(
        stock_base_units=total,
        evidence={"vendor_constraints": {"minimum_order_quantity": 0, "minimum_order_value": "0"}},
    )
    policy = settings(base_units_per_price_unit=1, pool_priority=["a"])
    allocation = allocate_supplier_stock((pool("a", total, q),), policy, q, source, ())
    with localcontext() as ctx:
        ctx.prec = 2
        result = assess_commercials(
            policy,
            allocation,
            quote(reported_price="1.2345", base_units_per_price_unit=1),
            source,
            1,
        )
    with localcontext() as ctx:
        ctx.prec = 140
        assert result.goods_line_value == Decimal("1.2345") * total
    assert allocation.total_allocated_units == total


def test_zero_price_is_visible_but_never_silently_trusted_as_free() -> None:
    source = supplier(
        stock_base_units=90,
        evidence={"vendor_constraints": {"minimum_order_quantity": 0, "minimum_order_value": "0"}},
    )
    allocation = allocate_supplier_stock(
        (pool("a", 60), pool("b", 60)), settings(), quantity(), source, ()
    )
    result = assess_commercials(settings(), allocation, quote(reported_price="0"), source, 6)
    assert result.goods_line_value == 0
    assert "ZERO_SUPPLIER_PRICE_REQUIRES_REVIEW" in result.review_reasons
