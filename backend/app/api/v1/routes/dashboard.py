"""The dashboard's numbers (phase 10). Every figure is a live count; nothing
is cached or invented."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.security import Principal
from app.db.session import get_db
from app.models.amazon import AmazonSyncRun
from app.models.enums import (
    AvailabilityEventType,
    ExceptionStatus,
    ImportJobStatus,
    OosStatus,
    SyncStatus,
)
from app.models.ingestion import ImportJob
from app.models.inventory import AvailabilityEvent
from app.models.matching import ProductMappingException
from app.models.sync import NineyardSyncRun
from app.models.vendor import Vendor
from app.models.watchlist import OosWatchlistEntry
from app.repositories.scoping import ScopedRepository

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
_read = Depends(require_roles(*READ_ROLES))


class LastImport(BaseModel):
    vendor_id: uuid.UUID
    vendor_code: str
    vendor_name: str
    job_id: uuid.UUID | None
    status: ImportJobStatus | None
    completed_at: datetime | None
    created_at: datetime | None
    total_rows: int | None
    exception_rows: int | None


class SyncStatusSummary(BaseModel):
    job_type: str | None
    status: SyncStatus | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class DashboardResponse(BaseModel):
    open_exceptions: int
    watchlist_active: int
    watchlist_in_stock: int
    watchlist_newly_available_7d: int
    imports_running: int
    last_imports: list[LastImport]
    amazon_syncs: list[SyncStatusSummary]
    nineyard_sync: SyncStatusSummary | None
    generated_at: datetime


class _Repository(ScopedRepository):
    pass


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Live counts for the dashboard",
    responses={403: {"description": "The caller lacks the required role."}},
)
def dashboard(
    principal: Principal = _read, session: Session = Depends(get_db)
) -> DashboardResponse:
    repository = _Repository(session, principal.organization_id)
    now = datetime.now(UTC)

    def count(statement: Select[Any]) -> int:
        return int(
            session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
        )

    open_exceptions = count(
        repository.select(ProductMappingException).where(
            ProductMappingException.status == ExceptionStatus.PENDING,
            (ProductMappingException.deferred_until.is_(None))
            | (ProductMappingException.deferred_until <= now),
        )
    )
    watchlist_active = count(
        repository.select(OosWatchlistEntry).where(OosWatchlistEntry.is_active.is_(True))
    )
    watchlist_in_stock = count(
        repository.select(OosWatchlistEntry).where(
            OosWatchlistEntry.is_active.is_(True),
            OosWatchlistEntry.current_status == OosStatus.IN_STOCK,
        )
    )
    newly_available = count(
        repository.select(AvailabilityEvent).where(
            AvailabilityEvent.event_type == AvailabilityEventType.BECAME_AVAILABLE,
            AvailabilityEvent.oos_watchlist_id.is_not(None),
            AvailabilityEvent.detected_at >= now - timedelta(days=7),
        )
    )
    imports_running = count(
        repository.select(ImportJob).where(
            ImportJob.status.in_((ImportJobStatus.PENDING, ImportJobStatus.RUNNING))
        )
    )

    # Last job per vendor: rank jobs by created_at within vendor.
    ranked = repository.select(
        ImportJob,
        ImportJob.vendor_id,
        ImportJob.id,
        ImportJob.status,
        ImportJob.completed_at,
        ImportJob.created_at,
        ImportJob.total_rows,
        ImportJob.exception_rows,
        func.row_number()
        .over(partition_by=ImportJob.vendor_id, order_by=ImportJob.created_at.desc())
        .label("rank"),
    ).subquery()
    latest = {
        row.vendor_id: row
        for row in session.execute(select(ranked).where(ranked.c.rank == 1)).all()
    }
    vendors = (
        session.execute(
            repository.select(Vendor).where(Vendor.is_active.is_(True)).order_by(Vendor.name)
        )
        .scalars()
        .all()
    )
    last_imports = [
        LastImport(
            vendor_id=vendor.id,
            vendor_code=vendor.code,
            vendor_name=vendor.name,
            job_id=latest[vendor.id].id if vendor.id in latest else None,
            status=latest[vendor.id].status if vendor.id in latest else None,
            completed_at=latest[vendor.id].completed_at if vendor.id in latest else None,
            created_at=latest[vendor.id].created_at if vendor.id in latest else None,
            total_rows=latest[vendor.id].total_rows if vendor.id in latest else None,
            exception_rows=latest[vendor.id].exception_rows if vendor.id in latest else None,
        )
        for vendor in vendors
    ]

    amazon_ranked = repository.select(
        AmazonSyncRun,
        AmazonSyncRun.job_type,
        AmazonSyncRun.status,
        AmazonSyncRun.started_at,
        AmazonSyncRun.completed_at,
        AmazonSyncRun.error_message,
        func.row_number()
        .over(partition_by=AmazonSyncRun.job_type, order_by=AmazonSyncRun.started_at.desc())
        .label("rank"),
    ).subquery()
    amazon_syncs = [
        SyncStatusSummary(
            job_type=str(row.job_type),
            status=row.status,
            started_at=row.started_at,
            completed_at=row.completed_at,
            error_message=row.error_message,
        )
        for row in session.execute(
            select(amazon_ranked)
            .where(amazon_ranked.c.rank == 1)
            .order_by(amazon_ranked.c.job_type)
        ).all()
    ]
    nineyard = session.execute(
        repository.select(NineyardSyncRun).order_by(NineyardSyncRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    nineyard_sync = (
        SyncStatusSummary(
            job_type="catalog",
            status=nineyard.status,
            started_at=nineyard.started_at,
            completed_at=nineyard.completed_at,
            error_message=nineyard.error_message,
        )
        if nineyard is not None
        else None
    )
    return DashboardResponse(
        open_exceptions=open_exceptions,
        watchlist_active=watchlist_active,
        watchlist_in_stock=watchlist_in_stock,
        watchlist_newly_available_7d=newly_available,
        imports_running=imports_running,
        last_imports=last_imports,
        amazon_syncs=amazon_syncs,
        nineyard_sync=nineyard_sync,
        generated_at=now,
    )
