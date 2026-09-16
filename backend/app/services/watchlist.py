"""The OOS watchlist (phase 9, AC-10) and how availability events reach it (AC-11.6).

A watch entry is buying intent: "tell me when product X is available (from
vendor Y, or from anyone), I want this many, at no more than this cost".
Entries are deactivated, never deleted (AC-10.2); add and remove are
audited (AC-10.4).

``apply_event`` is called by the snapshot stage for every availability
event. When the event concerns a watched product it is linked to the most
specific active entry (vendor-specific before all-vendors); every matching
entry's ``current_status`` follows — ``IN_STOCK`` on BECAME_AVAILABLE,
``OUT_OF_STOCK`` on BECAME_UNAVAILABLE when no other vendor line for the
product is still available — with one ``oos_status_history`` row per actual
change. ``max_unit_cost`` is honoured by flagging, never by dropping: an
event whose cost is above the ceiling is linked with
``over_max_unit_cost = true`` so the buyer sees it and decides.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.models.enums import AvailabilityEventType, AvailabilityStatus, OosStatus
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.models.watchlist import OosStatusHistory, OosWatchlistEntry
from app.repositories.exceptions import ExceptionRepository
from app.repositories.inventory import InventoryRepository, WatchlistPage
from app.repositories.vendors import VendorRepository
from app.schemas.watchlist import WatchlistCreate
from app.services import audit

_logger = get_logger(__name__)


class WatchNotFound(NotFoundError):  # noqa: N818
    code = "watchlist_entry_not_found"
    message = "No such watchlist entry in this organization."


class WatchAlreadyActive(ConflictError):  # noqa: N818
    code = "watchlist_entry_exists"
    message = "This product (and vendor) is already on the watchlist."


class WatchAlreadyInactive(ConflictError):  # noqa: N818
    code = "watchlist_entry_inactive"
    message = "This watchlist entry was already removed."


class ProductNotFound(NotFoundError):  # noqa: N818
    code = "product_not_found"
    message = "No such active product in this organization."


class VendorNotFound(NotFoundError):  # noqa: N818
    code = "vendor_not_found"
    message = "No such vendor in this organization."


@dataclass(frozen=True, slots=True)
class WatchHit:
    linked: bool
    over_ceiling: bool


# --- entries ---------------------------------------------------------------------------------


def list_watchlist(
    session: Session,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    vendor_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    include_inactive: bool,
) -> WatchlistPage:
    return InventoryRepository(session, principal.organization_id).list_watchlist(
        page=page,
        page_size=page_size,
        vendor_id=vendor_id,
        product_id=product_id,
        include_inactive=include_inactive,
    )


def get_watch(session: Session, principal: Principal, entry_id: uuid.UUID) -> OosWatchlistEntry:
    entry = InventoryRepository(session, principal.organization_id).get_watch(entry_id)
    if entry is None:
        raise WatchNotFound()
    return entry


def add_watch(
    session: Session, principal: Principal, payload: WatchlistCreate
) -> OosWatchlistEntry:
    organization_id = principal.organization_id
    inventory = InventoryRepository(session, organization_id)
    if ExceptionRepository(session, organization_id).active_product(payload.product_id) is None:
        raise ProductNotFound()
    if payload.vendor_id is not None and (
        VendorRepository(session, organization_id).get(payload.vendor_id) is None
    ):
        raise VendorNotFound()
    if inventory.active_watch(payload.product_id, payload.vendor_id) is not None:
        raise WatchAlreadyActive()

    now = datetime.now(UTC)
    entry = OosWatchlistEntry(
        organization_id=organization_id,
        product_id=payload.product_id,
        vendor_id=payload.vendor_id,
        reason=payload.reason,
        priority=payload.priority,
        desired_quantity=payload.desired_quantity,
        max_unit_cost=payload.max_unit_cost,
        current_status=_status_now(inventory, payload.product_id, payload.vendor_id),
        current_status_changed_at=now,
        is_active=True,
        added_by_user_id=principal.user_id,
        added_at=now,
        notes=payload.notes,
    )
    with transaction(session):
        session.add(entry)
        session.flush()
        audit.record_change(
            session,
            organization_id=organization_id,
            action="watchlist.added",
            instance=entry,
            before=None,
            actor=principal,
            summary=(
                f"Product {entry.product_id} watched"
                + (f" at vendor {entry.vendor_id}" if entry.vendor_id else " at any vendor")
                + f" ({entry.priority.value.lower()})."
            ),
        )
    return entry


def remove_watch(
    session: Session, principal: Principal, entry_id: uuid.UUID, *, note: str | None
) -> OosWatchlistEntry:
    entry = get_watch(session, principal, entry_id)
    if not entry.is_active:
        raise WatchAlreadyInactive()
    before = audit.snapshot(entry)
    with transaction(session):
        entry.is_active = False
        entry.deactivated_by_user_id = principal.user_id
        entry.deactivated_at = datetime.now(UTC)
        if note:
            entry.notes = note
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="watchlist.removed",
            instance=entry,
            before=before,
            actor=principal,
            summary=f"Watch on product {entry.product_id} removed.",
        )
    return entry


def history(session: Session, principal: Principal, entry_id: uuid.UUID) -> list[OosStatusHistory]:
    get_watch(session, principal, entry_id)
    return list(InventoryRepository(session, principal.organization_id).history(entry_id))


# --- events reaching the watchlist ---------------------------------------------------------------


def apply_events(
    session: Session,
    organization_id: uuid.UUID,
    raised: Sequence[tuple[AvailabilityEvent, VendorInventorySnapshot]],
    actor: Principal | None,
) -> list[WatchHit]:
    """``apply_event`` for a chunk of events with one watch lookup for all of
    them, so 25,000 events cost one query, not 25,000."""
    product_ids = {event.product_id for event, _ in raised if event.product_id is not None}
    if not product_ids:
        return [WatchHit(linked=False, over_ceiling=False) for _ in raised]
    inventory = InventoryRepository(session, organization_id)
    watches = inventory.active_watches_for_products(product_ids)
    return [
        apply_event(session, organization_id, event, snapshot, actor, watches=watches)
        for event, snapshot in raised
    ]


def apply_event(
    session: Session,
    organization_id: uuid.UUID,
    event: AvailabilityEvent,
    snapshot: VendorInventorySnapshot,
    actor: Principal | None,
    *,
    watches: Sequence[OosWatchlistEntry] | None = None,
) -> WatchHit:
    """Link an availability event to the watch entries it concerns and move
    their status. Runs inside the snapshot stage's transaction. ``watches``
    is the pre-fetched active entries for the chunk's products, if any."""
    if event.product_id is None:
        return WatchHit(linked=False, over_ceiling=False)
    inventory = InventoryRepository(session, organization_id)
    if watches is None:
        entries: Sequence[OosWatchlistEntry] = inventory.active_watches_for(
            event.product_id, event.vendor_id
        )
    else:
        concerned = [
            w
            for w in watches
            if w.product_id == event.product_id
            and (w.vendor_id is None or w.vendor_id == event.vendor_id)
        ]
        # Vendor-specific first, then all-vendors, oldest first — as the query does.
        entries = sorted(concerned, key=lambda w: (w.vendor_id is None, w.created_at))
    if not entries:
        return WatchHit(linked=False, over_ceiling=False)

    primary = entries[0]
    event.oos_watchlist_id = primary.id
    over = _over_ceiling(primary.max_unit_cost, snapshot.unit_cost)
    event.over_max_unit_cost = over
    for entry in entries:
        new_status = _status_after(inventory, entry, event)
        _move_status(session, organization_id, entry, new_status, snapshot)
    session.flush()
    _logger.info(
        "watchlist.hit",
        event_id=str(event.id),
        entry_id=str(primary.id),
        event_type=event.event_type.value,
        over_max_unit_cost=over,
    )
    return WatchHit(linked=True, over_ceiling=bool(over))


