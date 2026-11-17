"""Authenticated financial XLSX upload, immutable results and raw evidence."""

from __future__ import annotations

import json
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_storage, require_roles
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.storage import StorageBackend
from app.schemas.connectbooks import ConnectBooksXlsxProfile
from app.schemas.financial_imports import (
    FinancialImportPage,
    FinancialImportResponse,
    FinancialMappingStatus,
    FinancialRowPage,
    FinancialRowResponse,
    FinancialUploadResponse,
)
from app.services import financial_imports as service
from app.services.connectbooks import MAX_XLSX_BYTES
from app.services.profitability import ProfitabilityPreviewError

router = APIRouter(prefix="/financial-imports", tags=["financial imports"])
_read = Depends(require_roles(*service.FINANCIAL_READ_ROLES))
_write = Depends(require_roles(RoleCode.DATA_OPERATOR))


def _profile_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise service.FinancialInputInvalid(details={"code": "DUPLICATE_PROFILE_JSON_KEY"})
        result[key] = value
    return result


@router.post("", response_model=FinancialUploadResponse, status_code=201)
def upload_financial(
    response: Response,
    file: Annotated[UploadFile, File()],
    profile_json: Annotated[str, Form(min_length=1, max_length=65536)],
    principal: Principal = _write,
    session: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FinancialUploadResponse:
    try:
        profile = ConnectBooksXlsxProfile.model_validate(
            json.loads(profile_json, object_pairs_hook=_profile_object)
        )
    except (ValidationError, json.JSONDecodeError, ProfitabilityPreviewError):
        raise service.FinancialInputInvalid(details={"code": "INVALID_PROFILE"}) from None
    content = file.file.read(MAX_XLSX_BYTES + 1)
    outcome = service.receive_financial_import(
        session,
        storage,
        principal=principal,
        filename=file.filename or "",
        content=content,
        profile=profile,
    )
    if outcome.duplicate:
        response.status_code = 200
    return FinancialUploadResponse(
        batch=FinancialImportResponse.model_validate(outcome.batch), duplicate=outcome.duplicate
    )


@router.get("", response_model=FinancialImportPage)
def list_financial(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> FinancialImportPage:
    rows, total = service.financial_history(session, principal, page, page_size)
    return FinancialImportPage(
        items=[FinancialImportResponse.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{identifier}", response_model=FinancialImportResponse)
def get_financial(
    identifier: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> FinancialImportResponse:
    return FinancialImportResponse.model_validate(
        service.get_financial_import(session, principal, identifier)
    )


@router.get("/{identifier}/rows", response_model=FinancialRowPage)
def list_financial_rows(
    identifier: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    review_only: bool = Query(False),
    mapping_status: FinancialMappingStatus | None = Query(None),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> FinancialRowPage:
    rows, total = service.financial_rows(
        session,
        principal,
        identifier,
        page,
        page_size,
        review_only=review_only,
        mapping_status=mapping_status,
    )
    return FinancialRowPage(
        items=[FinancialRowResponse.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{identifier}/raw")
def raw_financial(
    identifier: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    batch = service.get_financial_import(session, principal, identifier)
    content = service.open_financial_raw(storage, batch)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="financial-report.xlsx"; '
            "filename*=UTF-8''" + quote(batch.original_filename, safe=""),
            "X-Content-SHA256": batch.raw_sha256,
        },
    )
