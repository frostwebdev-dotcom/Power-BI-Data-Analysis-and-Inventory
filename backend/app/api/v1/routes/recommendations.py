"""Preview and retain observed replenishment quantities, without purchase execution."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.recommendations import (
    RecommendationPage,
    RecommendationPreview,
    RecommendationPreviewRequest,
    RecommendationRecordRequest,
    RecommendationResponse,
)
from app.services import recommendations as service

_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "The caller lacks the required staff role."},
    404: {"description": "No such policy, identity or saved evaluation in this organization."},
    409: {"description": "The policy, preview evidence or request identifier changed."},
}
router = APIRouter(tags=["replenishment recommendations"], responses=_RESPONSES)
_read = Depends(require_roles(*service.READ))
_write = Depends(require_roles(*service.WRITE))


@router.post(
    "/purchasing-policies/{policy_id}/recommendation-preview", response_model=RecommendationPreview
)
def preview(
    policy_id: uuid.UUID,
    payload: RecommendationPreviewRequest,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> RecommendationPreview:
    return service.preview_recommendation(session, principal, policy_id, payload)


@router.post(
    "/purchasing-policies/{policy_id}/recommendations",
    response_model=RecommendationResponse,
    status_code=201,
)
def record(
    policy_id: uuid.UUID,
    payload: RecommendationRecordRequest,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> RecommendationResponse:
    return service.record_recommendation(session, principal, policy_id, payload)


@router.get("/replenishment-recommendations", response_model=RecommendationPage)
def history(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    policy_id: uuid.UUID | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    status: Literal["BLOCKED", "REVIEW_REQUIRED", "NO_REORDER"] | None = Query(None),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> RecommendationPage:
    return service.recommendation_page(
        session,
        principal,
        page,
        page_size,
        policy_id=policy_id,
        product_id=product_id,
        status=status,
    )


@router.get("/replenishment-recommendations/{identifier}", response_model=RecommendationResponse)
def detail(
    identifier: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> RecommendationResponse:
    return service.get_recommendation(session, principal, identifier)
