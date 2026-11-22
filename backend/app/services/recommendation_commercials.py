"""Exact single-SKU goods checks and explicit pool allocation, never reservations."""

from __future__ import annotations

import uuid
from contextlib import suppress
from decimal import Context, Decimal, localcontext
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.imports.extract import ColumnPlan, ResolvedColumn, extract_row
from app.imports.profile_rules import ProfileRules
from app.models import VendorInventorySnapshot
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.repositories.supplier_readiness import SupplierReadinessRepository
from app.schemas.commercials import (
    AllocationRow,
    CommercialAssessment,
    CommercialPolicy,
    CurrentSupplierPrice,
    MinimumCheck,
    SharedStockAllocation,
)
from app.schemas.recommendations import PoolEvaluation
from app.schemas.replenishment import ReplenishmentPolicy
from app.schemas.supplier_readiness import SupplierReadinessResponse


def allocate_supplier_stock(
    pools: tuple[PoolEvaluation, ...],
    policy: CommercialPolicy,
    quantity: ReplenishmentPolicy,
    supplier: SupplierReadinessResponse,
    blocking_reasons: tuple[str, ...],
) -> SharedStockAllocation:
    by_id = {pool.pool_id: pool for pool in pools}
    reasons = set(blocking_reasons)
    priority = policy.pool_priority
    if (
        len(by_id) != len(pools)
        or len(set(priority)) != len(priority)
        or set(priority) != set(by_id)
        or not priority
        or policy.allocation_reference is None
    ):
        reasons.add("COMMERCIAL_POOL_PRIORITY_UNCONFIRMED")
    if supplier.status != "VERIFIED":
        reasons.add("SUPPLIER_INPUTS_NOT_VERIFIED")
    if any(pool.result.calculation.order_quantity_before_vendor_cap is None for pool in pools):
        reasons.add("POOL_REQUEST_UNKNOWN")
    requested = [pool.result.calculation.order_quantity_before_vendor_cap for pool in pools]
    no_demand = bool(requested) and all(value == 0 for value in requested)
    if not no_demand and supplier.availability != "IN_STOCK":
        reasons.add("SUPPLIER_NOT_IN_STOCK")
    stock = supplier.stock_base_units
    if stock is not None and (type(stock) is not int or stock < 0):
        stock = None
        reasons.add("SUPPLIER_STOCK_INVALID")
    unconfirmed = stock is None and not no_demand and not reasons
    if unconfirmed:
        reasons.add("SHARED_SUPPLIER_STOCK_UNKNOWN")
    valid = not reasons
    remaining = stock
    rows: list[AllocationRow] = []
    for i, pool_id in enumerate(priority):
        pool = by_id.get(pool_id)
        if pool is None:
            continue
        request = pool.result.calculation.order_quantity_before_vendor_cap
        allocated = None
        notes: set[str] = set()
        if valid and request is not None:
            if request == 0:
                allocated = 0
            else:
                assert remaining is not None
                allocated = min(
                    request,
                    remaining // quantity.order_multiple_units * quantity.order_multiple_units,
                )
                if 0 < allocated < quantity.minimum_order_units:
                    allocated = 0
                    notes.add("REMAINING_STOCK_BELOW_POOL_MINIMUM")
                remaining -= allocated
                if allocated < request:
                    notes.add("SHARED_STOCK_SHORTFALL")
        rows.append(
            AllocationRow(
                pool_id=pool_id,
                priority=i + 1,
                requested_units=request,
                allocated_units=allocated,
                remaining_supplier_units=remaining if valid else None,
                reasons=tuple(sorted(notes)),
            )
        )
    total = sum(row.allocated_units or 0 for row in rows) if valid else None
    partial = any(row.allocated_units != row.requested_units for row in rows) if valid else False
    return SharedStockAllocation(
        status="UNCONFIRMED"
        if unconfirmed
        else "BLOCKED"
        if not valid
        else "PARTIAL"
        if partial
        else "COMPLETE",
        supplier_stock_units=stock,
        total_allocated_units=total,
        remaining_supplier_units=remaining if valid else None,
        rows=tuple(rows),
        reasons=tuple(sorted(reasons)),
    )


