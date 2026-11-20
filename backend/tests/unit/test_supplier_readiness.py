"""Explicit supplier settings, legacy bytes and reviewed quantity projections."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.purchasing_policies import PurchasingPolicyDefinition
from app.schemas.recommendations import RecommendationResponse, SupplierRecommendationSnapshot
from app.schemas.supplier_readiness import SupplierPolicy, SupplierReadinessResponse
from app.services.purchasing_policies import definition_snapshot, digest
from app.services.recommendations import build_supplier_snapshot, evidence_digest
from app.services.replenishment import calculate_replenishment
from app.services.supplier_readiness import availability_from_profile, whole_quantity
from tests.unit.test_purchasing_policies import definition
from tests.unit.test_recommendations import NOW, approved, readiness


def supplier_settings() -> dict[str, Any]:
    return {
        "profile_id": str(uuid.uuid4()),
        "inventory_max_age_seconds": 3600,
        "freshness_basis": "FILE_RECEIPT_TIME",
        "freshness_confirmation_reference": "Synthetic manager accepts receipt-age proxy",
        "base_units_per_source_quantity": 6,
        "quantity_confirmation_reference": "Synthetic source case has six base units",
        "availability_confirmation_reference": "Synthetic supplier confirms listed availability",
    }


def supplier(**changes: Any) -> SupplierReadinessResponse:
    return SupplierReadinessResponse.model_validate(
        {
            "evaluated_at": NOW,
            "status": "VERIFIED",
            "availability": "IN_STOCK",
            "stock_base_units": 600,
            "issues": (),
            "review_reasons": (),
            "evidence": {},
            **changes,
        }
    )


def test_old_policy_definitions_keep_identical_bytes_and_digests() -> None:
    data = definition()
    legacy = PurchasingPolicyDefinition.model_validate(data)
    explicit_null = PurchasingPolicyDefinition.model_validate({**data, "supplier": None})
    assert "supplier" not in legacy.model_dump(mode="json")
    assert digest(definition_snapshot(legacy)) == digest(definition_snapshot(explicit_null))
    saved = definition_snapshot(legacy)
    assert digest(definition_snapshot(PurchasingPolicyDefinition.model_validate(saved))) == digest(
        saved
    )


@pytest.mark.parametrize("field", list(supplier_settings()))
def test_supplier_choices_are_explicit(field: str) -> None:
    data = supplier_settings()
    del data[field]
    with pytest.raises(ValidationError):
        SupplierPolicy.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("freshness_basis", "SUPPLIER_EFFECTIVE_TIME"),
        ("inventory_max_age_seconds", True),
        ("inventory_max_age_seconds", 0),
        ("base_units_per_source_quantity", 1.5),
        ("base_units_per_source_quantity", 0),
        ("base_units_per_source_quantity", True),
    ],
)
def test_coercions_and_unproven_time_basis_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SupplierPolicy.model_validate({**supplier_settings(), field: value})


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("12.000"), 12),
        (0, 0),
        (None, None),
        (Decimal("2.5"), None),
        (True, None),
        ("12", None),
        (12.0, None),
        (Decimal("NaN"), None),
        (Decimal("Infinity"), None),
        (-1, None),
    ],
)
def test_only_confirmed_whole_quantities_are_usable(value: object, expected: int | None) -> None:
    assert whole_quantity(value) == expected


@pytest.mark.parametrize(
    "mode,quantity,expected",
    [
        ("quantity_gt_zero", 0, "OUT_OF_STOCK"),
        ("quantity_gt_zero", 2, "AVAILABLE"),
        ("quantity_gt_zero", None, "UNKNOWN"),
        ("quantity_gt_zero", "2", "UNKNOWN"),
        ("listed", None, "AVAILABLE"),
    ],
)
def test_availability_contract_is_rechecked(mode: str, quantity: object, expected: str) -> None:
    assert availability_from_profile({"available_when": mode}, quantity, {}) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({" STATE ": "yes "}, "AVAILABLE"),
        ({"State": "no"}, "OUT_OF_STOCK"),
        ({"State": "maybe"}, "UNKNOWN"),
        ({}, "UNKNOWN"),
        ({"State": "yes", " STATE ": "no"}, "UNKNOWN"),
    ],
)
def test_status_columns_need_one_exact_profile_interpretation(
    raw: dict[str, Any], expected: str
) -> None:
    rules = {
        "available_when": "status_column",
        "status_column": "State",
        "available_values": ["yes"],
        "unavailable_values": ["no"],
    }
    assert availability_from_profile(rules, None, raw) == expected


@pytest.mark.parametrize(
    "stock,status,quantity",
    [
        (600, "READY", 294),
        (100, "REVIEW_REQUIRED", 96),
        (0, "BLOCKED", None),
        (None, "REVIEW_REQUIRED", 294),
    ],
)
def test_supplier_caps_use_exact_existing_formula(
    stock: int | None, status: str, quantity: int | None
) -> None:
    policy = approved()
    snapshot = build_supplier_snapshot(
        policy, {}, (), readiness(policy), supplier(stock_base_units=stock)
    )
    [pool] = snapshot.pools
    assert pool.result.status == status and pool.result.suggested_units == quantity
    assert calculate_replenishment(pool.inputs) == pool.result
    assert snapshot.status == ("BLOCKED" if status == "BLOCKED" else "REVIEW_REQUIRED")
    assert "PROFITABILITY_NOT_INTEGRATED" in snapshot.review_reasons
    assert not snapshot.purchasing_eligible


def test_supplier_block_and_shared_stock_are_explicit() -> None:
    policy = approved()
    source = readiness(policy)
    source = source.model_copy(
        update={
            "pools": (source.pools[0], source.pools[0].model_copy(update={"pool_id": "second"}))
        }
    )
    result = build_supplier_snapshot(policy, {}, (), source, supplier())
    assert "SUPPLIER_STOCK_SHARED_ACROSS_POOLS" in result.review_reasons
    blocked = build_supplier_snapshot(
        policy,
        {},
        (),
        source,
        supplier(status="BLOCKED", issues=("SUPPLIER_FILE_RECEIPT_STALE_OR_FUTURE",)),
    )
    assert blocked.status == "BLOCKED"
    assert all(pool.result.suggested_units is None for pool in blocked.pools)


def test_supplier_digest_ignores_observation_clock_but_keeps_source_evidence() -> None:
    policy = approved()
    source = readiness(policy)
    first = build_supplier_snapshot(policy, {}, (), source, supplier())
    later = build_supplier_snapshot(
        policy, {}, (), source, supplier(evaluated_at=NOW + timedelta(seconds=1))
    )
    assert evidence_digest(first) == evidence_digest(later)
    changed = build_supplier_snapshot(
        policy, {}, (), source, supplier(evidence={"file_id": "different"})
    )
    assert evidence_digest(first) != evidence_digest(changed)
    assert SupplierRecommendationSnapshot.model_validate_json(first.model_dump_json()) == first


def test_legacy_saved_snapshot_still_parses_without_enrichment() -> None:
    from app.services.recommendations import build_snapshot

    policy = approved()
    legacy = build_snapshot(policy, {}, (), readiness(policy))
    response = RecommendationResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "request_id": uuid.uuid4(),
            "policy_id": policy.policy_id,
            "approved_revision_id": policy.id,
            "product_id": policy.product_id,
            "vendor_product_id": policy.vendor_product_id,
            "marketplace_id": policy.marketplace_id,
            "status": legacy.status,
            "snapshot": legacy.model_dump(mode="json"),
            "evidence_sha256": evidence_digest(legacy),
            "snapshot_sha256": digest(legacy.model_dump(mode="json")),
            "recorded_by_user_id": uuid.uuid4(),
            "recorded_at": NOW,
            "reason": "Synthetic retained v1 history",
        }
    )
    assert response.snapshot.rule_version == "replenishment-evaluation-v1"
    assert response.snapshot.model_dump(mode="json") == legacy.model_dump(mode="json")
