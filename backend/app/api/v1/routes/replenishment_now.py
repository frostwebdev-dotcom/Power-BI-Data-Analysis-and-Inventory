"""Bounded explicit current checks; no provider sync, recording or purchasing."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.replenishment_now import (
    CurrentCheckRequest,
    CurrentCheckResponse,
    CurrentChoicePage,
)
from app.services.purchasing_policies import READ
from app.services.replenishment_now import current_check, current_choices

router = APIRouter(
    tags=["current replenishment"],
    responses={
        403: {"description": "Staff read role required."},
        404: {"description": "Selected policy not found in this organization."},
        409: {"description": "A selected policy approval changed; refresh choices."},
    },
)
_read = Depends(require_roles(*READ))


@router.get("/replenishment-now/policies", response_model=CurrentChoicePage)
def choices(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
    product_id: uuid.UUID | None = Query(None),
    vendor_id: uuid.UUID | None = Query(None),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> CurrentChoicePage:
    return current_choices(
        session, principal, page, page_size, q=q, product_id=product_id, vendor_id=vendor_id
    )


@router.post("/replenishment-now/preview", response_model=CurrentCheckResponse)
def preview(
    payload: CurrentCheckRequest, principal: Principal = _read, session: Session = Depends(get_db)
) -> CurrentCheckResponse:
    return current_check(session, principal, payload)
