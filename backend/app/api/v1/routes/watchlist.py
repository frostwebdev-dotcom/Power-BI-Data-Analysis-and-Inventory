"""Watchlist and availability-feed routes (AC-10, AC-11).

Reads for every role; adding and removing a watch for ``PURCHASING_MANAGER``
(``ADMIN`` passes every check).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.models.inventory import AvailabilityEvent
from app.repositories.inventory import InventoryRepository
from app.schemas.watchlist import (
    AvailabilityEventListResponse,
    AvailabilityEventResponse,
    StatusHistoryResponse,
    WatchlistCreate,
    WatchlistEntryResponse,
    WatchlistHistoryResponse,
    WatchlistListResponse,
    WatchlistRemove,
)
from app.services import watchlist as service

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
availability_router = APIRouter(prefix="/availability", tags=["availability"])

_read = Depends(require_roles(*READ_ROLES))
_write = Depends(require_roles(RoleCode.PURCHASING_MANAGER))

Responses = dict[int | str, dict[str, Any]]
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}
_NOT_FOUND: Responses = {404: {"description": "No such entry, product or vendor."}}
_CONFLICT: Responses = {409: {"description": "The watch already exists or was already removed."}}


@router.get(
    "",
    response_model=WatchlistListResponse,
    summary="Watched products, newest first",
    responses=_FORBIDDEN,
)
def list_watchlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    vendor_id: uuid.UUID | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    include_inactive: bool = Query(False),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> WatchlistListResponse:
    result = service.list_watchlist(
        session,
        principal,
        page=page,
        page_size=page_size,
        vendor_id=vendor_id,
        product_id=product_id,
        include_inactive=include_inactive,
    )
    return WatchlistListResponse(
        items=[WatchlistEntryResponse.model_validate(e) for e in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post(
    "",
    response_model=WatchlistEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Watch a product, at one vendor or any",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def add_watch(
    payload: WatchlistCreate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> WatchlistEntryResponse:
    return WatchlistEntryResponse.model_validate(service.add_watch(session, principal, payload))


@router.get(
    "/{entry_id}",
    response_model=WatchlistEntryResponse,
    summary="One watch entry",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_watch(
    entry_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> WatchlistEntryResponse:
    return WatchlistEntryResponse.model_validate(service.get_watch(session, principal, entry_id))


@router.post(
    "/{entry_id}/remove",
    response_model=WatchlistEntryResponse,
    summary="Remove a watch (deactivated, never deleted)",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def remove_watch(
    entry_id: uuid.UUID,
    payload: WatchlistRemove | None = None,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> WatchlistEntryResponse:
    note = payload.note if payload is not None else None
    return WatchlistEntryResponse.model_validate(
        service.remove_watch(session, principal, entry_id, note=note)
    )


@router.get(
    "/{entry_id}/history",
    response_model=WatchlistHistoryResponse,
    summary="Every status change of a watch entry, oldest first",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def watch_history(
    entry_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> WatchlistHistoryResponse:
    entry = service.get_watch(session, principal, entry_id)
    return WatchlistHistoryResponse(
        entry=WatchlistEntryResponse.model_validate(entry),
        history=[
            StatusHistoryResponse.model_validate(h)
            for h in service.history(session, principal, entry_id)
        ],
    )


# --- the availability feed ------------------------------------------------------------------


def _event(event: AvailabilityEvent) -> AvailabilityEventResponse:
    body = AvailabilityEventResponse.model_validate(event)
    body.vendor_sku = event.vendor_product.vendor_sku if event.vendor_product else None
    body.product_name = event.product.name if event.product else None
    body.catalog_item_number = event.product.catalog_item_number if event.product else None
    return body


@availability_router.get(
    "/events",
    response_model=AvailabilityEventListResponse,
    summary="Availability transitions, newest first",
    responses=_FORBIDDEN,
)
def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    vendor_id: uuid.UUID | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    watchlist_only: bool = Query(False, description="Only events linked to a watch entry."),
    since: datetime | None = Query(None, description="Only events detected at or after this."),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> AvailabilityEventListResponse:
    result = InventoryRepository(session, principal.organization_id).list_events(
        page=page,
        page_size=page_size,
        vendor_id=vendor_id,
        product_id=product_id,
        watchlist_only=watchlist_only,
        since=since,
    )
    return AvailabilityEventListResponse(
        items=[_event(e) for e in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
