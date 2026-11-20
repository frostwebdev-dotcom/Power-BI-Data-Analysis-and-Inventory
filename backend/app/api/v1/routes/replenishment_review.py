"""Selected-policy comparison and historical saved-evaluation discovery."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Principal
from app.db.session import get_db
from app.schemas.recommendations import EvaluationStatus
from app.schemas.replenishment_review import (
    SavedReviewPage,
    SavedReviewSummary,
    VendorComparisonRequest,
    VendorComparisonResponse,
)
from app.services.purchasing_policies import READ
from app.services.replenishment_review import review_page, review_summary
from app.services.vendor_comparisons import preview_vendor_comparison

router = APIRouter(
    tags=["replenishment review"],
    responses={
        403: {"description": "The caller lacks the required staff role."},
        404: {"description": "No such selected policy, product or report in this organization."},
        409: {"description": "A selected policy approval or financial binding changed."},
    },
)
_read = Depends(require_roles(*READ))


@router.post("/vendor-comparisons/preview", response_model=VendorComparisonResponse)
def compare(
    payload: VendorComparisonRequest,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> VendorComparisonResponse:
    return preview_vendor_comparison(session, principal, payload)


@router.get("/replenishment-review/summary", response_model=SavedReviewSummary)
def summary(principal: Principal = _read, session: Session = Depends(get_db)) -> SavedReviewSummary:
    return review_summary(session, principal)


@router.get("/replenishment-review", response_model=SavedReviewPage)
def history(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
    product_id: uuid.UUID | None = Query(None),
    vendor_id: uuid.UUID | None = Query(None),
    policy_id: uuid.UUID | None = Query(None),
    status: EvaluationStatus | None = Query(None),
    approval: Literal["ALL", "CURRENT", "CHANGED"] = Query("CURRENT"),
    shortfall_only: bool = Query(False),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> SavedReviewPage:
    return review_page(
        session,
        principal,
        page,
        page_size,
        q=q,
        product_id=product_id,
        vendor_id=vendor_id,
        policy_id=policy_id,
        status=status,
        approval=approval,
        shortfall_only=shortfall_only,
    )
