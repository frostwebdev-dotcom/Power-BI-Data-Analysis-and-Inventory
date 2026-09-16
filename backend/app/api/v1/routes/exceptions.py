"""Exception-queue routes (AC-8).

Reads for every role; approve / reject / defer for ``PURCHASING_MANAGER``
(``ADMIN`` passes every check).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.models.enums import ExceptionReason, ExceptionStatus
from app.models.matching import ProductMappingException
from app.schemas.exceptions import (
    ApproveRequest,
    Candidate,
    DeferRequest,
    ExceptionDetailResponse,
    ExceptionListResponse,
    ExceptionSummary,
    ListingSummary,
    ProductSummary,
    RejectRequest,
    ResolutionResponse,
    SourceRow,
    VendorLineSummary,
)
from app.services import exceptions as service

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

_read = Depends(require_roles(*READ_ROLES))
_resolve = Depends(require_roles(RoleCode.PURCHASING_MANAGER))

Responses = dict[int | str, dict[str, Any]]
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}
_NOT_FOUND: Responses = {404: {"description": "No such exception or product."}}
_CONFLICT: Responses = {
    409: {
        "description": (
            "The exception is not PENDING, or the mapping needs supersede=true "
            "because an approved mapping to another product exists."
        )
    }
}


def _summary(item: ProductMappingException) -> ExceptionSummary:
    row = item.import_job_row
    line = item.vendor_product
    body = ExceptionSummary.model_validate(item)
    body.vendor_sku = (row.vendor_sku if row is not None else None) or (
        line.vendor_sku if line is not None else None
    )
    body.description = (row.description if row is not None else None) or (
        line.vendor_description if line is not None else None
    )
    body.row_number = row.row_number if row is not None else None
    body.age_hours = round((datetime.now(UTC) - item.created_at).total_seconds() / 3600, 2)
    return body


def _detail(
    session: Session, principal: Principal, item: ProductMappingException
) -> ExceptionDetailResponse:
    products = service.candidate_products(session, principal, item)
    candidates: list[Candidate] = []
    for raw in item.candidates.get("products", []):
        try:
            product_id = uuid.UUID(str(raw.get("product_id")))
        except ValueError:
            continue
        product = products.get(product_id)
        candidates.append(
            Candidate(
                product_id=product_id,
                rule=str(raw.get("rule", "")),
                priority=int(raw.get("priority", 0)),
                identifier=raw.get("identifier"),
                score=raw.get("score"),
                reason=raw.get("reason"),
                product=ProductSummary.model_validate(product) if product is not None else None,
            )
        )
    summary = _summary(item)
    return ExceptionDetailResponse(
        **summary.model_dump(),
        candidates=candidates,
        match_evaluations=item.match_evaluations,
        source_row=(
            SourceRow.model_validate(item.import_job_row)
            if item.import_job_row is not None
            else None
        ),
        vendor_line=(
            VendorLineSummary.model_validate(item.vendor_product)
            if item.vendor_product is not None
            else None
        ),
        listing=(
            ListingSummary.model_validate(item.marketplace_listing)
            if item.marketplace_listing is not None
            else None
        ),
        suggested_product=(
            ProductSummary.model_validate(item.suggested_product)
            if item.suggested_product is not None
            else None
        ),
        resolved_product=(
            ProductSummary.model_validate(item.resolved_product)
            if item.resolved_product is not None
            else None
        ),
    )


@router.get(
    "",
    response_model=ExceptionListResponse,
    summary="The exception queue, oldest first",
    responses=_FORBIDDEN,
)
def list_exceptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: ExceptionStatus | None = Query(
        ExceptionStatus.PENDING, alias="status", description="Omit the value for every status."
    ),
    reason: ExceptionReason | None = Query(None),
    vendor_id: uuid.UUID | None = Query(None),
    import_job_id: uuid.UUID | None = Query(None),
    min_age_hours: float | None = Query(None, ge=0, description="Only items at least this old."),
    include_deferred: bool = Query(False, description="Also show items deferred to the future."),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ExceptionListResponse:
    result = service.list_exceptions(
        session,
        principal,
        page=page,
        page_size=page_size,
        status=status_filter,
        reason=reason,
        vendor_id=vendor_id,
        import_job_id=import_job_id,
        min_age_hours=min_age_hours,
        include_deferred=include_deferred,
    )
    return ExceptionListResponse(
        items=[_summary(i) for i in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{exception_id}",
    response_model=ExceptionDetailResponse,
    summary="One exception: the source row, the candidates, the rule trail",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_exception(
    exception_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ExceptionDetailResponse:
    return _detail(session, principal, service.get_exception(session, principal, exception_id))


@router.post(
    "/{exception_id}/approve",
    response_model=ResolutionResponse,
    summary="Approve: a permanent mapping to the product",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def approve(
    exception_id: uuid.UUID,
    payload: ApproveRequest,
    principal: Principal = _resolve,
    session: Session = Depends(get_db),
) -> ResolutionResponse:
    resolution = service.approve(session, principal, exception_id, payload)
    return ResolutionResponse(
        exception=_detail(session, principal, resolution.item),
        superseded_product_id=resolution.superseded_product_id,
    )


@router.post(
    "/{exception_id}/reject",
    response_model=ResolutionResponse,
    summary="Reject: no mapping is made",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def reject(
    exception_id: uuid.UUID,
    payload: RejectRequest,
    principal: Principal = _resolve,
    session: Session = Depends(get_db),
) -> ResolutionResponse:
    resolution = service.reject(session, principal, exception_id, payload)
    return ResolutionResponse(exception=_detail(session, principal, resolution.item))


@router.post(
    "/{exception_id}/defer",
    response_model=ResolutionResponse,
    summary="Defer: stays pending, leaves the queue view until a time",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def defer(
    exception_id: uuid.UUID,
    payload: DeferRequest,
    principal: Principal = _resolve,
    session: Session = Depends(get_db),
) -> ResolutionResponse:
    resolution = service.defer(session, principal, exception_id, payload)
    return ResolutionResponse(exception=_detail(session, principal, resolution.item))
