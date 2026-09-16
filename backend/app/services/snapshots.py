"""The snapshotting stage of an import (phase 9, AC-11).

``snapshot_import_job`` takes a RUNNING job at stage SNAPSHOTTING and:

1. writes one ``vendor_inventory_snapshots`` row per vendor line the job
   touched — every row with a ``vendor_product_id``, matched or not
   (AC-11.7: a transition on an unmapped line is still signal), with
   ``product_id`` set where the row matched. Snapshots are append-only;
   the unique ``(import_job_id, vendor_product_id)`` index means re-running
   the stage cannot double-write history;
2. diffs each new snapshot against the vendor line's previous one and
   raises exactly one ``availability_events`` row per transition —
   ``BECAME_AVAILABLE`` or ``BECAME_UNAVAILABLE`` — referencing both
   snapshots. A line seen for the first time with stock raises
   ``BECAME_AVAILABLE``. No transition, no event; the unique
   ``(current_snapshot_id, event_type)`` index is the backstop (AC-11.4);
3. hands each event to the watchlist (:mod:`app.services.watchlist`) so a
   watched product's status and history follow;
4. closes the job: ``COMPLETED``, or ``COMPLETED_WITH_ERRORS`` when any row
   was rejected. Audited.

**What counts as a transition.** ``AVAILABLE`` is available; ``OUT_OF_STOCK``
and ``DISCONTINUED`` are unavailable; ``UNKNOWN`` is not a state anyone should
act on, so a move *to* UNKNOWN raises nothing and a move *from* UNKNOWN to
AVAILABLE is a ``BECAME_AVAILABLE`` — the buyer wants to hear it.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.models.enums import (
    ActorType,
    AvailabilityEventType,
    AvailabilityStatus,
    ImportJobStage,
    ImportJobStatus,
    ImportRowStatus,
)
from app.models.ingestion import ImportJob, ImportJobRow
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.repositories.imports import ImportRepository
from app.repositories.inventory import InventoryRepository
from app.services import audit, watchlist

_logger = get_logger(__name__)

CHUNK_ROWS: Final = 1000
UNAVAILABLE: Final = frozenset({AvailabilityStatus.OUT_OF_STOCK, AvailabilityStatus.DISCONTINUED})


class ImportJobNotAtSnapshotting(ConflictError):  # noqa: N818
    code = "import_job_not_at_snapshotting"
    message = "Only a RUNNING import at stage SNAPSHOTTING can be snapshotted."


def transition(
    previous: AvailabilityStatus | None, current: AvailabilityStatus
) -> AvailabilityEventType | None:
    """The one place the availability state machine lives."""
    if current is AvailabilityStatus.AVAILABLE:
        if previous is None or previous is not AvailabilityStatus.AVAILABLE:
            return AvailabilityEventType.BECAME_AVAILABLE
        return None
    if current in UNAVAILABLE and previous is AvailabilityStatus.AVAILABLE:
        return AvailabilityEventType.BECAME_UNAVAILABLE
    return None


def snapshot_import_job(
    session: Session,
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    actor: Principal | None = None,
) -> ImportJob:
    repository = ImportRepository(session, organization_id)
    job = repository.get_job(job_id)
    if job is None:
        from app.services.imports import ImportJobNotFound

        raise ImportJobNotFound()
    if (
        job.status is not ImportJobStatus.RUNNING
        or job.current_stage is not ImportJobStage.SNAPSHOTTING
    ):
        raise ImportJobNotAtSnapshotting(
            details={
                "status": job.status.value,
                "current_stage": job.current_stage.value if job.current_stage else None,
            }
        )
    _Snapshotter(session, organization_id, job, actor).run()
    return job


class _Snapshotter:
    def __init__(
        self, session: Session, organization_id: uuid.UUID, job: ImportJob, actor: Principal | None
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.job = job
        self.actor = actor
        self.inventory = InventoryRepository(session, organization_id)
        self.effective_at = job.import_file.uploaded_at or job.started_at or datetime.now(UTC)
        self.snapshots = 0
        self.events: Counter[str] = Counter()
        self.watch_hits = 0
        self.over_ceiling = 0
        self.chunks = 0

    def run(self) -> None:
        last = -1
        while True:
            rows = self._next_rows(last)
            if not rows:
                break
            with transaction(self.session):
                for row in rows:
                    self._snapshot_row(row)
            self.chunks += 1
            last = rows[-1].row_number
        self._finish()

    def _next_rows(self, after: int) -> list[ImportJobRow]:
        return list(
            self.session.execute(
                self.inventory.select(ImportJobRow)
                .where(
                    ImportJobRow.import_job_id == self.job.id,
                    ImportJobRow.status.in_((ImportRowStatus.OK, ImportRowStatus.WARNING)),
                    ImportJobRow.vendor_product_id.is_not(None),
                    ImportJobRow.row_number > after,
                )
                .order_by(ImportJobRow.row_number)
                .limit(CHUNK_ROWS)
            )
            .scalars()
            .all()
        )

    def _snapshot_row(self, row: ImportJobRow) -> None:
        assert row.vendor_product_id is not None
        existing = self.inventory.snapshot_for(self.job.id, row.vendor_product_id)
        if existing is not None:
            return  # the stage ran before and was interrupted: never a second snapshot
        previous = self.inventory.latest_snapshot(row.vendor_product_id)
        status = row.availability_status or AvailabilityStatus.UNKNOWN
        snapshot = VendorInventorySnapshot(
            organization_id=self.organization_id,
            vendor_id=self.job.vendor_id,
            vendor_product_id=row.vendor_product_id,
            product_id=row.product_id,
            import_job_id=self.job.id,
            quantity_available=row.quantity,
            unit_cost=row.unit_cost,
            currency=row.currency if row.unit_cost is not None else None,
            availability_status=status,
            effective_at=self.effective_at,
            captured_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        self.session.flush()
        self.snapshots += 1

        kind = transition(previous.availability_status if previous else None, status)
        if kind is None:
            return
        event = AvailabilityEvent(
            organization_id=self.organization_id,
            vendor_id=self.job.vendor_id,
            vendor_product_id=row.vendor_product_id,
            product_id=row.product_id,
            event_type=kind,
            previous_snapshot_id=previous.id if previous else None,
            current_snapshot_id=snapshot.id,
            previous_status=previous.availability_status if previous else None,
            new_status=status,
            previous_quantity=previous.quantity_available if previous else None,
            new_quantity=row.quantity,
            detected_at=datetime.now(UTC),
        )
        self.session.add(event)
        self.session.flush()
        self.events[kind.value] += 1
        hit = watchlist.apply_event(self.session, self.organization_id, event, snapshot, self.actor)
        if hit.linked:
            self.watch_hits += 1
        if hit.over_ceiling:
            self.over_ceiling += 1

    def _finish(self) -> None:
        job = self.job
        before = audit.snapshot(job)
        summary: dict[str, Any] = {
            "snapshots": self.snapshots,
            "events": dict(self.events),
            "watchlist_hits": self.watch_hits,
            "over_max_unit_cost": self.over_ceiling,
            "effective_at": self.effective_at.isoformat(),
            "chunks": self.chunks,
        }
        with transaction(self.session):
            job.status = (
                ImportJobStatus.COMPLETED_WITH_ERRORS
                if job.error_rows > 0
                else ImportJobStatus.COMPLETED
            )
            job.current_stage = None
            job.completed_at = datetime.now(UTC)
            job.error_details = {**job.error_details, "snapshotting": summary}
            audit.record_change(
                self.session,
                organization_id=self.organization_id,
                action="import_job.completed",
                instance=job,
                before=before,
                actor=self.actor,
                actor_type=None if self.actor else ActorType.WORKER,
                actor_label=None if self.actor else "import-runner",
                summary=(
                    f"Import {job.status.value.lower().replace('_', ' ')}: {self.snapshots} "
                    f"snapshots, {sum(self.events.values())} availability events, "
                    f"{self.watch_hits} watchlist hits."
                ),
            )
        _logger.info("import.completed", job_id=str(job.id), **summary["events"])


def latest_status_by_product(
    session: Session, organization_id: uuid.UUID, product_id: uuid.UUID
) -> dict[uuid.UUID, AvailabilityStatus]:
    """Each vendor line's latest availability for a product — what "in stock
    somewhere" is decided on."""
    inventory = InventoryRepository(session, organization_id)
    return inventory.latest_status_by_line(product_id)
