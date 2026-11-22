"""Policy contracts are explicit and content hashes ignore collection order."""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.schemas.purchasing_policies import (
    PolicyCreateRequest,
    PolicyReviewRequest,
    PurchasingPolicyDefinition,
)
from app.services.purchasing_policies import (
    create_policy,
    definition_snapshot,
    digest,
    preview_review,
)

PRODUCT = uuid.UUID("11111111-1111-4111-8111-111111111111")
LISTING = uuid.UUID("22222222-2222-4222-8222-222222222222")


def definition(product: uuid.UUID = PRODUCT, listing: uuid.UUID = LISTING) -> dict[str, Any]:
    return {
        "quantity": {
            "velocity_window_days": 14,
            "target_days": 14,
            "lead_time_days": 2,
            "safety_days": 1,
            "minimum_order_units": 0,
            "order_multiple_units": 6,
        },
        "amazon": {
            "product_id": str(product),
            "marketplace_id": "ATVPDKIKX0DER",
            "sales_channel": "Amazon.com",
            "demand_basis": "FULLY_SHIPPED_ONLY",
            "velocity_window_days": 14,
            "orders_max_age_seconds": 7200,
            "inventory_max_age_seconds": 3600,
            "listing_units": [
                {
                    "listing_id": str(listing),
                    "base_units_per_listing_unit": 6,
                    "confirmation_reference": "Synthetic confirmed six pack",
                }
            ],
            "stock_pools": [
                {
                    "pool_id": "fba",
                    "channel": "FBA",
                    "listing_ids": [str(listing)],
                    "stock_source_listing_id": str(listing),
                    "distinct_stock_reference": "Synthetic independent FBA stock",
                }
            ],
        },
        "coverage_policy": "target-plus-lead-plus-safety",
        "coverage_confirmation_reference": "Synthetic manager says target excludes lead and safety",
        "inbound_policy": "CONFIRMED_BEFORE_STOCKOUT_ONLY",
        "vendor_base_units_per_order_unit": 6,
        "vendor_unit_confirmation_reference": "Synthetic vendor sells six base units per order",
    }


@pytest.mark.parametrize("field", list(definition()))
def test_every_policy_choice_is_required(field: str) -> None:
    data = definition()
    del data[field]
    with pytest.raises(ValidationError):
        PurchasingPolicyDefinition.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("coverage_policy", "target-includes-lead"),
        ("inbound_policy", "ALL_INBOUND"),
        ("vendor_base_units_per_order_unit", True),
        ("vendor_base_units_per_order_unit", 0),
        ("vendor_base_units_per_order_unit", 1.5),
        ("unreviewed_extra", True),
    ],
)
def test_unsupported_or_coerced_choices_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PurchasingPolicyDefinition.model_validate({**definition(), field: value})


def test_windows_must_agree_and_persisted_constraints_are_bounded() -> None:
    data = definition()
    data["quantity"]["velocity_window_days"] = 7
    with pytest.raises(ValidationError):
        PurchasingPolicyDefinition.model_validate(data)
    data = definition()
    data["quantity"]["minimum_order_units"] = 1000000001
    with pytest.raises(ValidationError):
        PurchasingPolicyDefinition.model_validate(data)


def test_unknown_assumptions_can_be_saved_as_draft() -> None:
    data = definition()
    data["coverage_confirmation_reference"] = None
    data["vendor_base_units_per_order_unit"] = None
    data["vendor_unit_confirmation_reference"] = None
    data["amazon"]["listing_units"][0]["base_units_per_listing_unit"] = None
    data["amazon"]["listing_units"][0]["confirmation_reference"] = None
    assert PurchasingPolicyDefinition.model_validate(data).vendor_base_units_per_order_unit is None


def test_hashes_are_canonical_for_lists_but_include_policy_and_confirmation_changes() -> None:
    data = definition()
    other = str(uuid.uuid4())
    data["amazon"]["listing_units"].append(
        {**data["amazon"]["listing_units"][0], "listing_id": other}
    )
    data["amazon"]["stock_pools"][0]["listing_ids"].append(other)
    first = PurchasingPolicyDefinition.model_validate(data)
    data["amazon"]["listing_units"].reverse()
    data["amazon"]["stock_pools"][0]["listing_ids"].reverse()
    second = PurchasingPolicyDefinition.model_validate(data)
    assert digest(definition_snapshot(first)) == digest(definition_snapshot(second))
    data["quantity"]["lead_time_days"] = 3
    assert digest(definition_snapshot(first)) != digest(
        definition_snapshot(PurchasingPolicyDefinition.model_validate(data))
    )
    assert first.amazon.listing_units[0].listing_id == LISTING  # input is untouched


@pytest.mark.parametrize("reason", ["", "short", "             "])
def test_reason_is_not_optional_or_whitespace(reason: str) -> None:
    with pytest.raises(ValidationError):
        PolicyCreateRequest(
            vendor_product_id=uuid.uuid4(),
            definition=PurchasingPolicyDefinition.model_validate(definition()),
            reason=reason,
        )


@pytest.mark.parametrize("acknowledgement", [False, "true", 1, None])
def test_review_requires_explicit_boolean_acknowledgement(acknowledgement: object) -> None:
    with pytest.raises(ValidationError):
        PolicyReviewRequest.model_validate(
            {
                "action": "APPROVE",
                "expected_head_id": str(uuid.uuid4()),
                "reviewed_revision_id": str(uuid.uuid4()),
                "definition_sha256": "a" * 64,
                "context_sha256": "b" * 64,
                "reason": "Synthetic reviewed all policy assumptions",
                "acknowledge_review": acknowledgement,
            }
        )


def test_operators_cannot_review_and_viewers_cannot_draft_without_database_access() -> None:
    session = cast(Session, object())
    operator = Principal(
        uuid.uuid4(),
        uuid.uuid4(),
        "operator@example.test",
        "Operator",
        frozenset({RoleCode.DATA_OPERATOR}),
    )
    viewer = Principal(
        uuid.uuid4(), uuid.uuid4(), "viewer@example.test", "Viewer", frozenset({RoleCode.VIEWER})
    )
    with pytest.raises(AuthorizationError):
        preview_review(session, operator, uuid.uuid4(), "APPROVE")
    with pytest.raises(AuthorizationError):
        create_policy(
            session,
            viewer,
            PolicyCreateRequest(
                vendor_product_id=uuid.uuid4(),
                definition=PurchasingPolicyDefinition.model_validate(definition()),
                reason="Synthetic explicit policy proposal",
            ),
        )


def test_policy_routes_require_authentication(client: TestClient) -> None:
    url = "/api/v1/purchasing-policies"
    assert client.get(url).status_code == 401
    assert client.post(url, json={}).status_code == 401
    assert client.get(url + f"/{uuid.uuid4()}").status_code == 401
