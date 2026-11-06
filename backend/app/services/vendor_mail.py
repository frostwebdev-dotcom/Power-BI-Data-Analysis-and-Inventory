"""Audited source management and safe, restartable email inventory ingestion."""

from __future__ import annotations

import fnmatch
import hashlib
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.security import Principal
from app.db.transaction import transaction
from app.imports.storage import StorageBackend
from app.integrations.vendor_mail.downloads import approved_initial_link, download_file, email_links
from app.integrations.vendor_mail.types import IntakeError, MailAttachment, MailMessage, MailReader
from app.models.enums import ImportJobStage, ImportJobStatus
from app.models.ingestion import ImportJob
from app.models.vendor_mail import VendorMailDelivery, VendorMailSource, VendorMailState
from app.repositories.imports import ImportRepository
from app.repositories.scoping import TenantScope
from app.repositories.vendor_mail import VendorMailRepository
from app.schemas.vendor_mail import MailSourceResponse, MailSourceWrite
from app.services import audit
from app.services.import_processing import process_import_job
from app.services.import_profiles import get_profile
from app.services.imports import receive_upload
from app.services.matching import match_import_job
from app.services.snapshots import snapshot_import_job
from app.services.system_user import ensure_system_user
from app.services.vendors import get_vendor

MAX_ATTEMPTS = 5
SUCCESS = ("COMPLETED", "DUPLICATE")
TERMINAL = (*SUCCESS, "NEEDS_REVIEW", "SUPERSEDED")


class MailSourceNotFound(NotFoundError):  # noqa: N818
    code = "vendor_mail_source_not_found"
    message = "No such automatic import source in this organization."


class MailDeliveryNotFound(NotFoundError):  # noqa: N818
    code = "vendor_mail_delivery_not_found"
    message = "No such email delivery in this organization."


class MailSourceConflict(ConflictError):  # noqa: N818
    code = "vendor_mail_source_conflict"
    message = "The source name already exists, or the selected vendor/profile is inactive."


