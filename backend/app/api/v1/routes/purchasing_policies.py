"""Explicit policy drafts and manager-reviewed decisions, never purchases."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.purchasing_policies import (
    Action,
    PolicyCreateRequest,
    PolicyDraftRequest,
    PolicyPage,
    PolicyReviewPreview,
    PolicyReviewRequest,
    PolicyRevisionPage,
    PolicySummary,
)
from app.services import purchasing_policies as service

_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The caller lacks the required staff or manager role."},
    404: {"description": "No such policy or linked identity in this organization."},
    409: {"description": "The policy changed or the requested review is blocked."},
}
router = APIRouter(
    prefix="/purchasing-policies", tags=["purchasing policies"], responses=_RESPONSES
)
_read = Depends(require_roles(*service.READ))
_write = Depends(require_roles(*service.WRITE))
_review = Depends(require_roles(*service.REVIEW))


@router.get("", response_model=PolicyPage)
def policies(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    product_id: uuid.UUID | None = Query(None),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> PolicyPage:
    return service.policy_page(session, principal, page, page_size, product_id)


@router.post("", response_model=PolicySummary, status_code=201)
def create(
    payload: PolicyCreateRequest, principal: Principal = _write, session: Session = Depends(get_db)
) -> PolicySummary:
    return service.create_policy(session, principal, payload)


@router.get("/{policy_id}", response_model=PolicySummary)
def policy(
    policy_id: uuid.UUID, principal: Principal = _read, session: Session = Depends(get_db)
) -> PolicySummary:
    return service.get_policy(session, principal, policy_id)


@router.get("/{policy_id}/revisions", response_model=PolicyRevisionPage)
def revisions(
    policy_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> PolicyRevisionPage:
    return service.revision_page(session, principal, policy_id, page, page_size)


@router.post("/{policy_id}/revisions", response_model=PolicySummary, status_code=201)
def draft(
    policy_id: uuid.UUID,
    payload: PolicyDraftRequest,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> PolicySummary:
    return service.record_draft(session, principal, policy_id, payload)


@router.post("/{policy_id}/review-preview", response_model=PolicyReviewPreview)
def review_preview(
    policy_id: uuid.UUID,
    action: Action = Query(...),
    principal: Principal = _review,
    session: Session = Depends(get_db),
) -> PolicyReviewPreview:
    return service.preview_review(session, principal, policy_id, action)


@router.post("/{policy_id}/reviews", response_model=PolicySummary, status_code=201)
def review(
    policy_id: uuid.UUID,
    payload: PolicyReviewRequest,
    principal: Principal = _review,
    session: Session = Depends(get_db),
) -> PolicySummary:
    return service.record_review(session, principal, policy_id, payload)
