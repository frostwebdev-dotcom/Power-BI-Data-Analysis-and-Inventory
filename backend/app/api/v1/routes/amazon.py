"""Read-only Amazon analytics routes admitted by ADR 0011.

The endpoint exposes only data already ingested into PostgreSQL.  It never
calls a write operation, returns buyer PII, or computes a reorder quantity.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.amazon import AmazonVelocityResponse, AmazonVelocityRowResponse
from app.services.amazon_velocity import VelocityRow, velocity_report

router = APIRouter(prefix="/amazon", tags=["amazon"])

_read = Depends(require_roles(*READ_ROLES))

Responses = dict[int | str, dict[str, Any]]
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}


def _response(row: VelocityRow) -> AmazonVelocityRowResponse:
    return AmazonVelocityRowResponse(
        seller_sku=row.seller_sku,
        asin=row.asin,
        product_id=row.product_id,
        catalog_item_number=row.catalog_item_number,
        upc=row.upc,
        units_7=row.units_7,
        units_14=row.units_14,
        units_30=row.units_30,
        avg_daily_14=row.avg_daily_14,
        fulfillable=row.fulfillable,
        fbm_quantity=row.fbm_quantity,
        inbound=row.inbound,
        on_hand=row.on_hand,
        days_of_supply=row.days_of_supply,
        mapping_status=row.mapping_status,
        mapping_method=row.mapping_method,
    )


@router.get(
    "/velocity",
    response_model=AmazonVelocityResponse,
    summary="Read-only Amazon sales velocity and inventory by SKU or product",
    responses=_FORBIDDEN,
)
def get_velocity(
    level: Literal["sku", "product"] = Query("sku"),
    top: int | None = Query(None, ge=1, le=1000),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> AmazonVelocityResponse:
    rows = velocity_report(
        session,
        principal.organization_id,
        level=level,
        top=top,
    )
    items = [_response(row) for row in rows]
    return AmazonVelocityResponse(level=level, items=items, count=len(items))
