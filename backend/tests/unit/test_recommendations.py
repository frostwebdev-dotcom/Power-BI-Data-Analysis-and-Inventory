"""Integration arithmetic, reproducible evidence and explicit staff contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.schemas.amazon_readiness import AmazonReadinessResponse, ReadinessIssue, ReadinessPool
from app.schemas.purchasing_policies import PolicyRevisionResponse, PurchasingPolicyDefinition
from app.schemas.recommendations import RecommendationPreviewRequest, RecommendationRecordRequest
from app.services.purchasing_policies import digest, mapping_evidence
from app.services.recommendations import (
    build_snapshot,
    evidence_digest,
    preview_recommendation,
    record_recommendation,
)
from app.services.replenishment import calculate_replenishment
from tests.unit.test_purchasing_policies import definition

NOW = datetime(2026, 11, 17, 20, tzinfo=UTC)


def approved() -> PolicyRevisionResponse:
    data = PurchasingPolicyDefinition.model_validate(definition())
    return PolicyRevisionResponse(
        id=uuid.uuid4(),
        policy_id=uuid.uuid4(),
        product_id=data.amazon.product_id,
        vendor_product_id=uuid.uuid4(),
        marketplace_id=data.amazon.marketplace_id,
        revision_number=2,
        supersedes_revision_id=uuid.uuid4(),
        reviewed_revision_id=uuid.uuid4(),
        kind="APPROVED",
        definition=data,
        definition_sha256=digest(data.model_dump(mode="json")),
        review_context={},
        context_sha256=digest({}),
        recorded_by_user_id=uuid.uuid4(),
        recorded_at=NOW - timedelta(minutes=1),
        reason="Synthetic reviewed policy",
    )


def readiness(policy: PolicyRevisionResponse, **changes: Any) -> AmazonReadinessResponse:
    pool = ReadinessPool(
        pool_id="fba",
        channel="FBA",
        listing_ids=(policy.definition.amazon.listing_units[0].listing_id,),
        stock_source_listing_id=policy.definition.amazon.listing_units[0].listing_id,
        sales_base_units=280,
        on_hand_base_units=50,
        observed_inbound_base_units=0,
        usable_inbound_base_units=0,
        quantity_input_ready=True,
    )
    return AmazonReadinessResponse.model_validate(
        {
            "request": policy.definition.amazon,
            "evaluated_at": NOW,
            "demand_window_start": NOW - timedelta(days=14),
            "demand_window_end": NOW,
            "status": "DATA_AVAILABLE",
            "issues": (),
            "latest_orders_run": None,
            "order_coverage_run_ids": (uuid.uuid4(),),
            "inventory_run": None,
            "listings": (),
            "pools": (pool,),
            **changes,
        }
    )


def test_exact_policy_arithmetic_is_preserved_without_supplier_guess() -> None:
    policy = approved()
    original = readiness(policy)
    result = build_snapshot(policy, {}, (), original)
    [pool] = result.pools
    assert pool.result.calculation.required_units == 340
    assert pool.result.calculation.net_shortfall_units == 290
    assert pool.result.calculation.order_quantity_before_vendor_cap == 294
    assert pool.result.status == "BLOCKED" and pool.result.suggested_units is None
    assert pool.inputs.vendor_availability == "UNKNOWN" and pool.inputs.vendor_stock_units is None
    assert "SUPPLIER_INPUTS_NOT_VERIFIED" in pool.blocking_reasons
    assert not result.purchasing_eligible and result.profitability == "NOT_INTEGRATED"
    assert result.readiness == original
    assert calculate_replenishment(pool.inputs) == pool.result


@pytest.mark.parametrize("sold,on_hand", [(0, 0), (280, 340), (1, 2)])
def test_no_reorder_needs_no_supplier_guess(sold: int, on_hand: int) -> None:
    policy = approved()
    source = readiness(policy)
    # One sold unit requires two units at 17 / 14 coverage.
    source = source.model_copy(
        update={
            "pools": (
                source.pools[0].model_copy(
                    update={
                        "sales_base_units": sold,
                        "on_hand_base_units": on_hand,
                    }
                ),
            )
        }
    )
    result = build_snapshot(policy, {}, (), source)
    assert result.status == "NO_REORDER"
    assert result.pools[0].result.suggested_units == 0
    assert result.pools[0].blocking_reasons == ()


@pytest.mark.parametrize("fault", ["policy", "source", "inbound", "sales", "stock", "no_pools"])
def test_incomplete_evidence_never_becomes_an_order(fault: str) -> None:
    policy = approved()
    source = readiness(policy)
    blocks: tuple[str, ...] = ()
    if fault == "policy":
        blocks = ("POLICY_IDENTITY_EVIDENCE_CHANGED",)
    elif fault == "source":
        source = source.model_copy(
            update={
                "status": "BLOCKED",
                "issues": (ReadinessIssue(code="ORDERS_DATA_STALE"),),
            }
        )
    elif fault == "no_pools":
        source = source.model_copy(update={"pools": ()})
    else:
        key = {
            "inbound": "usable_inbound_base_units",
            "sales": "sales_base_units",
            "stock": "on_hand_base_units",
        }[fault]
        source = source.model_copy(
            update={"pools": (source.pools[0].model_copy(update={key: None}),)}
        )
    result = build_snapshot(policy, {}, blocks, source)
    assert result.status == "BLOCKED" and not result.purchasing_eligible
    assert all(pool.result.suggested_units is None for pool in result.pools)


def test_pool_quantities_stay_separate_and_replay_exactly() -> None:
    policy = approved()
    source = readiness(policy)
    source = source.model_copy(
        update={
            "pools": (
                source.pools[0],
                source.pools[0].model_copy(update={"pool_id": "separate", "sales_base_units": 14}),
            )
        }
    )
    result = build_snapshot(policy, {}, (), source)
    assert [pool.result.calculation.required_units for pool in result.pools] == [340, 17]
    assert [pool.inputs.on_hand_units for pool in result.pools] == [50, 50]
    for pool in result.pools:
        assert calculate_replenishment(pool.inputs) == pool.result


def test_evidence_excludes_clock_but_includes_staleness_and_source_values() -> None:
    policy = approved()
    source = readiness(policy)
    original = build_snapshot(policy, {}, (), source)
    later = build_snapshot(
        policy, {}, (), source.model_copy(update={"evaluated_at": NOW + timedelta(seconds=1)})
    )
    assert evidence_digest(original) == evidence_digest(later)
    assert digest(original.model_dump(mode="json")) != digest(later.model_dump(mode="json"))
    changed = source.model_copy(
        update={"pools": (source.pools[0].model_copy(update={"sales_base_units": 281}),)}
    )
    assert evidence_digest(original) != evidence_digest(build_snapshot(policy, {}, (), changed))
    stale = source.model_copy(
        update={"status": "BLOCKED", "issues": (ReadinessIssue(code="ORDERS_DATA_STALE"),)}
    )
    assert evidence_digest(original) != evidence_digest(build_snapshot(policy, {}, (), stale))


def test_blocked_no_baseline_window_clock_does_not_prevent_recording() -> None:
    policy = approved()
    source = readiness(
        policy,
        order_coverage_run_ids=(),
        status="BLOCKED",
        issues=(ReadinessIssue(code="PURCHASE_WINDOW_BASELINE_UNPROVEN"),),
    )
    first = build_snapshot(policy, {}, (), source)
    later = source.model_copy(
        update={
            "evaluated_at": NOW + timedelta(seconds=1),
            "demand_window_start": source.demand_window_start + timedelta(seconds=1),
            "demand_window_end": source.demand_window_end + timedelta(seconds=1),
        }
    )
    assert evidence_digest(first) == evidence_digest(build_snapshot(policy, {}, (), later))


def test_policy_comparison_ignores_labels_and_vendor_observation_time() -> None:
    first = {
        "product": {"id": "product", "name": "Old"},
        "vendor_product": {"id": "vendor-product", "last_seen_at": "old"},
        "listings": [{"id": "listing", "asin": "ASIN"}],
    }
    second = {
        **first,
        "product": {"id": "product", "name": "New"},
        "vendor_product": {"id": "vendor-product", "last_seen_at": "new"},
    }
    assert mapping_evidence(first) == mapping_evidence(second)
    second["listings"] = [{"id": "listing", "asin": "CHANGED"}]
    assert mapping_evidence(first) != mapping_evidence(second)


def payload() -> dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "expected_approved_revision_id": str(uuid.uuid4()),
        "expected_evidence_sha256": "a" * 64,
        "reason": "Synthetic reviewed calculation",
        "acknowledge_review": True,
    }


@pytest.mark.parametrize("field", list(payload()))
def test_record_requires_every_review_and_retry_field(field: str) -> None:
    data = payload()
    del data[field]
    with pytest.raises(ValidationError):
        RecommendationRecordRequest.model_validate(data)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_record_requires_strict_acknowledgement(value: object) -> None:
    with pytest.raises(ValidationError):
        RecommendationRecordRequest.model_validate({**payload(), "acknowledge_review": value})


@pytest.mark.parametrize(
    "extra", ["sales_units", "mapping_approved", "definition", "vendor_availability"]
)
def test_caller_cannot_override_server_evidence(extra: str) -> None:
    with pytest.raises(ValidationError):
        RecommendationPreviewRequest.model_validate(
            {"expected_approved_revision_id": str(uuid.uuid4()), extra: True}
        )


def test_roles_are_checked_before_database_access() -> None:
    viewer = Principal(
        uuid.uuid4(), uuid.uuid4(), "viewer@example.test", "Viewer", frozenset({RoleCode.VIEWER})
    )
    none = Principal(
        viewer.user_id, viewer.organization_id, viewer.email, viewer.display_name, frozenset()
    )
    session = cast(Session, object())
    with pytest.raises(AuthorizationError):
        record_recommendation(
            session, viewer, uuid.uuid4(), RecommendationRecordRequest.model_validate(payload())
        )
    with pytest.raises(AuthorizationError):
        preview_recommendation(
            session,
            none,
            uuid.uuid4(),
            RecommendationPreviewRequest(expected_approved_revision_id=uuid.uuid4()),
        )


def test_recommendation_routes_require_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/replenishment-recommendations").status_code == 401
    assert client.get(f"/api/v1/replenishment-recommendations/{uuid.uuid4()}").status_code == 401
    base = f"/api/v1/purchasing-policies/{uuid.uuid4()}"
    assert client.post(base + "/recommendation-preview", json={}).status_code == 401
    assert client.post(base + "/recommendations", json={}).status_code == 401
