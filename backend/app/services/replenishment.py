"""Pure quantity previews (ADR 0022). No database, network, writes or profit guesses.

The live inventory adapter is deliberately not wired yet: it must normalize
packs and shared stock pools, establish freshness and preserve source evidence.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from app.schemas.replenishment import (
    QuantityBreakdown,
    Reason,
    ReplenishmentPreviewInput,
    ReplenishmentPreviewResult,
)


def calculate_replenishment(inputs: ReplenishmentPreviewInput) -> ReplenishmentPreviewResult:
    """Evaluate one explicit inventory pool against one vendor's purchasing policy.

    Lead time and safety are additive to target coverage. This versioned preview
    interpretation needs purchasing-manager approval before live integration.
    All quantities are confirmed base units; unknown inventory never becomes 0.
    """
    policy = inputs.policy
    coverage = policy.target_days + policy.lead_time_days + policy.safety_days
    blocked: list[Reason] = []
    if not inputs.mapping_approved:
        blocked.append("MAPPING_UNAPPROVED")
    if not inputs.units_confirmed:
        blocked.append("UNIT_CONVERSION_UNCONFIRMED")
    if not inputs.source_data_current:
        blocked.append("SOURCE_DATA_STALE")
    if inputs.sales_units is None:
        blocked.append("SALES_UNITS_MISSING")
    if inputs.on_hand_units is None:
        blocked.append("ON_HAND_MISSING")
    if inputs.inbound_units is None:
        blocked.append("INBOUND_MISSING")
    if blocked:
        return ReplenishmentPreviewResult(
            status="BLOCKED",
            reasons=tuple(blocked),
            calculation=QuantityBreakdown(coverage_days=coverage),
            suggested_units=None,
        )

    sold, on_hand, inbound = inputs.sales_units, inputs.on_hand_units, inputs.inbound_units
    assert sold is not None and on_hand is not None and inbound is not None
    window = policy.velocity_window_days
    # Exact integer ceilings avoid a rounded daily rate changing order quantities.
    required = (sold * coverage + window - 1) // window
    shortfall = max(0, required - on_hand - inbound)
    multiple = policy.order_multiple_units
    order = (
        ((max(shortfall, policy.minimum_order_units) + multiple - 1) // multiple) * multiple
        if shortfall
        else 0
    )
    # Display ratios use an explicit context, independent of ambient Decimal state.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        daily_sales = Decimal(sold) / Decimal(window)
        days_of_supply = Decimal(on_hand) * Decimal(window) / Decimal(sold) if sold else None
    calculation = QuantityBreakdown(
        coverage_days=coverage,
        daily_sales=daily_sales,
        days_of_supply=days_of_supply,
        required_units=required,
        net_shortfall_units=shortfall,
        order_quantity_before_vendor_cap=order,
    )
    if not shortfall:
        return ReplenishmentPreviewResult(
            status="NO_REORDER",
            reasons=("NO_SHORTFALL",),
            calculation=calculation,
            suggested_units=0,
        )
    if inputs.vendor_availability != "IN_STOCK":
        reason: Reason = (
            "VENDOR_OUT_OF_STOCK"
            if inputs.vendor_availability == "OUT_OF_STOCK"
            else "VENDOR_AVAILABILITY_UNKNOWN"
        )
        return ReplenishmentPreviewResult(
            status="BLOCKED", reasons=(reason,), calculation=calculation, suggested_units=None
        )
    stock = inputs.vendor_stock_units
    if stock is None:
        return ReplenishmentPreviewResult(
            status="REVIEW_REQUIRED",
            reasons=("VENDOR_QUANTITY_UNKNOWN",),
            calculation=calculation,
            suggested_units=order,
        )
    available_order = min(order, (stock // multiple) * multiple)
    if available_order < policy.minimum_order_units or available_order == 0:
        reason = (
            "VENDOR_STOCK_BELOW_MINIMUM"
            if available_order < policy.minimum_order_units
            else "VENDOR_STOCK_UNORDERABLE"
        )
        return ReplenishmentPreviewResult(
            status="BLOCKED", reasons=(reason,), calculation=calculation, suggested_units=None
        )
    partial = available_order < order
    return ReplenishmentPreviewResult(
        status="REVIEW_REQUIRED" if partial else "READY",
        reasons=("PARTIAL_VENDOR_STOCK",) if partial else (),
        calculation=calculation,
        suggested_units=available_order,
    )
