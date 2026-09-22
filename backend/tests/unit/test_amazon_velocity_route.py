"""Contract tests for the read-only Amazon velocity endpoint."""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from sqlalchemy.orm import Session

from app.api.v1.routes import amazon
from app.core.security import Principal, RoleCode
from app.models.enums import MappingStatus, MatchMethod
from app.services.amazon_velocity import VelocityRow


def _principal() -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="viewer@example.test",
        display_name="Test Viewer",
        roles=frozenset({RoleCode.VIEWER}),
    )


def test_velocity_route_shapes_derived_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    principal = _principal()
    row = VelocityRow(
        seller_sku="SKU-1",
        asin="B000000001",
        product_id=uuid.uuid4(),
        catalog_item_number="NY-1",
        upc="012345678905",
        units_7=7,
        units_14=28,
        units_30=45,
        fulfillable=30,
        fbm_quantity=10,
        inbound=6,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.UPC,
    )

    def fake_report(
        session: Session,
        organization_id: uuid.UUID,
        *,
        level: str,
        top: int | None,
    ) -> list[VelocityRow]:
        assert organization_id == principal.organization_id
        assert level == "product"
        assert top == 25
        return [row]

    monkeypatch.setattr(amazon, "velocity_report", fake_report)
    response = amazon.get_velocity(
        level="product",
        top=25,
        principal=principal,
        session=cast(Session, object()),
    )

    assert response.level == "product"
    assert response.count == 1
    [item] = response.items
    assert item.avg_daily_14 == 2
    assert item.on_hand == 40
    assert item.days_of_supply == 20
    assert item.mapping_status is MappingStatus.APPROVED


def test_velocity_route_preserves_empty_inventory_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = VelocityRow(
        seller_sku="SKU-EMPTY",
        asin=None,
        product_id=None,
        catalog_item_number=None,
        upc=None,
        units_7=0,
        units_14=0,
        units_30=0,
        fulfillable=None,
        fbm_quantity=None,
        inbound=None,
        mapping_status=None,
        mapping_method=None,
    )
    monkeypatch.setattr(
        amazon,
        "velocity_report",
        lambda *args, **kwargs: [row],
    )

    response = amazon.get_velocity(
        level="sku",
        top=None,
        principal=_principal(),
        session=cast(Session, object()),
    )

    [item] = response.items
    assert item.on_hand is None
    assert item.days_of_supply is None
