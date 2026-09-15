"""Import upload and job schemas (AC-5, ADR 0004)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ImportJobStatus


class ImportFileResponse(BaseModel):
    """The retained file: what was received and where it is. Never the bytes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    original_filename: str
    storage_uri: str
    sha256: str
    size_bytes: int
    declared_mime: str | None
    detected_encoding: str | None
    uploaded_by_user_id: uuid.UUID | None
    uploaded_at: datetime


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    vendor_id: uuid.UUID
    import_file: ImportFileResponse
    vendor_import_profile_id: uuid.UUID | None
    profile_version: int | None
    status: ImportJobStatus
    total_rows: int
    processed_rows: int
    matched_rows: int
    exception_rows: int
    error_rows: int
    skipped_rows: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    triggered_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ImportUploadResponse(BaseModel):
    """The job an upload resulted in.

    ``duplicate`` is true when the bytes were already retained and a live job
    exists for them (AC-5.6): the existing job is returned and nothing new
    was created.
    """

    job: ImportJobResponse
    duplicate: bool


class ImportJobListResponse(BaseModel):
    items: list[ImportJobResponse]
    page: int
    page_size: int
    total: int
