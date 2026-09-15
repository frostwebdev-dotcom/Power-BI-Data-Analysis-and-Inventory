"""Receiving a vendor file: retain it byte for byte, then record it (AC-5, ADR 0004).

Order matters and is the whole point:

1. refuse what cannot be an import (wrong extension, too large, empty)
   before anything touches disk;
2. write the bytes to storage exactly as received — **before** any parse,
   before any row is inserted (AC-5.4);
3. record ``import_files`` and a ``PENDING`` ``import_jobs`` row in one
   transaction with their audit entries.

If step 3 fails the stored object stays: it is content-addressed, so a
retry finds it and records it. Nothing in this module reads the file's
contents beyond hashing it and, for CSV, detecting the encoding.

Re-uploading identical bytes is detected by checksum (AC-5.6). While a job
for that file is live — anything but FAILED or CANCELLED, which is what the
partial unique index permits — the existing job is returned with
``duplicate=True`` and nothing is created. Once every job for the file has
failed or been cancelled, the same bytes may be imported again; the file is
not stored twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.imports.readers import detect_encoding
from app.imports.storage import StorageBackend, sha256_hex
from app.models.enums import ImportJobStatus, ImportRowStatus
from app.models.ingestion import ImportFile, ImportJob
from app.models.vendor import VendorImportProfile
from app.repositories.imports import ImportJobPage, ImportRepository, ImportRowPage
from app.services import audit
from app.services.import_profiles import ImportProfileNotActive, get_profile
from app.services.vendors import get_vendor

_logger = get_logger(__name__)

ALLOWED_EXTENSIONS: Final = frozenset({".csv", ".xlsx"})


class UnsupportedFileType(AppError):  # noqa: N818
    status_code = 415
    code = "unsupported_file_type"
    message = "Only .csv and .xlsx files can be imported."


class UploadTooLarge(AppError):  # noqa: N818
    status_code = 413
    code = "upload_too_large"
    message = "The file is larger than this system accepts."


class UploadEmpty(ValidationFailedError):  # noqa: N818
    code = "upload_empty"
    message = "The file has no content."


class ImportJobNotFound(NotFoundError):  # noqa: N818
    code = "import_job_not_found"
    message = "No such import in this organization."


class ImportFileBelongsToAnotherVendor(ConflictError):  # noqa: N818
    code = "import_file_belongs_to_another_vendor"
    message = "A file with these exact bytes was already received for a different vendor."


class ImportFileConflict(ConflictError):  # noqa: N818
    code = "import_file_conflict"
    message = "The same file was received concurrently; retry the upload."


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    job: ImportJob
    duplicate: bool


# --- receive -----------------------------------------------------------------------------


def receive_upload(
    session: Session,
    storage: StorageBackend,
    *,
    principal: Principal,
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    declared_mime: str | None,
    max_bytes: int,
) -> UploadOutcome:
    """See the module docstring. ``principal`` supplies the organization and
    the uploader."""
    name = _checked_filename(filename)
    if len(content) > max_bytes:
        raise UploadTooLarge(
            details={"size_bytes": len(content), "max_bytes": max_bytes},
        )
    if not content:
        raise UploadEmpty()

    vendor = get_vendor(session, principal, vendor_id)
    profile = _checked_profile(session, principal, vendor.id, profile_id)
    repository = ImportRepository(session, principal.organization_id)

    digest = sha256_hex(content)
    existing = repository.file_by_sha256(digest)
    if existing is not None:
        if existing.vendor_id != vendor.id:
            raise ImportFileBelongsToAnotherVendor(
                details={"import_file_id": str(existing.id), "vendor_id": str(existing.vendor_id)}
            )
        live = repository.live_job_for_file(existing.id)
        if live is not None:
            _logger.info(
                "import.duplicate_upload", import_file_id=str(existing.id), job_id=str(live.id)
            )
            return UploadOutcome(job=live, duplicate=True)
        if not storage.exists(existing.storage_uri):
            # Should never happen (ADR 0004). We hold the bytes, so restore
            # the object rather than fail; the warning is the paper trail.
            _logger.warning("import.stored_object_missing", uri=existing.storage_uri)
            storage.put(
                content, name, organization_id=principal.organization_id, at=existing.uploaded_at
            )
        job = _record_job(session, repository, principal, existing, profile, retry=True)
        return UploadOutcome(job=job, duplicate=False)

    # New bytes: stored first, recorded second (AC-5.4).
    uri = storage.put(content, name, organization_id=principal.organization_id)
    import_file = ImportFile(
        vendor_id=vendor.id,
        original_filename=name,
        storage_uri=uri,
        sha256=digest,
        size_bytes=len(content),
        declared_mime=declared_mime,
        detected_encoding=(detect_encoding(content) if _extension(name) == ".csv" else None),
        uploaded_by_user_id=principal.user_id,
    )
    try:
        with transaction(session):
            repository.add_file(import_file)
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="import_file.received",
                instance=import_file,
                before=None,
                actor=principal,
                summary=(
                    f"File {name} ({len(content)} bytes, sha256 {digest[:12]}…) "
                    f"retained for {vendor.code}."
                ),
            )
            job = _add_job(session, repository, principal, import_file, profile, retry=False)
    except IntegrityError as exc:
        raise ImportFileConflict() from exc
    _logger.info(
        "import.received",
        import_file_id=str(import_file.id),
        job_id=str(job.id),
        vendor_id=str(vendor.id),
        size_bytes=len(content),
        encoding=import_file.detected_encoding,
    )
    return UploadOutcome(job=job, duplicate=False)


# --- reads -------------------------------------------------------------------------------


def list_jobs(
    session: Session,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    vendor_id: uuid.UUID | None,
    status: ImportJobStatus | None,
) -> ImportJobPage:
    return ImportRepository(session, principal.organization_id).list_jobs(
        page=page, page_size=page_size, vendor_id=vendor_id, status=status
    )


def get_job(session: Session, principal: Principal, job_id: uuid.UUID) -> ImportJob:
    job = ImportRepository(session, principal.organization_id).get_job(job_id)
    if job is None:
        raise ImportJobNotFound()
    return job


def list_rows(
    session: Session,
    principal: Principal,
    job_id: uuid.UUID,
    *,
    page: int,
    page_size: int,
    status: ImportRowStatus | None,
    error_code: str | None,
) -> ImportRowPage:
    get_job(session, principal, job_id)
    return ImportRepository(session, principal.organization_id).list_rows(
        job_id, page=page, page_size=page_size, status=status, error_code=error_code
    )


@dataclass(frozen=True, slots=True)
class ImportReport:
    job: ImportJob
    by_status: dict[ImportRowStatus, int]
    by_error_code: dict[str, int]
    issues_by_code: dict[str, int]
    summary: str

    @property
    def counters(self) -> dict[str, int]:
        return {
            "total": self.job.total_rows,
            "processed": self.job.processed_rows,
            "ok": self.by_status.get(ImportRowStatus.OK, 0),
            "warning": self.by_status.get(ImportRowStatus.WARNING, 0),
            "error": self.by_status.get(ImportRowStatus.ERROR, 0),
            "skipped": self.by_status.get(ImportRowStatus.SKIPPED, 0),
            "matched": self.job.matched_rows,
            "exception": self.job.exception_rows,
        }


#: How each issue code reads in the report's summary sentence.
_ISSUE_PHRASES: Final = {
    "QUANTITY_INVALID": "with invalid quantities",
    "QUANTITY_NEGATIVE": "with negative quantities",
    "UPC_INVALID": "with invalid UPCs",
    "PRICE_INVALID": "with invalid prices",
    "PACK_SIZE_INVALID": "with invalid pack sizes",
    "IDENTIFIER_MISSING": "without a UPC or vendor SKU",
    "AVAILABILITY_UNKNOWN": "with unrecognised availability",
    "DUPLICATE_IN_FILE": "superseded by a later duplicate vendor SKU",
}


def build_report(session: Session, principal: Principal, job_id: uuid.UUID) -> ImportReport:
    """Counters from the job, breakdowns from the rows, and the sentence the
    brief asks for: "1,245 rows imported, 3 with invalid quantities, …"."""
    job = get_job(session, principal, job_id)
    repository = ImportRepository(session, principal.organization_id)
    by_status = repository.rows_by_status(job.id)
    by_error_code = repository.rows_by_error_code(job.id)
    parsing = job.error_details.get("parsing", {}) if isinstance(job.error_details, dict) else {}
    issues_by_code = {
        str(code): int(count) for code, count in (parsing.get("issue_counts") or {}).items()
    }
    return ImportReport(
        job=job,
        by_status=by_status,
        by_error_code=by_error_code,
        issues_by_code=issues_by_code,
        summary=_summarise(job, by_status, issues_by_code),
    )


def _summarise(
    job: ImportJob, by_status: dict[ImportRowStatus, int], issues_by_code: dict[str, int]
) -> str:
    if job.status is ImportJobStatus.PENDING:
        return "Waiting to be processed."
    if job.status is ImportJobStatus.FAILED:
        return f"Import failed: {job.error_message or 'no reason recorded'}"
    imported = by_status.get(ImportRowStatus.OK, 0) + by_status.get(ImportRowStatus.WARNING, 0)
    parts = [f"{imported:,} rows imported"]
    errors = by_status.get(ImportRowStatus.ERROR, 0)
    if errors:
        parts.append(f"{errors:,} rejected")
    for code, phrase in _ISSUE_PHRASES.items():
        count = issues_by_code.get(code, 0)
        if count:
            parts.append(f"{count:,} {phrase}")
    skipped = by_status.get(ImportRowStatus.SKIPPED, 0)
    if skipped:
        parts.append(f"{skipped:,} empty rows skipped")
    sentence = ", ".join(parts) + "."
    if job.status is ImportJobStatus.RUNNING:
        stage = job.current_stage.value.lower() if job.current_stage else "running"
        sentence += f" Import is still running ({stage})."
    return sentence


def open_raw(storage: StorageBackend, job: ImportJob) -> bytes:
    """The retained bytes for a job's file. Verified against the recorded
    checksum on the way out, so a corrupted object is an error, not data."""
    content = storage.open(job.import_file.storage_uri)
    if sha256_hex(content) != job.import_file.sha256:
        raise AppError(
            "The retained file no longer matches its recorded checksum.",
            code="import_file_integrity",
            status_code=500,
        )
    return content


# --- helpers -----------------------------------------------------------------------------


def _extension(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def _checked_filename(filename: str) -> str:
    # Only the final path component is kept: browsers may send a path.
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name:
        raise ValidationFailedError(details={"filename": "must not be blank"})
    extension = _extension(name)
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileType(
            details={"extension": extension or None, "allowed": sorted(ALLOWED_EXTENSIONS)}
        )
    return name


def _checked_profile(
    session: Session, principal: Principal, vendor_id: uuid.UUID, profile_id: uuid.UUID | None
) -> VendorImportProfile | None:
    if profile_id is None:
        return None
    # Scoped by vendor: another vendor's profile is a 404, not a mismatch.
    profile = get_profile(session, principal, vendor_id, profile_id)
    if not profile.is_active:
        raise ImportProfileNotActive()
    return profile


def _record_job(
    session: Session,
    repository: ImportRepository,
    principal: Principal,
    import_file: ImportFile,
    profile: VendorImportProfile | None,
    *,
    retry: bool,
) -> ImportJob:
    try:
        with transaction(session):
            return _add_job(session, repository, principal, import_file, profile, retry=retry)
    except IntegrityError as exc:
        raise ImportFileConflict() from exc


def _add_job(
    session: Session,
    repository: ImportRepository,
    principal: Principal,
    import_file: ImportFile,
    profile: VendorImportProfile | None,
    *,
    retry: bool,
) -> ImportJob:
    """Inside the caller's transaction: the PENDING job and its audit row."""
    job = ImportJob(
        vendor_id=import_file.vendor_id,
        import_file_id=import_file.id,
        vendor_import_profile_id=profile.id if profile is not None else None,
        profile_version=profile.version if profile is not None else None,
        status=ImportJobStatus.PENDING,
        triggered_by_user_id=principal.user_id,
    )
    repository.add_job(job)
    job.import_file = import_file
    audit.record_change(
        session,
        organization_id=principal.organization_id,
        action="import_job.created",
        instance=job,
        before=None,
        actor=principal,
        summary=(
            f"Import job created for {import_file.original_filename}"
            + (" (retry of a failed or cancelled import)" if retry else "")
            + (f" under profile {profile.name} v{profile.version}" if profile else "")
            + "."
        ),
    )
    return job
