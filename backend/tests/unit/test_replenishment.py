"""Milestone 2 formula, readiness and vendor constraints (ADR 0022)."""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal, localcontext
from io import StringIO

import pytest
from pydantic import ValidationError

from app.cli.replenishment_preview import MAX_INPUT_CHARACTERS, main
from app.schemas.replenishment import ReplenishmentPolicy, ReplenishmentPreviewInput
from app.services.replenishment import calculate_replenishment


def policy(**overrides: object) -> ReplenishmentPolicy:
    values: dict[str, object] = {
        "velocity_window_days": 14,
        "target_days": 14,
        "lead_time_days": 0,
        "safety_days": 0,
        "minimum_order_units": 0,
        "order_multiple_units": 1,
    }
    values.update(overrides)
    return ReplenishmentPolicy.model_validate(values)


def inputs(**overrides: object) -> ReplenishmentPreviewInput:
    values: dict[str, object] = {
        "policy": policy(),
        "sales_units": 280,
        "on_hand_units": 50,
        "inbound_units": 20,
        "vendor_availability": "IN_STOCK",
        "vendor_stock_units": 1000,
        "mapping_approved": True,
        "units_confirmed": True,
        "source_data_current": True,
    }
    values.update(overrides)
    return ReplenishmentPreviewInput.model_validate(values)


def test_client_worked_example() -> None:
    result = calculate_replenishment(inputs())
    assert result.status == "READY"
    assert result.reasons == ()
    assert result.calculation.coverage_days == 14
    assert result.calculation.daily_sales == Decimal(20)
    assert result.calculation.days_of_supply == Decimal("2.5")
    assert result.calculation.required_units == 280
    assert result.calculation.net_shortfall_units == 210
    assert result.suggested_units == 210


def test_additive_lead_and_safety_are_visible() -> None:
    result = calculate_replenishment(inputs(policy=policy(lead_time_days=2, safety_days=1)))
    assert result.calculation.coverage_policy == "target-plus-lead-plus-safety"
    assert result.calculation.coverage_days == 17
    assert result.calculation.required_units == 340
    assert result.suggested_units == 270


@pytest.mark.parametrize("window", [7, 14, 30])
def test_required_units_use_exact_rational_ceiling(window: int) -> None:
    result = calculate_replenishment(
        inputs(
            sales_units=1,
            on_hand_units=0,
            inbound_units=0,
            policy=policy(velocity_window_days=window, target_days=window + 1),
        )
    )
    assert result.calculation.required_units == 2
    assert result.suggested_units == 2


@pytest.mark.parametrize("on_hand,inbound", [(300, 0), (0, 300), (50, 230)])
def test_fully_covered_inventory_never_triggers_moq(on_hand: int, inbound: int) -> None:
    result = calculate_replenishment(
        inputs(
            on_hand_units=on_hand,
            inbound_units=inbound,
            policy=policy(minimum_order_units=100, order_multiple_units=12),
            vendor_availability="UNKNOWN",
            vendor_stock_units=None,
        )
    )
    assert result.status == "NO_REORDER"
    assert result.suggested_units == 0
    assert result.calculation.order_quantity_before_vendor_cap == 0


def test_zero_demand_has_no_runway_or_reorder() -> None:
    result = calculate_replenishment(inputs(sales_units=0, policy=policy(minimum_order_units=100)))
    assert result.status == "NO_REORDER"
    assert result.calculation.daily_sales == 0
    assert result.calculation.days_of_supply is None
    assert result.suggested_units == 0


def test_moq_and_order_multiple_round_up_together() -> None:
    result = calculate_replenishment(
        inputs(
            sales_units=10,
            on_hand_units=0,
            inbound_units=0,
            policy=policy(minimum_order_units=100, order_multiple_units=12),
        )
    )
    assert result.calculation.net_shortfall_units == 10
    assert result.suggested_units == 108


def test_known_vendor_limit_rounds_down_without_exceeding_stock() -> None:
    result = calculate_replenishment(
        inputs(vendor_stock_units=215, policy=policy(order_multiple_units=12))
    )
    assert result.calculation.order_quantity_before_vendor_cap == 216
    assert result.suggested_units == 204
    assert result.status == "REVIEW_REQUIRED"
    assert result.reasons == ("PARTIAL_VENDOR_STOCK",)


def test_vendor_cap_cannot_violate_moq() -> None:
    result = calculate_replenishment(
        inputs(
            vendor_stock_units=104, policy=policy(minimum_order_units=100, order_multiple_units=12)
        )
    )
    assert result.status == "BLOCKED"
    assert result.reasons == ("VENDOR_STOCK_BELOW_MINIMUM",)
    assert result.suggested_units is None


@pytest.mark.parametrize("stock", [0, 5, 11])
def test_less_than_one_order_multiple_is_not_orderable(stock: int) -> None:
    result = calculate_replenishment(
        inputs(vendor_stock_units=stock, policy=policy(order_multiple_units=12))
    )
    assert result.status == "BLOCKED"
    assert result.reasons == ("VENDOR_STOCK_UNORDERABLE",)
    assert result.suggested_units is None


def test_in_stock_without_quantity_stays_unknown_and_requires_review() -> None:
    request = inputs(vendor_stock_units=None)
    result = calculate_replenishment(request)
    assert request.vendor_stock_units is None
    assert result.status == "REVIEW_REQUIRED"
    assert result.reasons == ("VENDOR_QUANTITY_UNKNOWN",)
    assert result.suggested_units == 210