def verified_price(
    normalized: dict[str, Any],
    raw: dict[str, Any],
    row_price: Decimal | None,
    row_currency: str | None,
    snapshot: VendorInventorySnapshot,
    profile: dict[str, Any],
    policy: CommercialPolicy,
    evidence: dict[str, Any],
) -> CurrentSupplierPrice:
    issues: set[str] = set()
    rules = None
    parsed = None
    try:
        rules = ProfileRules.from_columns(
            **{
                key: profile[key]
                for key in (
                    "column_map",
                    "normalization_rules",
                    "availability_rules",
                    "quantity_semantics",
                    "price_semantics",
                    "pack_size_handling",
                )
            }
        )
        raw_price = raw.get("unit_cost")
        if isinstance(raw_price, str):
            plan = ColumnPlan(
                [ResolvedColumn("unit_cost", "unit_cost", 0, "unit_cost", True)], [], [], None
            )
            with localcontext(Context(prec=80)):
                replay = extract_row(rules, plan, 1, [raw_price])
            parsed = replay.unit_cost
        else:
            issues.add("SUPPLIER_RAW_PRICE_UNCONFIRMED")
    except (KeyError, TypeError, ValueError, ValidationError):
        issues.add("SUPPLIER_PRICE_PROFILE_INVALID")
    value = normalized.get("unit_cost")
    if not isinstance(value, str):
        price = None
    else:
        try:
            price = Decimal(value)
        except ArithmeticError:
            price = None
    if price is None or not price.is_finite() or price < 0:
        price = None
        issues.add("SUPPLIER_PRICE_MISSING_OR_INVALID")
    if price is not None and (price != row_price or price != snapshot.unit_cost or price != parsed):
        issues.add("SUPPLIER_PRICE_PROVENANCE_CONFLICT")
    currency = normalized.get("currency")
    if (
        currency != policy.currency
        or row_currency != policy.currency
        or snapshot.currency != policy.currency
        or rules is None
        or rules.price_semantics.currency != policy.currency
    ):
        issues.add("SUPPLIER_PRICE_CURRENCY_CONFLICT")
    row_issues = normalized.get("issues")
    if not isinstance(row_issues, list) or any(
        not isinstance(issue, dict)
        or issue.get("field") in {"unit_cost", "currency", "price", "pack_size"}
        for issue in row_issues
    ):
        issues.add("SUPPLIER_PRICE_WARNINGS")
    factor = policy.base_units_per_price_unit
    if factor is None or policy.price_confirmation_reference is None:
        issues.add("COMMERCIAL_PRICE_UNITS_UNCONFIRMED")
    if rules is not None:
        evidence = {
            **evidence,
            "price_semantics": rules.price_semantics.model_dump(mode="json"),
            "raw_reported_price": raw.get("unit_cost"),
            "normalized_reported_price": value,
            "snapshot_price": str(snapshot.unit_cost) if snapshot.unit_cost is not None else None,
        }
        if rules.price_semantics.per == "case":
            pack = (
                rules.quantity_semantics.fixed_pack_size
                if rules.quantity_semantics.case_pack_from == "fixed"
                else normalized.get("pack_size")
            )
            if type(pack) is not int or pack != factor:
                issues.add("SUPPLIER_PRICE_CASE_FACTOR_CONFLICT")
    return CurrentSupplierPrice(
        status="UNAVAILABLE" if issues else "VERIFIED",
        reported_price=price if not issues else None,
        currency=policy.currency if not issues else None,
        base_units_per_price_unit=factor,
        evidence=evidence,
        issues=tuple(sorted(issues)),
    )


def current_supplier_price(
    session: Session,
    organization_id: uuid.UUID,
    vendor_product_id: uuid.UUID,
    supplier: SupplierReadinessResponse,
    policy: CommercialPolicy,
) -> CurrentSupplierPrice:
    evidence = supplier.evidence

    def unavailable(code: str) -> CurrentSupplierPrice:
        return CurrentSupplierPrice(
            status="UNAVAILABLE",
            reported_price=None,
            currency=None,
            base_units_per_price_unit=policy.base_units_per_price_unit,
            evidence={},
            issues=(code,),
        )

    if supplier.status != "VERIFIED":
        return unavailable("SUPPLIER_INPUTS_NOT_VERIFIED")
    from app.models import VendorProduct

    identity = PurchasingPolicyRepository(session, organization_id).owned(
        VendorProduct, vendor_product_id
    )
    if identity is None:
        return unavailable("SUPPLIER_IDENTITY_UNCONFIRMED")
    try:
        job_id = uuid.UUID(evidence["job"]["id"])
    except (KeyError, ValueError, TypeError):
        return unavailable("SUPPLIER_PRICE_SOURCE_UNCONFIRMED")
    repository = SupplierReadinessRepository(session, organization_id)
    rows, stocks = repository.rows(job_id, identity), repository.snapshots(job_id, identity.id)
    if (
        len(rows) != 1
        or len(stocks) != 1
        or str(rows[0].id) != evidence.get("row", {}).get("id")
        or str(stocks[0].id) != evidence.get("snapshot", {}).get("id")
    ):
        return unavailable("SUPPLIER_PRICE_SOURCE_CHANGED")
    row, stock = rows[0], stocks[0]
    return verified_price(
        row.normalized_data,
        row.raw_data,
        row.unit_cost,
        row.currency,
        stock,
        evidence.get("profile", {}),
        policy,
        {
            "job_id": str(job_id),
            "row_id": str(row.id),
            "snapshot_id": str(stock.id),
            "file": evidence.get("file"),
        },
    )


