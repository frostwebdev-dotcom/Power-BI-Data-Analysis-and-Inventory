"""Parse a queued import into ``import_job_rows`` (phase 6, AC-5, AC-9).

``process_import_job`` takes a PENDING job and runs the parsing stage:

1. RUNNING / PARSING, audited; the retained bytes are re-hashed on the
   way in (ADR 0004);
2. the file's header is checked against the profile — a missing required
   column or a header-signature mismatch fails the job **before any row is
   written**, with ``error_details`` naming what was expected and what was
   observed (AC-6.4, AC-9.2);
3. rows stream through :mod:`app.imports.extract` and are written in
   chunks of ``CHUNK_ROWS``, each chunk its own transaction, with Core
   inserts so the session never accumulates 20,000 objects — memory holds
   one chunk plus one dict entry per distinct vendor SKU;
4. earlier occurrences of a vendor SKU repeated in the file are marked
   ``DUPLICATE_IN_FILE`` afterwards, in one bulk update — the last
   occurrence is the one that counts;
5. counters are reconciled from the rows themselves, the issue breakdown
   is stored on the job, and the job moves to stage MATCHING — still
   RUNNING, waiting for the matcher (phase 7).

A row-level problem never stops the file. A file-level problem, or any
unexpected exception, fails the job with a coded ``error_details`` and an
audit row; rows already written stay as evidence of how far it got.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.imports.extract import ExtractedRow, IssueCode, Severity, extract_row, plan_columns
from app.imports.mapping import header_issues
from app.imports.readers import SourceRow, UnreadableFile, open_rows
from app.imports.storage import StorageBackend, StorageError, sha256_hex
from app.models.enums import ActorType, ImportJobStage, ImportJobStatus, ImportRowStatus
from app.models.ingestion import ImportJob, ImportJobRow
from app.repositories.import_profiles import ImportProfileRepository
from app.repositories.imports import ImportRepository
from app.services import audit
from app.services.import_profiles import read_options_of, rules_of

_logger = get_logger(__name__)

CHUNK_ROWS: Final = 1000

#: File-level failure codes, stored in ``import_jobs.error_details["code"]``.
FAIL_PROFILE_MISSING: Final = "PROFILE_MISSING"
FAIL_FILE_INTEGRITY: Final = "FILE_INTEGRITY"
FAIL_FILE_UNREADABLE: Final = "FILE_UNREADABLE"
FAIL_REQUIRED_COLUMN_MISSING: Final = "REQUIRED_COLUMN_MISSING"
FAIL_HEADER_SIGNATURE_MISMATCH: Final = "HEADER_SIGNATURE_MISMATCH"
FAIL_INTERNAL: Final = "INTERNAL_ERROR"


class ImportJobNotPending(ConflictError):  # noqa: N818
    code = "import_job_not_pending"
    message = "Only a PENDING import can be started."


def process_import_job(
    session: Session,
    storage: StorageBackend,
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    actor: Principal | None = None,
) -> ImportJob:
    """Run the parsing stage. Returns the job in whatever state it ended."""
    repository = ImportRepository(session, organization_id)
    job = repository.get_job(job_id)
    if job is None:
        from app.services.imports import ImportJobNotFound

        raise ImportJobNotFound()
    if job.status is not ImportJobStatus.PENDING:
        raise ImportJobNotPending(details={"status": job.status.value})

    runner = _Run(session, storage, repository, job, organization_id, actor)
    try:
        runner.run()
    except Exception as exc:  # a bug or an outage: the job records it, the log has the trace
        _logger.error("import.parse_crashed", job_id=str(job.id), exc_info=True)
        session.rollback()
        runner.fail(
            FAIL_INTERNAL,
            f"The import stopped unexpectedly: {type(exc).__name__}.",
            {"exception": type(exc).__name__},
        )
    return job


class _Run:
    def __init__(
        self,
        session: Session,
        storage: StorageBackend,
        repository: ImportRepository,
        job: ImportJob,
        organization_id: uuid.UUID,
        actor: Principal | None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = repository
        self.job = job
        self.organization_id = organization_id
        self.actor = actor
        self.chunks_committed = 0

    # --- the stage ---------------------------------------------------------------------

    def run(self) -> None:
        job = self.job
        self._start()

        if job.vendor_import_profile_id is None:
            self.fail(
                FAIL_PROFILE_MISSING,
                "The import has no profile; a profile says how to read the file.",
                {},
            )
            return
        profile = ImportProfileRepository(self.session, self.organization_id).get(
            job.vendor_id, job.vendor_import_profile_id
        )
        if profile is None:
            self.fail(FAIL_PROFILE_MISSING, "The import's profile no longer exists.", {})
            return
        rules = rules_of(profile)
        options = read_options_of(profile)

        try:
            content = self.storage.open(job.import_file.storage_uri)
        except StorageError as exc:
            self.fail(
                FAIL_FILE_INTEGRITY, "The retained file could not be read.", {"reason": str(exc)}
            )
            return
        if sha256_hex(content) != job.import_file.sha256:
            self.fail(
                FAIL_FILE_INTEGRITY,
                "The retained file no longer matches its recorded checksum.",
                {"expected_sha256": job.import_file.sha256},
            )
            return

        try:
            stream = open_rows(content, options)
        except UnreadableFile as exc:
            self.fail(
                FAIL_FILE_UNREADABLE,
                "The file could not be read as the profile describes.",
                {"reason": str(exc)},
            )
            return

        plan = plan_columns(rules, stream.headers)
        signature, matches, notes = header_issues(plan, stream.headers, profile.header_signature)
        observed = {
            "observed_headers": stream.headers,
            "header_signature": signature,
            "encoding": stream.encoding,
            "sheet": stream.sheet,
        }
        if plan.missing_required:
            self.fail(
                FAIL_REQUIRED_COLUMN_MISSING,
                "A required column is missing from the file: "
                + ", ".join(f"{c.target} ({c.source!r})" for c in plan.missing_required)
                + ".",
                {
                    **observed,
                    "missing_columns": [
                        {"target": c.target, "source": c.source} for c in plan.missing_required
                    ],
                },
            )
            return
        if matches is False:
            self.fail(
                FAIL_HEADER_SIGNATURE_MISMATCH,
                "The file's columns differ from the ones the profile was built for.",
                {
                    **observed,
                    "expected_signature": profile.header_signature,
                    "expected_columns": [
                        {"target": c.target, "source": c.source} for c in plan.columns
                    ],
                },
            )
            return

        superseded = self._write_rows(rules, plan, stream.rows)
        self._mark_duplicates(superseded)
        summary = self._reconcile(
            {**observed, "notes": stream.notes + notes, "duplicates_superseded": len(superseded)}
        )
        self._finish(summary)

    # --- rows ------------------------------------------------------------------------------

    def _write_rows(
        self, rules: Any, plan: Any, rows: Iterator[SourceRow]
    ) -> list[tuple[uuid.UUID, int]]:
        """Stream, extract, and insert in chunks. Returns the rows superseded by
        a later occurrence of the same vendor SKU as (row id, later row number)."""
        job = self.job
        buffer: list[dict[str, Any]] = []
        seen: dict[str, uuid.UUID] = {}
        superseded: list[tuple[uuid.UUID, int]] = []

        def flush() -> None:
            if not buffer:
                return
            with transaction(self.session):
                self.session.execute(insert(ImportJobRow), buffer)
                job.total_rows += len(buffer)
                job.processed_rows += sum(
                    1 for r in buffer if r["status"] != ImportRowStatus.SKIPPED
                )
                job.skipped_rows += sum(1 for r in buffer if r["status"] == ImportRowStatus.SKIPPED)
                job.error_rows += sum(1 for r in buffer if r["status"] == ImportRowStatus.ERROR)
            self.chunks_committed += 1
            buffer.clear()

        for source in rows:
            row_id = uuid.uuid4()
            if source.is_blank:
                buffer.append(self._skipped_row(row_id, source.row_number))
            else:
                extracted = extract_row(rules, plan, source.row_number, source.cells)
                sku = extracted.normalized_vendor_sku
                if sku is not None:
                    if sku in seen:
                        superseded.append((seen[sku], source.row_number))
                    seen[sku] = row_id
                buffer.append(self._row_values(row_id, extracted))
            if len(buffer) >= CHUNK_ROWS:
                flush()
        flush()
        return superseded

    def _row_values(self, row_id: uuid.UUID, row: ExtractedRow) -> dict[str, Any]:
        primary = row.primary_issue
        return {
            "id": row_id,
            "organization_id": self.organization_id,
            "import_job_id": self.job.id,
            "row_number": row.row_number,
            "raw_data": row.raw,
            "normalized_data": row.normalized_json(),
            "vendor_sku": row.vendor_sku,
            "normalized_vendor_sku": row.normalized_vendor_sku,
            "raw_upc": row.raw_upc,
            "normalized_upc": row.normalized_upc,
            "description": row.description,
            "quantity": row.quantity,
            "unit_cost": row.unit_cost,
            "currency": row.currency,
            "availability_status": row.availability_status,
            "status": row.status,
            "error_code": primary.code.value if primary else None,
            "error_message": primary.message if primary else None,
        }

    def _skipped_row(self, row_id: uuid.UUID, row_number: int) -> dict[str, Any]:
        return {
            "id": row_id,
            "organization_id": self.organization_id,
            "import_job_id": self.job.id,
            "row_number": row_number,
            "raw_data": {},
            "normalized_data": {"issues": []},
            "status": ImportRowStatus.SKIPPED,
            "error_code": None,
            "error_message": "the row is empty",
        }

    def _mark_duplicates(self, superseded: list[tuple[uuid.UUID, int]]) -> None:
        """Earlier occurrences of a repeated vendor SKU become WARNING
        DUPLICATE_IN_FILE (an ERROR row stays ERROR); the issue is appended to
        the row's own list so it explains itself."""
        if not superseded:
            return
        statement = text(
            """
            update import_job_rows
               set status = case when status = 'ERROR' then status else 'WARNING' end,
                   error_code = coalesce(error_code, :code),
                   error_message = coalesce(error_message, :message),
                   normalized_data = normalized_data
                       || jsonb_build_object('superseded_by_row', cast(:later as integer))
                       || jsonb_build_object(
                              'issues',
                              coalesce(normalized_data -> 'issues', '[]'::jsonb)
                              || jsonb_build_array(jsonb_build_object(
                                     'code', cast(:code as text),
                                     'severity', cast(:severity as text),
                                     'field', 'vendor_sku',
                                     'message', cast(:message as text)))
                          )
             where id = :row_id and import_job_id = :job_id
            """
        )
        params = [
            {
                "row_id": row_id,
                "job_id": self.job.id,
                "later": later,
                "code": IssueCode.DUPLICATE_IN_FILE.value,
                "severity": Severity.WARNING.value,
                "message": f"vendor SKU repeated later in the file (row {later}); that row is used",
            }
            for row_id, later in superseded
        ]
        with transaction(self.session):
            self.session.execute(statement, params)

    def _reconcile(self, extra: dict[str, Any]) -> dict[str, Any]:
        """Counters from the rows themselves, so the job never disagrees with
        its rows (AC-9.4)."""
        job = self.job
        by_status: dict[ImportRowStatus, int] = {}
        for status_value, count in self.session.execute(
            select(ImportJobRow.status, func.count())
            .where(ImportJobRow.import_job_id == job.id)
            .group_by(ImportJobRow.status)
        ).all():
            by_status[status_value] = int(count)
        issue_counts: Counter[str] = Counter()
        for code, count in self.session.execute(
            text(
                """
                select issue ->> 'code', count(*)
                  from import_job_rows,
                       jsonb_array_elements(normalized_data -> 'issues') as issue
                 where import_job_id = :job_id
                 group by issue ->> 'code'
                """
            ),
            {"job_id": job.id},
        ).all():
            issue_counts[str(code)] = int(count)
        counts = {status.value: int(by_status.get(status, 0)) for status in ImportRowStatus}
        summary = {
            "by_status": counts,
            "issue_counts": dict(sorted(issue_counts.items())),
            "chunks": self.chunks_committed,
            **extra,
        }
        with transaction(self.session):
            job.total_rows = sum(counts.values())
            job.skipped_rows = counts[ImportRowStatus.SKIPPED.value]
            job.processed_rows = job.total_rows - job.skipped_rows
            job.error_rows = counts[ImportRowStatus.ERROR.value]
            job.error_details = {**job.error_details, "parsing": summary}
        return summary

    # --- lifecycle ---------------------------------------------------------------------

    def _start(self) -> None:
        job = self.job
        before = audit.snapshot(job)
        with transaction(self.session):
            job.status = ImportJobStatus.RUNNING
            job.current_stage = ImportJobStage.PARSING
            job.started_at = datetime.now(UTC)
            self._audit("import_job.started", before, "Import started: parsing.")

    def _finish(self, summary: dict[str, Any]) -> None:
        job = self.job
        before = audit.snapshot(job)
        with transaction(self.session):
            job.current_stage = ImportJobStage.MATCHING
            self._audit(
                "import_job.parsed",
                before,
                f"Parsed {job.total_rows} rows: {summary['by_status']['OK']} ok, "
                f"{summary['by_status']['WARNING']} warnings, {job.error_rows} errors, "
                f"{job.skipped_rows} skipped. Waiting for matching.",
            )
        _logger.info(
            "import.parsed",
            job_id=str(job.id),
            total_rows=job.total_rows,
            error_rows=job.error_rows,
            skipped_rows=job.skipped_rows,
            chunks=self.chunks_committed,
        )

    def fail(self, code: str, message: str, details: dict[str, Any]) -> None:
        job = self.job
        before = audit.snapshot(job)
        with transaction(self.session):
            job.status = ImportJobStatus.FAILED
            job.current_stage = None
            job.completed_at = datetime.now(UTC)
            if job.started_at is None:
                job.started_at = job.completed_at
            job.error_message = message
            job.error_details = {**job.error_details, "code": code, **details}
            self._audit("import_job.failed", before, f"Import failed ({code}): {message}")
        _logger.warning("import.failed", job_id=str(job.id), code=code)

    def _audit(self, action: str, before: dict[str, Any], summary: str) -> None:
        audit.record_change(
            self.session,
            organization_id=self.organization_id,
            action=action,
            instance=self.job,
            before=before,
            actor=self.actor,
            actor_type=None if self.actor else ActorType.WORKER,
            actor_label=None if self.actor else "import-runner",
            summary=summary,
        )