def account_key(settings: Settings) -> str:
    value = "|".join(
        (
            str(settings.vendor_mail_tenant_id),
            (settings.vendor_mail_mailbox or "").lower(),
            settings.vendor_mail_folder_id,
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


def source_response(source: VendorMailSource, at: datetime | None = None) -> MailSourceResponse:
    at = at or datetime.now(UTC)
    freshness = "current"
    if not source.is_active:
        freshness = "paused"
    elif source.last_received_at is None:
        freshness = (
            "stale"
            if source.stale_after_hours is not None
            and at - source.starts_at > timedelta(hours=source.stale_after_hours)
            else "never_received"
        )
    elif source.last_success_at is None or source.last_received_at > source.last_success_at:
        freshness = "awaiting_import"
    elif source.stale_after_hours is None:
        freshness = "irregular"
    elif at - source.last_success_at > timedelta(hours=source.stale_after_hours):
        freshness = "stale"
    return MailSourceResponse.model_validate(
        {
            **MailSourceWrite.model_validate(source, from_attributes=True).model_dump(),
            "id": source.id,
            "vendor_code": source.vendor.code,
            "profile_name": source.profile.name,
            "profile_version": source.profile.version,
            "starts_at": source.starts_at,
            "last_received_at": source.last_received_at,
            "last_success_at": source.last_success_at,
            "freshness": freshness,
        }
    )


def save_source(
    session: Session,
    actor: Principal,
    payload: MailSourceWrite,
    source_id: uuid.UUID | None = None,
) -> VendorMailSource:
    vendor = get_vendor(session, actor, payload.vendor_id)
    profile = get_profile(session, actor, vendor.id, payload.profile_id)
    if not vendor.is_active or not profile.is_active:
        raise MailSourceConflict()
    if payload.is_active and not profile.header_signature:
        raise MailSourceConflict(
            message=(
                "Validate and pin the import profile against a sample file "
                "before enabling automatic imports."
            )
        )
    repo = VendorMailRepository(session, actor.organization_id)
    row = repo.source(source_id) if source_id else None
    if source_id and row is None:
        raise MailSourceNotFound()
    if row is not None and row.vendor_id != vendor.id:
        raise MailSourceConflict(message="Create a new source to change its vendor.")
    before = audit.snapshot(row) if row is not None else None
    if row is None:
        row = VendorMailSource(organization_id=actor.organization_id, **payload.model_dump())
    else:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
    row.vendor, row.profile = vendor, profile
    try:
        with transaction(session):
            session.add(row)
            session.flush()
            audit.record_change(
                session,
                organization_id=actor.organization_id,
                action="vendor_mail.source.updated" if before else "vendor_mail.source.created",
                instance=row,
                before=before,
                actor=actor,
                summary=f"Automatic vendor inventory source {row.name} configured.",
            )
    except IntegrityError:
        raise MailSourceConflict() from None
    return row


def retry_delivery(
    session: Session,
    actor: Principal,
    delivery_id: uuid.UUID,
    *,
    use_current_profile: bool = False,
) -> VendorMailDelivery:
    repo = VendorMailRepository(session, actor.organization_id)
    row = repo.delivery(delivery_id)
    if row is None:
        raise MailDeliveryNotFound()
    if row.status not in ("FAILED", "NEEDS_REVIEW"):
        raise ConflictError(
            message="Only failed deliveries or deliveries requiring review can retry."
        )
    source = repo.source(row.source_id)
    if source is None or not source.is_active:
        raise ConflictError(message="Activate the source before retrying.")
    before = _delivery_snapshot(row)
    if use_current_profile:
        profile = get_profile(session, actor, source.vendor_id, source.profile_id)
        if not profile.is_active:
            raise MailSourceConflict()
        if row.import_job_id:
            job = ImportRepository(session, actor.organization_id).get_job(row.import_job_id)
            if job and job.status in (ImportJobStatus.RUNNING, ImportJobStatus.PENDING):
                raise ConflictError(
                    message="Recover the current import before changing its profile."
                )
        row.profile_id = profile.id
        row.import_job_id = None
    with transaction(session):
        row.status = "PENDING"
        row.attempts = 0
        row.next_attempt_at = None
        row.error_code = None
        row.completed_at = None
        _record_delivery(session, row, "vendor_mail.delivery.retry_requested", before, actor=actor)
    return row


def _delivery_snapshot(row: VendorMailDelivery) -> dict[str, object]:
    # Graph IDs and private signed links never reach audit/public responses.
    return {
        "status": row.status,
        "profile_id": str(row.profile_id),
        "attempts": row.attempts,
        "error_code": row.error_code,
        "import_job_id": str(row.import_job_id) if row.import_job_id else None,
    }


def _record_delivery(
    session: Session,
    row: VendorMailDelivery,
    action: str,
    before: dict[str, object] | None,
    *,
    actor: Principal | None = None,
) -> None:
    audit.record(
        session,
        organization_id=row.organization_id,
        action=action,
        entity_type="vendor_mail_deliveries",
        entity_id=row.id,
        actor=actor,
        actor_label=None if actor else "vendor-inventory-email",
        before=before,
        after=_delivery_snapshot(row),
        summary=f"Vendor inventory email: {row.status.lower()}.",
    )


@contextmanager
def mailbox_lock(session: Session, organization_id: uuid.UUID, key: str) -> Iterator[bool]:
    # Hold a dedicated connection: committing an import must not return a
    # session-level advisory lock to the pool while the run is still active.
    bind = session.get_bind()
    context = (
        bind.connect().execution_options(isolation_level="AUTOCOMMIT")
        if isinstance(bind, Engine)
        else nullcontext(bind)
    )
    lock_id = int.from_bytes(
        hashlib.sha256(f"{organization_id}:{key}".encode()).digest()[:8], signed=True
    )
    with context as connection:
        assert isinstance(connection, Connection)
        acquired = bool(
            connection.scalar(text("select pg_try_advisory_lock(:key)"), {"key": lock_id})
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("select pg_advisory_unlock(:key)"), {"key": lock_id})


def matching_sources(
    sources: list[VendorMailSource], message: MailMessage
) -> list[VendorMailSource]:
    return [
        s
        for s in sources
        if s.is_active
        and message.sender.lower() in s.sender_addresses
        and message.received_at >= s.starts_at
        and (not s.subject_contains or s.subject_contains.casefold() in message.subject.casefold())
    ]


def _candidates(
    source: VendorMailSource,
    message: MailMessage,
    attachments: list[MailAttachment],
) -> list[tuple[str, str, str | None]]:
    if source.mode == "ATTACHMENT":
        return [
            (a.id, a.name, a.id)
            for a in attachments
            if a.is_file
            and not a.inline
            and PurePosixPath(a.name).suffix.lower() in (".xls", ".xlsx", ".csv")
            and fnmatch.fnmatchcase(a.name.lower(), source.filename_pattern.lower())
        ]
    return [
        (
            hashlib.sha256(url.encode()).hexdigest(),
            source.download_filename or "inventory.xlsx",
            None,
        )
        for url in email_links(message.body)
        if approved_initial_link(url, source.download_hosts, source.download_path_prefix or "")
    ]


def discover_message(
    session: Session,
    repo: VendorMailRepository,
    state: VendorMailState,
    sources: list[VendorMailSource],
    message: MailMessage,
    reader: MailReader,
) -> None:
    matched = matching_sources(sources, message)
    if not matched:
        return
    attachments = (
        reader.attachments(message.id)
        if message.has_attachments and any(s.mode == "ATTACHMENT" for s in matched)
        else []
    )
    selected = [(source, _candidates(source, message, attachments)) for source in matched]
    vendors = {source.vendor_id for source, candidates in selected if candidates}
    any_candidates = any(candidates for _, candidates in selected)
    for source, candidates in selected:
        if repo.by_message(state.id, source.id, message.id):
            continue
        # A different filename can legitimately belong to another rule in the
        # same message. Don't create spurious failures for those unmatched rules.
        if not candidates and any_candidates:
            continue
        error = (
            "AMBIGUOUS_VENDOR"
            if len(vendors) > 1
            else (
                "MULTIPLE_MATCHING_FILES"
                if len(candidates) > 1
                else "NO_MATCHING_FILE_OR_LINK"
                if not candidates
                else None
            )
        )
        locator, filename, attachment_id = (
            candidates[0] if len(candidates) == 1 else ("review", "Review email source", None)
        )
        item_key = hashlib.sha256(locator.encode()).hexdigest()
        with transaction(session):
            row = VendorMailDelivery(
                organization_id=repo.scope.organization_id,
                state_id=state.id,
                source_id=source.id,
                profile_id=source.profile_id,
                message_id=message.id,
                item_key=item_key,
                kind=source.mode,
                attachment_id=attachment_id,
                filename=filename[:255],
                received_at=message.received_at,
                status="NEEDS_REVIEW" if error else "PENDING",
                error_code=error,
            )
            session.add(row)
            session.flush()
            source.last_received_at = max(
                source.last_received_at or message.received_at, message.received_at
            )
            _record_delivery(session, row, "vendor_mail.delivery.received", None)


def _content(
    reader: MailReader,
    source: VendorMailSource,
    row: VendorMailDelivery,
    max_bytes: int,
) -> bytes:
    review_key = hashlib.sha256(b"review").hexdigest()
    if row.item_key == review_key:
        message = reader.message(row.message_id)
        attachments = reader.attachments(row.message_id) if source.mode == "ATTACHMENT" else []
        candidates = _candidates(source, message, attachments)
        if len(candidates) != 1:
            raise IntakeError("FILE_SELECTION_REQUIRES_REVIEW", review=True)
        locator, row.filename, row.attachment_id = candidates[0]
        row.kind, row.item_key = source.mode, hashlib.sha256(locator.encode()).hexdigest()
    if row.kind == "ATTACHMENT" and row.attachment_id:
        return reader.attachment(row.message_id, row.attachment_id, max_bytes)
    message = reader.message(row.message_id)
    if message.sender.lower() not in source.sender_addresses:
        raise IntakeError("SENDER_RULE_CHANGED", review=True)
    if row.kind == "LINK":
        for url in email_links(message.body):
            key = hashlib.sha256(hashlib.sha256(url.encode()).hexdigest().encode()).hexdigest()
            if key == row.item_key and approved_initial_link(
                url, source.download_hosts, source.download_path_prefix or ""
            ):
                return download_file(url, hosts=source.download_hosts, max_bytes=max_bytes)
        raise IntakeError("DOWNLOAD_LINK_MISSING_OR_CHANGED", review=True)
    # A deliberate retry after a routing correction may discover one candidate.
    attachments = reader.attachments(row.message_id) if source.mode == "ATTACHMENT" else []
    candidates = _candidates(source, message, attachments)
    if len(candidates) != 1:
        raise IntakeError("FILE_SELECTION_REQUIRES_REVIEW", review=True)
    locator, row.filename, row.attachment_id = candidates[0]
    row.kind = source.mode
    row.item_key = hashlib.sha256(locator.encode()).hexdigest()
    return _content(reader, source, row, max_bytes)


def _system_principal(session: Session, organization_id: uuid.UUID) -> Principal:
    with transaction(session):
        user = ensure_system_user(session, organization_id)
    return Principal(user.id, organization_id, user.email, user.display_name, frozenset())


def _job_for_delivery(session: Session, row: VendorMailDelivery) -> ImportJob | None:
    return (
        ImportRepository(session, row.organization_id).get_job(row.import_job_id)
        if row.import_job_id
        else None
    )


def _finish_job(
    session: Session, storage: StorageBackend, row: VendorMailDelivery, job: ImportJob
) -> ImportJob:
    if job.status is ImportJobStatus.PENDING:
        job = process_import_job(
            session, storage, organization_id=row.organization_id, job_id=job.id
        )
    if job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.MATCHING:
        if job.error_rows:
            with transaction(session):
                before = audit.snapshot(job)
                job.status = ImportJobStatus.FAILED
                job.current_stage = None
                job.completed_at = datetime.now(UTC)
                job.error_message = (
                    "Automatic import held: correct invalid source rows before applying stock."
                )
                audit.record_change(
                    session,
                    organization_id=row.organization_id,
                    action="import.auto_validation_failed",
                    instance=job,
                    before=before,
                    actor_label="vendor-inventory-email",
                )
            raise IntakeError("SOURCE_ROWS_INVALID", review=True)
        job = match_import_job(session, organization_id=row.organization_id, job_id=job.id)
    if job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.SNAPSHOTTING:
        job = snapshot_import_job(session, organization_id=row.organization_id, job_id=job.id)
    return job


def apply_delivery(
    session: Session,
    repo: VendorMailRepository,
    row: VendorMailDelivery,
    reader: MailReader,
    storage: StorageBackend,
    settings: Settings,
    at: datetime,
) -> None:
    source = repo.source(row.source_id)
    if source is None or not source.is_active:
        return
    before = _delivery_snapshot(row)
    with transaction(session):
        row.status = "RUNNING"
        row.attempts += 1
        row.next_attempt_at = None
        _record_delivery(session, row, "vendor_mail.delivery.started", before)
    try:
        actor = _system_principal(session, row.organization_id)
        vendor = get_vendor(session, actor, source.vendor_id)
        profile = get_profile(session, actor, vendor.id, row.profile_id)
        if not vendor.is_active or not profile.is_active:
            raise IntakeError("VENDOR_OR_PROFILE_INACTIVE", review=True)
        if not profile.header_signature:
            raise IntakeError("PROFILE_NOT_VALIDATED", review=True)
        message = reader.message(row.message_id)
        if source not in matching_sources([source], message):
            raise IntakeError("SENDER_RULE_CHANGED", review=True)
        attachments = reader.attachments(message.id) if message.has_attachments else []
        matches = matching_sources(repo.sources(active_only=True), message)
        vendors = {
            candidate.vendor_id
            for candidate in matches
            if _candidates(candidate, message, attachments)
        }
        if len(vendors) > 1:
            raise IntakeError("AMBIGUOUS_VENDOR", review=True)
        job = _job_for_delivery(session, row)
        # Recover interrupted matching/snapshotting before accepting a later file.
        unfinished = session.scalar(
            repo.select(VendorMailDelivery)
            .join(
                ImportJob,
                ImportJob.id == VendorMailDelivery.import_job_id,
            )
            .where(
                VendorMailDelivery.source_id == source.id,
                VendorMailDelivery.id != row.id,
                ImportJob.status.in_((ImportJobStatus.PENDING, ImportJobStatus.RUNNING)),
            )
            .limit(1)
        )
        if unfinished is not None:
            raise IntakeError("PRIOR_IMPORT_INCOMPLETE", retryable=True)
        if source.last_success_at and row.received_at < source.last_success_at:
            if job and job.status in (ImportJobStatus.RUNNING, ImportJobStatus.PENDING):
                raise IntakeError("INTERRUPTED_OLDER_IMPORT_REQUIRES_REVIEW", review=True)
            _set_result(session, row, "SUPERSEDED", "NEWER_FILE_ALREADY_APPLIED", at)
            return
        duplicate = False
        if job is None or job.status in (ImportJobStatus.FAILED, ImportJobStatus.CANCELLED):
            content = _content(reader, source, row, settings.import_max_upload_mb * 1024 * 1024)
            if len(content) > settings.import_max_upload_mb * 1024 * 1024:
                raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
            outcome = receive_upload(
                session,
                storage,
                principal=actor,
                vendor_id=source.vendor_id,
                profile_id=row.profile_id,
                filename=row.filename,
                content=content,
                declared_mime=None,
                max_bytes=settings.import_max_upload_mb * 1024 * 1024,
            )
            job, duplicate = outcome.job, outcome.duplicate
            with transaction(session):
                row.import_job_id = job.id
        if job.status is ImportJobStatus.COMPLETED:
            # Reappearing historical bytes must not make a newer, different
            # stock file look current merely because content was seen before.
            # Recheck linked jobs too: ordinary retries keep their original ID.
            latest = session.scalar(
                TenantScope(row.organization_id)
                .select(ImportJob)
                .where(
                    ImportJob.vendor_id == source.vendor_id,
                    ImportJob.source_sheet == job.source_sheet,
                    ImportJob.status == ImportJobStatus.COMPLETED,
                )
                .order_by(ImportJob.completed_at.desc())
                .limit(1)
            )
            if latest and latest.id != job.id:
                raise IntakeError("OLDER_CONTENT_REAPPEARED", review=True)
        job = _finish_job(session, storage, row, job)
        if job.status is ImportJobStatus.COMPLETED and job.error_rows == 0:
            with transaction(session):
                old = _delivery_snapshot(row)
                row.status = "DUPLICATE" if duplicate else "COMPLETED"
                row.error_code = None
                row.completed_at = at
                source.last_success_at = max(
                    source.last_success_at or row.received_at, row.received_at
                )
                _record_delivery(session, row, "vendor_mail.delivery.completed", old)
        elif job.status is ImportJobStatus.RUNNING:
            raise IntakeError("IMPORT_INTERRUPTED", retryable=True)
        else:
            raise IntakeError("IMPORT_REQUIRES_REVIEW", review=True)
    except IntakeError as error:
        session.rollback()
        # The row can be expired by a rollback; identity remains stable.
        row = repo.delivery(row.id) or row
        retry = error.retryable and row.attempts < MAX_ATTEMPTS
        with transaction(session):
            old = _delivery_snapshot(row)
            row.status = "FAILED" if retry else "NEEDS_REVIEW"
            row.error_code = error.code
            row.next_attempt_at = (
                at + timedelta(minutes=min(60, 2**row.attempts)) if retry else None
            )
            row.completed_at = None if retry else at
            _record_delivery(session, row, "vendor_mail.delivery.failed", old)
    except AppError as error:
        session.rollback()
        _set_result(session, row, "NEEDS_REVIEW", error.code.upper()[:64], at)
    except Exception:
        session.rollback()
        # Unexpected provider errors never leak signed URLs through str(exc).
        _set_result(session, row, "NEEDS_REVIEW", "INTAKE_INTERNAL_ERROR", at)


def _set_result(
    session: Session, row: VendorMailDelivery, status: str, code: str, at: datetime
) -> None:
    with transaction(session):
        old = _delivery_snapshot(row)
        row.status, row.error_code, row.completed_at = status, code, at
        row.next_attempt_at = None
        _record_delivery(session, row, "vendor_mail.delivery.finished", old)


def run_intake(
    session: Session,
    organization_id: uuid.UUID,
    settings: Settings,
    reader: MailReader,
    storage: StorageBackend,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    key = account_key(settings)
    with mailbox_lock(session, organization_id, key) as acquired:
        if not acquired:
            return "BUSY"
        repo = VendorMailRepository(session, organization_id)
        sources = repo.sources(active_only=True)
        if not sources:
            return "NO_ACTIVE_SOURCES"
        at = now()
        state = repo.state(key)
        if state is None:
            with transaction(session):
                state = VendorMailState(
                    organization_id=organization_id,
                    account_key=key,
                    initial_since=at - timedelta(hours=settings.vendor_mail_lookback_hours),
                )
                session.add(state)
                session.flush()
        with transaction(session):
            state.last_poll_at = at
        try:
            # Checkpoint only after every routed delivery in that page commits.
            # Crash replay is safe because delivery identity and raw-file identity
            # are separately unique. No body or signed URL is persisted.
            for _ in range(settings.vendor_mail_max_pages):
                page = reader.page(state.checkpoint_url, state.initial_since)
                for message in page.messages:
                    discover_message(session, repo, state, sources, message, reader)
                with transaction(session):
                    state.checkpoint_url = page.checkpoint
                if not page.more:
                    break
            with transaction(session):
                state.last_success_at = now()
                state.error_code = None
        except IntakeError as error:
            session.rollback()
            with transaction(session):
                state.error_code = error.code
                if error.code == "GRAPH_CURSOR_EXPIRED":
                    state.checkpoint_url = None  # replay from original lower bound
        except Exception:
            session.rollback()
            with transaction(session):
                state.error_code = "MAILBOX_POLL_INTERNAL_ERROR"
        for row in repo.due(state.id, now(), settings.vendor_mail_max_deliveries):
            apply_delivery(session, repo, row, reader, storage, settings, now())
        return "COMPLETED" if state.error_code is None else "POLL_FAILED"


def configured_organization(session: Session, settings: Settings) -> uuid.UUID:
    if not settings.vendor_mail_configured:
        raise IntakeError("MAILBOX_NOT_CONFIGURED")
    organization = session.scalar(
        text("select id from organizations where slug=:slug and is_active=true"),
        {"slug": settings.vendor_mail_organization_slug},
    )
    if not isinstance(organization, uuid.UUID):
        raise IntakeError("MAILBOX_ORGANIZATION_NOT_FOUND")
    return organization