def assess_commercials(
    policy: CommercialPolicy,
    allocation: SharedStockAllocation,
    price: CurrentSupplierPrice,
    supplier: SupplierReadinessResponse,
    order_factor: int | None,
) -> CommercialAssessment:
    total = allocation.total_allocated_units
    reasons = {
        "SUPPLIER_STOCK_NOT_RESERVED",
        "LANDED_COST_NOT_EVALUATED",
        *price.issues,
        *allocation.reasons,
    }
    if allocation.status == "PARTIAL":
        reasons.add("SHARED_STOCK_SHORTFALL")
    supplier_units = (
        total // order_factor
        if total is not None and order_factor and total % order_factor == 0
        else None
    )
    price_factor = policy.base_units_per_price_unit
    price_units = (
        total // price_factor
        if total is not None and price_factor and total % price_factor == 0
        else None
    )
    value = None
    if price.status == "VERIFIED" and price.reported_price is not None and price_units is not None:
        # Source Numeric(14,4), arbitrary-size integer quantity: explicit enough
        # precision for the complete integer product, independent of ambient state.
        with localcontext(Context(prec=max(80, len(str(price_units)) + 40))):
            value = price.reported_price * price_units
        if price.reported_price == 0:
            reasons.add("ZERO_SUPPLIER_PRICE_REQUIRES_REVIEW")
    if total is not None and price_units is None:
        reasons.add("ALLOCATION_PRICE_UNITS_CONFLICT")
    if supplier_units is None:
        reasons.add("ALLOCATION_ORDER_UNITS_UNCONFIRMED")
    constraints = supplier.evidence.get("vendor_constraints", {})
    checks: list[MinimumCheck] = []
    for name, key, scope, observed in (
        (
            "SUPPLIER_ORDER_UNITS",
            "minimum_order_quantity",
            policy.minimum_quantity_scope,
            Decimal(supplier_units) if supplier_units is not None else None,
        ),
        ("GOODS_LINE_VALUE", "minimum_order_value", policy.minimum_value_scope, value),
    ):
        raw = constraints.get(key)
        minimum = None
        if isinstance(raw, (str, int, Decimal)) and not isinstance(raw, bool):
            with suppress(ArithmeticError):
                minimum = Decimal(raw)
        if minimum is not None and (not minimum.is_finite() or minimum < 0):
            minimum = None
        status = (
            "NO_ALLOCATION"
            if total == 0
            else "UNCONFIRMED"
            if minimum is None
            or scope is None
            or policy.minimum_confirmation_reference is None
            or observed is None
            else "MET"
            if observed >= minimum
            else "NOT_MET"
        )
        checks.append(
            MinimumCheck.model_validate(
                {
                    "name": name,
                    "scope": scope,
                    "minimum": minimum,
                    "observed": observed,
                    "status": status,
                }
            )
        )
        if status == "NOT_MET":
            reasons.add("SUPPLIER_" + name + "_MINIMUM_NOT_MET")
        if status == "UNCONFIRMED":
            reasons.add("SUPPLIER_" + name + "_MINIMUM_UNCONFIRMED")
    if policy.terms_review_reference is None:
        reasons.add("SUPPLIER_TERMS_REVIEW_UNCONFIRMED")
    incomplete = (
        total is None
        or price.status != "VERIFIED"
        or any(c.status == "UNCONFIRMED" for c in checks)
        or policy.terms_review_reference is None
        or supplier_units is None
        or price_units is None
    )
    return CommercialAssessment(
        policy=policy,
        price=price,
        goods_line_value=value,
        currency=policy.currency,
        supplier_order_units=supplier_units,
        price_units=price_units,
        checks=tuple(checks),
        status="CONSTRAINT_NOT_MET"
        if any(c.status == "NOT_MET" for c in checks)
        else "INCOMPLETE"
        if incomplete
        else "ASSESSED",
        review_reasons=tuple(sorted(reasons)),
    )