@pytest.mark.parametrize(
    "availability,reason",
    [("UNKNOWN", "VENDOR_AVAILABILITY_UNKNOWN"), ("OUT_OF_STOCK", "VENDOR_OUT_OF_STOCK")],
)
def test_unavailable_or_unknown_vendor_blocks_suggestion(availability: str, reason: str) -> None:
    result = calculate_replenishment(inputs(vendor_availability=availability))
    assert result.status == "BLOCKED"
    assert result.reasons == (reason,)
    assert result.suggested_units is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("mapping_approved", False, "MAPPING_UNAPPROVED"),
        ("units_confirmed", False, "UNIT_CONVERSION_UNCONFIRMED"),
        ("source_data_current", False, "SOURCE_DATA_STALE"),
        ("sales_units", None, "SALES_UNITS_MISSING"),
        ("on_hand_units", None, "ON_HAND_MISSING"),
        ("inbound_units", None, "INBOUND_MISSING"),
    ],
)
def test_missing_readiness_or_inventory_never_becomes_zero(
    field: str, value: object, reason: str
) -> None:
    result = calculate_replenishment(inputs(**{field: value}))
    assert result.status == "BLOCKED"
    assert result.reasons == (reason,)
    assert result.suggested_units is None
    assert result.calculation.required_units is None


def test_all_blocking_reasons_are_returned_in_stable_order() -> None:
    result = calculate_replenishment(
        inputs(
            mapping_approved=False,
            units_confirmed=False,
            source_data_current=False,
            sales_units=None,
            on_hand_units=None,
            inbound_units=None,
        )
    )
    assert result.reasons == (
        "MAPPING_UNAPPROVED",
        "UNIT_CONVERSION_UNCONFIRMED",
        "SOURCE_DATA_STALE",
        "SALES_UNITS_MISSING",
        "ON_HAND_MISSING",
        "INBOUND_MISSING",
    )


@pytest.mark.parametrize(
    "field", ["sales_units", "on_hand_units", "inbound_units", "vendor_stock_units"]
)
@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_quantities_require_nonnegative_whole_units(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        inputs(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_days", 0),
        ("target_days", 1.2),
        ("lead_time_days", -1),
        ("safety_days", -1),
        ("minimum_order_units", -1),
        ("order_multiple_units", 0),
        ("order_multiple_units", True),
        ("velocity_window_days", 10),
        ("velocity_window_days", 7.0),
        ("lead_time_days", 3651),
    ],
)
def test_invalid_policies_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        policy(**{field: value})


def test_policy_is_required_and_unknown_fields_are_not_ignored() -> None:
    payload = inputs().model_dump()
    payload["policy"] = {"velocity_window_days": 14}
    with pytest.raises(ValidationError):
        ReplenishmentPreviewInput.model_validate(payload)
    with pytest.raises(ValidationError):
        inputs(unknown_cost=0)
    with pytest.raises(ValidationError):
        inputs(units_confirmed="true")


def test_input_is_unchanged_and_result_repeatable_across_decimal_contexts() -> None:
    request = inputs(sales_units=83, policy=policy(velocity_window_days=7))
    before = request.model_dump_json()
    expected = calculate_replenishment(request).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        assert calculate_replenishment(request).model_dump_json() == expected
    assert request.model_dump_json() == before


def test_preview_obeys_vendor_moq_and_multiple_invariants() -> None:
    for minimum in (0, 10, 100):
        for multiple in (1, 6, 12):
            for stock in (0, 5, 10, 101, 215, 1000):
                result = calculate_replenishment(
                    inputs(
                        vendor_stock_units=stock,
                        policy=policy(minimum_order_units=minimum, order_multiple_units=multiple),
                    )
                )
                if result.suggested_units is not None:
                    assert 0 < result.suggested_units <= stock
                    assert result.suggested_units >= minimum
                    assert result.suggested_units % multiple == 0


def test_three_vendor_scenarios_do_not_treat_unknown_stock_as_zero() -> None:
    results = [
        calculate_replenishment(inputs(vendor_stock_units=stock)) for stock in (1000, 150, None)
    ]
    assert [result.suggested_units for result in results] == [210, 150, 210]
    assert [result.status for result in results] == ["READY", "REVIEW_REQUIRED", "REVIEW_REQUIRED"]


def test_cli_outputs_reproducible_explicit_input_preview() -> None:
    output, errors = StringIO(), StringIO()
    request = inputs()
    assert main(stdin=StringIO(request.model_dump_json()), stdout=output, stderr=errors) == 0
    payload = json.loads(output.getvalue())
    assert payload["preview_only"] is True
    assert payload["data_source"] == "explicit_inputs"
    assert payload["request"] == request.model_dump(mode="json")
    assert payload["result"]["suggested_units"] == 210
    assert payload["result"]["calculation"]["daily_sales"] == "20"
    assert errors.getvalue() == ""
    again = StringIO()
    assert main(stdin=StringIO(request.model_dump_json()), stdout=again, stderr=errors) == 0
    assert again.getvalue() == output.getvalue()


@pytest.mark.parametrize("raw", ["", "not JSON", "{}", '{"private_input":"never-echo-this"}'])
def test_cli_rejects_bad_payload_without_echoing_input(raw: str) -> None:
    output, errors = StringIO(), StringIO()
    assert main(stdin=StringIO(raw), stdout=output, stderr=errors) == 2
    assert output.getvalue() == ""
    assert "never-echo-this" not in errors.getvalue()
    assert "Invalid preview JSON" in errors.getvalue()


def test_cli_bounds_input_size() -> None:
    output, errors = StringIO(), StringIO()
    assert main(stdin=StringIO(" " * (MAX_INPUT_CHARACTERS + 1)), stdout=output, stderr=errors) == 2
    assert output.getvalue() == ""
    assert "size limit" in errors.getvalue()
