"""Vendor routes (AC-4).

Reads are open to every role — the role model is flat, so "VIEWER or above"
has to be spelled out as the full list; a DATA_OPERATOR who could create a
vendor but not read it back would be absurd. Writes need ``DATA_OPERATOR``.
``ADMIN`` passes both.
Routes do three things only — parse, call the service, shape the response —
so the audit, transaction and refusal rules live in one place
(:mod:`app.services.vendors`) and cannot be bypassed by a second route.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.models.enums import VendorStatus
from app.models.vendor import Vendor
from app.schemas.vendors import (
    VendorContactCreate,
    VendorContactResponse,
    VendorContactUpdate,
    VendorCreate,
    VendorDetailResponse,
    VendorListResponse,
    VendorResponse,
    VendorUpdate,
)
from app.services import vendors as service

router = APIRouter(prefix="/vendors", tags=["vendors"])

READ_ROLES = (
    RoleCode.VIEWER,
    RoleCode.DATA_OPERATOR,
    RoleCode.PURCHASING_MANAGER,
    RoleCode.ADMIN,
)
_read = Depends(require_roles(*READ_ROLES))
_write = Depends(require_roles(RoleCode.DATA_OPERATOR))

Responses = dict[int | str, dict[str, Any]]
_NOT_FOUND: Responses = {404: {"description": "No such vendor (or contact) in this organization."}}
_CONFLICT: Responses = {409: {"description": "The change conflicts with current state."}}
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}


def _detail(session: Session, principal: Principal, vendor: Vendor) -> VendorDetailResponse:
    contacts = service.list_contacts(session, principal, vendor.id)
    return VendorDetailResponse(
        **VendorResponse.model_validate(vendor).model_dump(),
        contacts=[VendorContactResponse.model_validate(c) for c in contacts],
    )


# --- vendors --------------------------------------------------------------------------


@router.get("", response_model=VendorListResponse, summary="List vendors", responses=_FORBIDDEN)
def list_vendors(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: VendorStatus | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=200, description="Substring of name or code."),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> VendorListResponse:
    result = service.list_vendors(
        session, principal, page=page, page_size=page_size, status=status_filter, q=q
    )
    return VendorListResponse(
        items=[VendorResponse.model_validate(v) for v in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{vendor_id}",
    response_model=VendorDetailResponse,
    summary="A vendor with its contacts",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_vendor(
    vendor_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> VendorDetailResponse:
    return _detail(session, principal, service.get_vendor(session, principal, vendor_id))


@router.post(
    "",
    response_model=VendorDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a vendor",
    responses={**_FORBIDDEN, **_CONFLICT},
)
def create_vendor(
    payload: VendorCreate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorDetailResponse:
    return _detail(session, principal, service.create_vendor(session, principal, payload))


@router.patch(
    "/{vendor_id}",
    response_model=VendorDetailResponse,
    summary="Update a vendor",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def update_vendor(
    vendor_id: uuid.UUID,
    payload: VendorUpdate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorDetailResponse:
    return _detail(
        session, principal, service.update_vendor(session, principal, vendor_id, payload)
    )


@router.post(
    "/{vendor_id}/deactivate",
    response_model=VendorDetailResponse,
    summary="Deactivate a vendor (never deleted)",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def deactivate_vendor(
    vendor_id: uuid.UUID,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorDetailResponse:
    return _detail(session, principal, service.deactivate_vendor(session, principal, vendor_id))


# --- contacts -------------------------------------------------------------------------


@router.get(
    "/{vendor_id}/contacts",
    response_model=list[VendorContactResponse],
    summary="A vendor's contacts",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def list_contacts(
    vendor_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> list[VendorContactResponse]:
    contacts = service.list_contacts(session, principal, vendor_id)
    return [VendorContactResponse.model_validate(c) for c in contacts]


@router.get(
    "/{vendor_id}/contacts/{contact_id}",
    response_model=VendorContactResponse,
    summary="One contact",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_contact(
    vendor_id: uuid.UUID,
    contact_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> VendorContactResponse:
    return VendorContactResponse.model_validate(
        service.get_contact(session, principal, vendor_id, contact_id)
    )


@router.post(
    "/{vendor_id}/contacts",
    response_model=VendorContactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a contact",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def add_contact(
    vendor_id: uuid.UUID,
    payload: VendorContactCreate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorContactResponse:
    return VendorContactResponse.model_validate(
        service.add_contact(session, principal, vendor_id, payload)
    )


@router.patch(
    "/{vendor_id}/contacts/{contact_id}",
    response_model=VendorContactResponse,
    summary="Update a contact",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def update_contact(
    vendor_id: uuid.UUID,
    contact_id: uuid.UUID,
    payload: VendorContactUpdate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorContactResponse:
    return VendorContactResponse.model_validate(
        service.update_contact(session, principal, vendor_id, contact_id, payload)
    )


@router.post(
    "/{vendor_id}/contacts/{contact_id}/deactivate",
    response_model=VendorContactResponse,
    summary="Deactivate a contact",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def deactivate_contact(
    vendor_id: uuid.UUID,
    contact_id: uuid.UUID,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> VendorContactResponse:
    return VendorContactResponse.model_validate(
        service.deactivate_contact(session, principal, vendor_id, contact_id)
    )
