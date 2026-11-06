"""Automatic inventory sources/history; credentials remain server-side."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.db.transaction import transaction
from app.jobs.vendor_mail import run_vendor_mail_job
from app.models.organization import Organization
from app.repositories.vendor_mail import VendorMailRepository
from app.schemas.vendor_mail import (
    MailConnectionStatus,
    MailDeliveryResponse,
    MailRetry,
    MailSourceResponse,
    MailSourceWrite,
)
from app.services import audit
from app.services.vendor_mail import (
    account_key,
    retry_delivery,
    save_source,
    source_response,
)

router = APIRouter(prefix="/vendor-mail", tags=["automatic inventory imports"])
_read = Depends(require_roles(*READ_ROLES))
_write = Depends(require_roles(RoleCode.PURCHASING_MANAGER))
_admin = Depends(require_roles(RoleCode.ADMIN))
Responses = dict[int | str, dict[str, Any]]
_FORBIDDEN: Responses = {403: {"description": "This role cannot manage automatic sources."}}


class SourceList(BaseModel):
    items: list[MailSourceResponse]
    total: int


class DeliveryList(BaseModel):
    items: list[MailDeliveryResponse]
    total: int
    page: int
    page_size: int


@router.get("/status", response_model=MailConnectionStatus, responses=_FORBIDDEN)
def connection_status(
    session: Session = Depends(get_db),
    principal: Principal = _read,
    settings: Settings = Depends(get_app_settings),
) -> MailConnectionStatus:
    org = session.get(Organization, principal.organization_id)
    belongs = org is not None and org.slug == settings.vendor_mail_organization_slug
    configured = settings.vendor_mail_configured and belongs
    missing = settings.vendor_mail_missing_settings.copy()
    if settings.vendor_mail_configured and not belongs:
        missing = ["VENDOR_MAIL_ORGANIZATION_SLUG"]
    state = (
        VendorMailRepository(session, principal.organization_id).state(account_key(settings))
        if configured
        else None
    )
    return MailConnectionStatus(
        enabled=settings.vendor_mail_enabled and belongs,
        configured=configured,
        mailbox=settings.vendor_mail_mailbox if belongs else None,
        poll_minutes=settings.vendor_mail_poll_minutes,
        missing_settings=missing,
        last_poll_at=state.last_poll_at if state else None,
        last_success_at=state.last_success_at if state else None,
        error_code=state.error_code if state else None,
    )


@router.get("/sources", response_model=SourceList, responses=_FORBIDDEN)
def list_sources(session: Session = Depends(get_db), principal: Principal = _read) -> SourceList:
    sources = VendorMailRepository(session, principal.organization_id).sources()
    return SourceList(items=[source_response(row) for row in sources], total=len(sources))


@router.post(
    "/sources",
    response_model=MailSourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_FORBIDDEN,
)
def create_source(
    payload: MailSourceWrite, session: Session = Depends(get_db), principal: Principal = _write
) -> MailSourceResponse:
    return source_response(save_source(session, principal, payload))


@router.patch("/sources/{source_id}", response_model=MailSourceResponse, responses=_FORBIDDEN)
def edit_source(
    source_id: uuid.UUID,
    payload: MailSourceWrite,
    session: Session = Depends(get_db),
    principal: Principal = _write,
) -> MailSourceResponse:
    return source_response(save_source(session, principal, payload, source_id))


@router.get("/deliveries", response_model=DeliveryList, responses=_FORBIDDEN)
def delivery_history(
    source_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
    principal: Principal = _read,
) -> DeliveryList:
    rows, total = VendorMailRepository(session, principal.organization_id).history(
        source_id, page, page_size
    )
    return DeliveryList(
        items=[MailDeliveryResponse.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/deliveries/{delivery_id}/retry", response_model=MailDeliveryResponse, responses=_FORBIDDEN
)
def retry(
    delivery_id: uuid.UUID,
    payload: MailRetry,
    session: Session = Depends(get_db),
    principal: Principal = _write,
) -> MailDeliveryResponse:
    return MailDeliveryResponse.model_validate(
        retry_delivery(
            session, principal, delivery_id, use_current_profile=payload.use_current_profile
        )
    )


@router.post("/poll", status_code=status.HTTP_202_ACCEPTED, responses=_FORBIDDEN)
def request_poll(
    background: BackgroundTasks,
    session: Session = Depends(get_db),
    principal: Principal = _admin,
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    connection = connection_status(session, principal, settings)
    if not connection.enabled or not connection.configured:
        raise ConflictError(
            message="Configure and enable the inventory mailbox before checking it."
        )
    with transaction(session):
        audit.record(
            session,
            organization_id=principal.organization_id,
            action="vendor_mail.poll.requested",
            entity_type="vendor_mail_states",
            actor=principal,
            summary="Manual inventory-mailbox check requested.",
        )
    background.add_task(run_vendor_mail_job, settings)
    return {"status": "QUEUED"}
