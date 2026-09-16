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

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import identity_snapshot, release_since, transaction
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

SNAPSHOT_COLUMNS: Final = (
    "id",
    "organization_id",
    "vendor_id",
    "vendor_product_id",
    "product_id",
    "import_job_id",
    "quantity_available",
    "unit_cost",
    "currency",
    "availability_status",
    "effective_at",
    "captured_at",
)
EVENT_COLUMNS: Final = (
    "id",
    "organization_id",
    "vendor_id",
    "vendor_product_id",
    "product_id",
    "oos_watchlist_id",
    "event_type",
    "previous_snapshot_id",
    "current_snapshot_id",
    "previous_status",
    "new_status",
    "previous_quantity",
    "new_quantity",
    "over_max_unit_cost",
    "detected_at",
)


def _columns(instance: object, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(instance, name) for name in names}


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
            held = identity_snapshot(self.session)
            rows = self._next_rows(last)
            if not rows:
                break
            with transaction(self.session):
                self._snapshot_chunk(rows)
            last = rows[-1].row_number
            # Release what the chunk loaded or created so memory stays flat.
            release_since(self.session, held, keep=(self.job,))
            self.chunks += 1
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

    def _snapshot_chunk(self, rows: list[ImportJobRow]) -> None:
        """One chunk: two lookups for the whole chunk, then one bulk INSERT of
        snapshots and one of events — not two queries and a flush per row.

        Snapshots and events are built as transient instances (never added to
        the session) so the watchlist can read and annotate them, then written
        with Core executemany; only the few status-history rows go through the
        unit of work.
        """
        line_ids = [row.vendor_product_id for row in rows if row.vendor_product_id is not None]
        already = self.inventory.snapshotted_lines(self.job.id, line_ids)
        previous_by_line = self.inventory.latest_snapshots(line_ids)

        pending: list[
            tuple[ImportJobRow, VendorInventorySnapshot, VendorInventorySnapshot | None]
        ] = []
        for row in rows:
            assert row.vendor_product_id is not None
            if row.vendor_product_id in already:
                continue  # the stage ran before and was interrupted: never a second snapshot
            status = row.availability_status or AvailabilityStatus.UNKNOWN
            snapshot = VendorInventorySnapshot(
                id=uuid.uuid4(),
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
            pending.append((row, snapshot, previous_by_line.get(row.vendor_product_id)))
        if not pending:
            return
        self.session.execute(
            insert(VendorInventorySnapshot).execution_options(render_nulls=True),
            [_columns(snapshot, SNAPSHOT_COLUMNS) for _, snapshot, _ in pending],
        )
        self.snapshots += len(pending)

        raised: list[tuple[AvailabilityEvent, VendorInventorySnapshot]] = []
        for row, snapshot, previous in pending:
            kind = transition(
                previous.availability_status if previous else None, snapshot.availability_status
            )
            if kind is None:
                continue
            event = AvailabilityEvent(
                id=uuid.uuid4(),
                organization_id=self.organization_id,
                vendor_id=self.job.vendor_id,
                vendor_product_id=snapshot.vendor_product_id,
                product_id=row.product_id,
                event_type=kind,
                previous_snapshot_id=previous.id if previous else None,
                current_snapshot_id=snapshot.id,
                previous_status=previous.availability_status if previous else None,
                new_status=snapshot.availability_status,
                previous_quantity=previous.quantity_available if previous else None,
                new_quantity=row.quantity,
                detected_at=datetime.now(UTC),
            )
            self.events[kind.value] += 1
            raised.append((event, snapshot))
        if not raised:
            return
        for hit in watchlist.apply_events(self.session, self.organization_id, raised, self.actor):
            if hit.linked:
                self.watch_hits += 1
            if hit.over_ceiling:
                self.over_ceiling += 1
        self.session.execute(
            insert(AvailabilityEvent).execution_options(render_nulls=True),
            [_columns(event, EVENT_COLUMNS) for event, _ in raised],
        )

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
