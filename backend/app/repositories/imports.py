"""Import file and job data access, through the tenant scope (ADR 0012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.enums import ImportJobStatus, ImportRowStatus
from app.models.ingestion import ImportFile, ImportJob, ImportJobRow
from app.repositories.scoping import ScopedRepository

MAX_PAGE_SIZE = 200

#: Statuses the partial unique index ``uq_import_jobs_active_import_file``
#: excludes: a file with only these jobs may be imported again.
RETRYABLE_STATUSES = frozenset({ImportJobStatus.FAILED, ImportJobStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class ImportRowPage:
    items: Sequence[ImportJobRow]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True, slots=True)
class ImportJobPage:
    items: Sequence[ImportJob]
    page: int
    page_size: int
    total: int


class ImportRepository(ScopedRepository):
    def file_by_sha256(self, sha256: str) -> ImportFile | None:
        return self.session.execute(
            self.select(ImportFile).where(ImportFile.sha256 == sha256)
        ).scalar_one_or_none()

    def live_job_for_file(self, import_file_id: uuid.UUID) -> ImportJob | None:
        """The one job the partial unique index allows: not FAILED or CANCELLED."""
        return self.session.execute(
            self.select(ImportJob)
            .options(selectinload(ImportJob.import_file))
            .where(
                ImportJob.import_file_id == import_file_id,
                ImportJob.status.not_in(RETRYABLE_STATUSES),
            )
        ).scalar_one_or_none()

    def get_job(self, job_id: uuid.UUID) -> ImportJob | None:
        return self.session.execute(
            self.select(ImportJob)
            .options(selectinload(ImportJob.import_file))
            .where(ImportJob.id == job_id)
        ).scalar_one_or_none()

    def list_jobs(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        vendor_id: uuid.UUID | None = None,
        status: ImportJobStatus | None = None,
    ) -> ImportJobPage:
        """Newest first."""
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        statement = self.select(ImportJob)
        if vendor_id is not None:
            statement = statement.where(ImportJob.vendor_id == vendor_id)
        if status is not None:
            statement = statement.where(ImportJob.status == status)

        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.options(selectinload(ImportJob.import_file))
                .order_by(ImportJob.created_at.desc(), ImportJob.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return ImportJobPage(items=items, page=page, page_size=page_size, total=total)

    def add_file(self, import_file: ImportFile) -> ImportFile:
        import_file.organization_id = self.scope.organization_id
        self.session.add(import_file)
        self.session.flush()
        return import_file

    def add_job(self, job: ImportJob) -> ImportJob:
        job.organization_id = self.scope.organization_id
        self.session.add(job)
        self.session.flush()
        return job

    # --- rows and report ---------------------------------------------------------------

    def list_rows(
        self,
        job_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 50,
        status: ImportRowStatus | None = None,
        error_code: str | None = None,
    ) -> ImportRowPage:
        """In file order, so a listing reads like the file (AC-9.5)."""
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        statement = self.select(ImportJobRow).where(ImportJobRow.import_job_id == job_id)
        if status is not None:
            statement = statement.where(ImportJobRow.status == status)
        if error_code is not None:
            statement = statement.where(ImportJobRow.error_code == error_code)
        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.order_by(ImportJobRow.row_number)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return ImportRowPage(items=items, page=page, page_size=page_size, total=total)

    def rows_by_status(self, job_id: uuid.UUID) -> dict[ImportRowStatus, int]:
        rows = self.session.execute(
            self.select(ImportJobRow, ImportJobRow.status, func.count())
            .where(ImportJobRow.import_job_id == job_id)
            .group_by(ImportJobRow.status)
        ).all()
        return {status: int(count) for status, count in rows}

    def rows_by_error_code(self, job_id: uuid.UUID) -> dict[str, int]:
        rows = self.session.execute(
            self.select(ImportJobRow, ImportJobRow.error_code, func.count())
            .where(ImportJobRow.import_job_id == job_id, ImportJobRow.error_code.is_not(None))
            .group_by(ImportJobRow.error_code)
        ).all()
        return {str(code): int(count) for code, count in rows}