def _over_ceiling(ceiling: Decimal | None, cost: Decimal | None) -> bool | None:
    if ceiling is None:
        return None
    if cost is None:
        return False
    return cost > ceiling


def _status_after(
    inventory: InventoryRepository, entry: OosWatchlistEntry, event: AvailabilityEvent
) -> OosStatus:
    if event.event_type is AvailabilityEventType.BECAME_AVAILABLE:
        return OosStatus.IN_STOCK
    # BECAME_UNAVAILABLE: out of stock only if no vendor line this entry
    # watches is still available.
    assert event.product_id is not None
    statuses = inventory.latest_status_by_line(event.product_id, vendor_id=entry.vendor_id)
    if any(s is AvailabilityStatus.AVAILABLE for s in statuses.values()):
        return OosStatus.IN_STOCK
    return OosStatus.OUT_OF_STOCK


def _status_now(
    inventory: InventoryRepository, product_id: uuid.UUID, vendor_id: uuid.UUID | None
) -> OosStatus:
    """A new entry starts from what the latest snapshots already say."""
    statuses = inventory.latest_status_by_line(product_id, vendor_id=vendor_id)
    if not statuses:
        return OosStatus.UNKNOWN
    if any(s is AvailabilityStatus.AVAILABLE for s in statuses.values()):
        return OosStatus.IN_STOCK
    if all(s is AvailabilityStatus.UNKNOWN for s in statuses.values()):
        return OosStatus.UNKNOWN
    return OosStatus.OUT_OF_STOCK


def _move_status(
    session: Session,
    organization_id: uuid.UUID,
    entry: OosWatchlistEntry,
    new_status: OosStatus,
    snapshot: VendorInventorySnapshot,
) -> None:
    if entry.current_status is new_status:
        return  # history records actual changes only (check constraint)
    previous = entry.current_status
    entry.current_status = new_status
    entry.current_status_changed_at = datetime.now(UTC)
    session.add(
        OosStatusHistory(
            organization_id=organization_id,
            oos_watchlist_id=entry.id,
            product_id=entry.product_id,
            vendor_id=snapshot.vendor_id,
            previous_status=previous,
            new_status=new_status,
            vendor_inventory_snapshot_id=snapshot.id,
            changed_at=datetime.now(UTC),
        )
    )
