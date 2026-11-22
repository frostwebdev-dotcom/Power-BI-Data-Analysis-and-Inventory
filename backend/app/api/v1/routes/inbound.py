"""Review evidence of existing inbound shipments, without receiving or ordering."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.inbound import (
    InboundContext,
    InboundContextRequest,
    InboundPage,
    InboundPreview,
    InboundProposal,
    InboundRecordRequest,
    InboundRevision,
    InboundRevokeRequest,
)
from app.services import inbound as service
from app.services.purchasing_policies import READ

router = APIRouter(
    prefix="/purchasing-policies/{policy_id}/inbound",
    tags=["reviewed inbound"],
    responses={
        403: {"description": "Required staff role is missing."},
        404: {"description": "No such scoped policy, pool or evidence."},
        409: {"description": "Approval, head, source or preview changed; review again."},
    },
)
_read = Depends(require_roles(*READ))
_manage = Depends(require_roles(*service.MANAGE))


@router.get("", response_model=InboundPage)
def history(
    policy_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    pool_id: str | None = Query(None, max_length=64),
    heads_only: bool = Query(True),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> InboundPage:
    return service.page(session, principal, policy_id, page, page_size, pool_id, heads_only)


@router.post("/context", response_model=InboundContext)
def context(
    policy_id: uuid.UUID,
    payload: InboundContextRequest,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> InboundContext:
    return service.context(session, principal, policy_id, payload)


@router.post("/preview", response_model=InboundPreview)
def preview(
    policy_id: uuid.UUID,
    payload: InboundProposal,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> InboundPreview:
    return service.preview(session, principal, policy_id, payload)


@router.post("", response_model=InboundRevision, status_code=201)
def record(
    policy_id: uuid.UUID,
    payload: InboundRecordRequest,
    principal: Principal = _manage,
    session: Session = Depends(get_db),
) -> InboundRevision:
    return service.record(session, principal, policy_id, payload)


@router.post("/revoke", response_model=InboundRevision, status_code=201)
def revoke(
    policy_id: uuid.UUID,
    payload: InboundRevokeRequest,
    principal: Principal = _manage,
    session: Session = Depends(get_db),
) -> InboundRevision:
    return service.revoke(session, principal, policy_id, payload)
