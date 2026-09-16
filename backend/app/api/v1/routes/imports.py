"""Import routes (AC-5 part 1): upload, list, inspect, download the raw file.

Upload is ``DATA_OPERATOR``; everything else reads for every role. The raw
download returns the retained bytes exactly, under the original filename.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_storage, require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.config import Settings
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.storage import StorageBackend
from app.models.enums import ImportJobStage, ImportJobStatus, ImportRowStatus
from app.schemas.imports import (
    ImportCounters,
    ImportJobListResponse,
    ImportJobResponse,
    ImportReportResponse,
    ImportRowListResponse,
    ImportRowResponse,
    ImportUploadResponse,
)
from app.services import imports as service
from app.services.import_processing import process_import_job
from app.services.matching import match_import_job

router = APIRouter(prefix="/imports", tags=["imports"])

_read = Depends(require_roles(*READ_ROLES))
_write = Depends(require_roles(RoleCode.DATA_OPERATOR))

Responses = dict[int | str, dict[str, Any]]
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}
_NOT_FOUND: Responses = {404: {"description": "No such import, vendor or profile."}}
_UPLOAD_REFUSED: Responses = {
    409: {"description": "The bytes were already received for another vendor."},
    413: {"description": "The file exceeds IMPORT_MAX_UPLOAD_MB."},
    415: {"description": "The file is not .csv or .xlsx."},
    422: {"description": "The file is empty or the form is invalid."},
}

_READ_CHUNK = 1 << 20
_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post(
    "",
    response_model=ImportUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a vendor file; it is retained byte for byte and queued",
    responses={
        **_FORBIDDEN,
        **_NOT_FOUND,
        **_UPLOAD_REFUSED,
        200: {
            "model": ImportUploadResponse,
            "description": "Identical bytes are already queued or imported; that job is returned.",
        },
    },
)
async def upload_file(
    response: Response,
    file: Annotated[UploadFile, File(description="A .csv or .xlsx vendor file.")],
    vendor_id: Annotated[uuid.UUID, Form()],
    profile_id: Annotated[uuid.UUID | None, Form()] = None,
    principal: Principal = _write,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
) -> ImportUploadResponse:
    max_bytes = settings.import_max_upload_mb * 1024 * 1024
    content = await _read_bounded(file, max_bytes)
    outcome = service.receive_upload(
        session,
        storage,
        principal=principal,
        vendor_id=vendor_id,
        profile_id=profile_id,
        filename=file.filename or "",
        content=content,
        declared_mime=file.content_type,
        max_bytes=max_bytes,
    )
    job = outcome.job
    if outcome.duplicate:
        # Nothing was created: 200, not 201, with the existing job.
        response.status_code = status.HTTP_200_OK
    elif settings.import_process_on_upload and job.vendor_import_profile_id is not None:
        # Interim (phase 6): parse inside the request. Without a profile the
        # job waits as PENDING; a scheduled runner takes this over later.
        job = process_import_job(
            session,
            storage,
            organization_id=principal.organization_id,
            job_id=job.id,
            actor=principal,
        )
        if job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.MATCHING:
            job = match_import_job(
                session, organization_id=principal.organization_id, job_id=job.id, actor=principal
            )
    return ImportUploadResponse(
        job=ImportJobResponse.model_validate(job), duplicate=outcome.duplicate
    )


async def _read_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload, but stop as soon as it is over the limit rather than
    buffering an arbitrarily large body first."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise service.UploadTooLarge(details={"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


@router.get(
    "",
    response_model=ImportJobListResponse,
    summary="List imports, newest first",
    responses=_FORBIDDEN,
)
def list_imports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    vendor_id: uuid.UUID | None = Query(None),
    status_filter: ImportJobStatus | None = Query(None, alias="status"),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportJobListResponse:
    result = service.list_jobs(
        session,
        principal,
        page=page,
        page_size=page_size,
        vendor_id=vendor_id,
        status=status_filter,
    )
    return ImportJobListResponse(
        items=[ImportJobResponse.model_validate(j) for j in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="One import and its retained file",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_import(
    job_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportJobResponse:
    return ImportJobResponse.model_validate(service.get_job(session, principal, job_id))


@router.get(
    "/{job_id}/report",
    response_model=ImportReportResponse,
    summary="Counters, breakdown by issue code, and a one-sentence summary",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_report(
    job_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportReportResponse:
    report = service.build_report(session, principal, job_id)
    job = report.job
    return ImportReportResponse(
        job_id=job.id,
        status=job.status,
        current_stage=job.current_stage,
        counters=ImportCounters(**report.counters),
        by_error_code=report.by_error_code,
        issues_by_code=report.issues_by_code,
        summary=report.summary,
        error_message=job.error_message,
        error_details=job.error_details,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get(
    "/{job_id}/rows",
    response_model=ImportRowListResponse,
    summary="The staged rows of an import, in file order",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def list_rows(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: ImportRowStatus | None = Query(None, alias="status"),
    error_code: str | None = Query(None, max_length=64),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportRowListResponse:
    result = service.list_rows(
        session,
        principal,
        job_id,
        page=page,
        page_size=page_size,
        status=status_filter,
        error_code=error_code,
    )
    return ImportRowListResponse(
        items=[ImportRowResponse.model_validate(r) for r in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{job_id}/raw",
    summary="Download the retained file, byte for byte",
    response_class=StreamingResponse,
    responses={
        **_FORBIDDEN,
        **_NOT_FOUND,
        200: {"content": {"application/octet-stream": {}}, "description": "The original bytes."},
    },
)
def download_raw(
    job_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> StreamingResponse:
    job = service.get_job(session, principal, job_id)
    content = service.open_raw(storage, job)
    name = job.import_file.original_filename
    extension = name[name.rfind(".") :].lower() if "." in name else ""
    return StreamingResponse(
        _chunks(content),
        media_type=_MEDIA_TYPES.get(extension, "application/octet-stream"),
        headers={
            "Content-Disposition": _attachment(name),
            "Content-Length": str(len(content)),
            "X-Content-SHA256": job.import_file.sha256,
        },
    )


def _chunks(content: bytes) -> Iterator[bytes]:
    for start in range(0, len(content), _READ_CHUNK):
        yield content[start : start + _READ_CHUNK]


def _attachment(filename: str) -> str:
    """RFC 6266: an ASCII fallback plus the UTF-8 original."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
