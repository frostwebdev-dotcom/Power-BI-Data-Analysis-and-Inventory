"""Import-profile routes (AC-6).

Nested under a vendor. Reads for every role, writes for ``DATA_OPERATOR``.
Editing is versioning: ``PATCH`` returns the *new* version. The two validate
routes read an uploaded sample and return a preview; nothing is stored.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.errors import ValidationFailedError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.profile_rules import rule_json_schemas
from app.schemas.import_profiles import (
    ImportProfileCreate,
    ImportProfileListResponse,
    ImportProfileResponse,
    ImportProfileUpdate,
    RuleSchemasResponse,
    ValidationPreviewResponse,
)
from app.services import import_profiles as service

router = APIRouter(prefix="/vendors/{vendor_id}/import-profiles", tags=["import-profiles"])
schemas_router = APIRouter(prefix="/import-profiles", tags=["import-profiles"])

_read = Depends(require_roles(*READ_ROLES))
_write = Depends(require_roles(RoleCode.DATA_OPERATOR))

Responses = dict[int | str, dict[str, Any]]
_NOT_FOUND: Responses = {404: {"description": "No such vendor or profile in this organization."}}
_CONFLICT: Responses = {409: {"description": "The change conflicts with current state."}}
_FORBIDDEN: Responses = {403: {"description": "The caller lacks the required role."}}
_UNPROCESSABLE: Responses = {422: {"description": "The rules or the sample file are invalid."}}


@schemas_router.get(
    "/rule-schemas",
    response_model=RuleSchemasResponse,
    summary="JSON Schema for each rule column",
    responses=_FORBIDDEN,
)
def rule_schemas(principal: Principal = _read) -> RuleSchemasResponse:
    return RuleSchemasResponse(schemas=rule_json_schemas())


@router.get(
    "",
    response_model=ImportProfileListResponse,
    summary="A vendor's import profiles",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def list_profiles(
    vendor_id: uuid.UUID,
    include_inactive: bool = Query(False, description="Also return superseded versions."),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportProfileListResponse:
    profiles = service.list_profiles(
        session, principal, vendor_id, include_inactive=include_inactive
    )
    return ImportProfileListResponse(
        items=[ImportProfileResponse.model_validate(p) for p in profiles], total=len(profiles)
    )


@router.get(
    "/{profile_id}",
    response_model=ImportProfileResponse,
    summary="One profile version",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
def get_profile(
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ImportProfileResponse:
    return ImportProfileResponse.model_validate(
        service.get_profile(session, principal, vendor_id, profile_id)
    )


@router.post(
    "",
    response_model=ImportProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a profile (version 1)",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT, **_UNPROCESSABLE},
)
def create_profile(
    vendor_id: uuid.UUID,
    payload: ImportProfileCreate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> ImportProfileResponse:
    return ImportProfileResponse.model_validate(
        service.create_profile(session, principal, vendor_id, payload)
    )


@router.patch(
    "/{profile_id}",
    response_model=ImportProfileResponse,
    summary="Edit a profile — creates the next version and retires this one",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT, **_UNPROCESSABLE},
)
def update_profile(
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID,
    payload: ImportProfileUpdate,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> ImportProfileResponse:
    return ImportProfileResponse.model_validate(
        service.update_profile(session, principal, vendor_id, profile_id, payload)
    )


@router.post(
    "/{profile_id}/deactivate",
    response_model=ImportProfileResponse,
    summary="Deactivate the active version (never deleted)",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_CONFLICT},
)
def deactivate_profile(
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID,
    principal: Principal = _write,
    session: Session = Depends(get_db),
) -> ImportProfileResponse:
    return ImportProfileResponse.model_validate(
        service.deactivate_profile(session, principal, vendor_id, profile_id)
    )


# --- validate against a sample file ------------------------------------------------------


def _preview_response(file: UploadFile, preview: Any, table: Any) -> ValidationPreviewResponse:
    body = preview.as_json()
    return ValidationPreviewResponse(
        file_name=file.filename,
        encoding=table.encoding,
        sheet=table.sheet,
        truncated=table.truncated,
        notes=table.notes,
        **body,
    )


@router.post(
    "/{profile_id}/validate",
    response_model=ValidationPreviewResponse,
    summary="Preview how this stored profile would read a sample file",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_UNPROCESSABLE},
)
async def validate_stored_profile(
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="A CSV or XLSX sample.")],
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ValidationPreviewResponse:
    profile = service.get_profile(session, principal, vendor_id, profile_id)
    content = await file.read()
    preview, table = service.preview_file(
        service.rules_of(profile),
        service.read_options_of(profile),
        content,
        expected_signature=profile.header_signature,
    )
    return _preview_response(file, preview, table)


@router.post(
    "/validate",
    response_model=ValidationPreviewResponse,
    summary="Preview how a draft profile would read a sample file",
    responses={**_FORBIDDEN, **_NOT_FOUND, **_UNPROCESSABLE},
)
async def validate_draft_profile(
    vendor_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="A CSV or XLSX sample.")],
    profile: Annotated[str, Form(description="The draft profile as JSON (ImportProfileCreate).")],
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ValidationPreviewResponse:
    """The draft arrives as a JSON string in a form field alongside the file,
    since multipart cannot carry a JSON body part FastAPI would validate."""
    from app.services.vendors import get_vendor

    get_vendor(session, principal, vendor_id)
    try:
        draft = ImportProfileCreate.model_validate(json.loads(profile))
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(details={"profile": f"not JSON: {exc.msg}"}) from exc
    except ValidationError as exc:
        # Same shape as the body-validation handler; no raw input echoed back.
        fields = [
            {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        raise ValidationFailedError(details={"fields": fields}) from exc
    content = await file.read()
    preview, table = service.preview_file(
        draft.rules(),
        service.read_options_of(draft),
        content,
        expected_signature=draft.header_signature,
    )
    return _preview_response(file, preview, table)
