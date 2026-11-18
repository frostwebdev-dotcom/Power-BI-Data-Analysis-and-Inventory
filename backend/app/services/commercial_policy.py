"""Validate explicit commercial assumptions without deriving client defaults."""

from __future__ import annotations

from typing import Any

from app.schemas.commercials import CommercialPolicy


def commercial_policy_blocks(
    policy: CommercialPolicy,
    supplier_context: dict[str, Any],
    pool_ids: tuple[str, ...],
    order_multiple: int,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    profile = supplier_context.get("profile") or {}
    if policy.base_units_per_price_unit is None or policy.price_confirmation_reference is None:
        reasons.add("COMMERCIAL_PRICE_UNITS_UNCONFIRMED")
    elif order_multiple % policy.base_units_per_price_unit:
        reasons.add("COMMERCIAL_PRICE_UNITS_CONFLICT")
    if (
        not policy.pool_priority
        or set(policy.pool_priority) != set(pool_ids)
        or policy.allocation_reference is None
    ):
        reasons.add("COMMERCIAL_POOL_PRIORITY_UNCONFIRMED")
    if (
        policy.minimum_quantity_scope or policy.minimum_value_scope
    ) and policy.minimum_confirmation_reference is None:
        reasons.add("COMMERCIAL_MINIMUM_SCOPE_UNCONFIRMED")
    constraints = supplier_context.get("vendor_constraints") or {}
    if (
        constraints.get("currency") != policy.currency
        or profile.get("price_semantics", {}).get("currency") != policy.currency
    ):
        reasons.add("COMMERCIAL_CURRENCY_CONFLICT")
    columns = profile.get("column_map", {}).get("columns", [])
    if not any(column.get("target") == "unit_cost" for column in columns):
        reasons.add("COMMERCIAL_PRICE_COLUMN_UNCONFIRMED")
    return tuple(sorted(reasons))
