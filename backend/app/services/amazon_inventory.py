"""Amazon inventory ingestion — FBA on-hand and inbound, plus FBM quantity.

One run writes one :class:`AmazonInventorySnapshot` per seller SKU with
``captured_at`` set to the run's start time. Snapshots are append-only:
a run never updates an earlier run's rows, so the history of "what did we
believe was inbound on that date" is a query rather than a reconstruction.

Two reads feed one snapshot per active SKU. The listings report is fetched
first so its active seller SKUs can bound the FBA request:

1. **The merchant listings report** (``GET_MERCHANT_LISTINGS_ALL_DATA``) —
   every listing with its ``fulfillment-channel`` and current status. Active
   seller SKUs are sent to FBA Inventory in batches of at most 50. Rows whose
   channel is ``DEFAULT`` are merchant-fulfilled; their ``quantity`` is the
   FBM quantity.
2. **FBA Inventory** (``iter_inventory_summaries(details=True)``) — the
   fulfillable, inbound (working / shipped / receiving), reserved,
   unfulfillable and researching quantities. This API does not know about
   merchant-fulfilled stock at all.

An active SKU that appears only in the report — FBM-only, never sent to FBA
— gets a snapshot with zero FBA quantities and the FBM quantity.

The listings rows are also what the listings→product mapping needs
(:mod:`app.services.amazon_listings`), so the same parsed rows feed it in the
same transaction — one report fetch serves both. The same
run/transaction/audit shape as :mod:`app.services.amazon_orders`.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.transaction import transaction
from app.integrations.amazon import AmazonClient, AmazonConfig, InventorySummary
from app.models.amazon import AmazonInventorySnapshot, AmazonSyncRun
from app.models.enums import AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_listings import map_listings
from app.services.amazon_orders import decode_report
from app.services.amazon_runs import SyncAlreadyRunning, close_run, fail_run, open_run

_logger = get_logger(__name__)

#: The report that lists every listing with its fulfillment channel.
LISTINGS_REPORT_TYPE: Final = "GET_MERCHANT_LISTINGS_ALL_DATA"

#: The only columns read from the listings report.
LISTINGS_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "seller-sku",
    "quantity",
    "fulfillment-channel",
    "asin1",
    "product-id",
    "product-id-type",
    "item-name",
    "status",
)

#: The listings report's value for merchant-fulfilled. Anything else
#: (``AMAZON_NA``, ``AMAZON_EU`` …) is FBA, and an unknown value is treated as
#: not-FBM rather than guessed at.
FBM_CHANNEL: Final = "DEFAULT"

#: ``product-id-type`` as documented for the merchant listings report.
PRODUCT_ID_TYPES: Final[Mapping[int, str]] = {1: "ASIN", 2: "ISBN", 3: "UPC", 4: "EAN"}

#: Snapshots are inserted in batches of this size.
BATCH_SIZE: Final = 500

AUDIT_ACTION_COMPLETED: Final = "amazon.inventory_sync.completed"
AUDIT_ACTION_FAILED: Final = "amazon.inventory_sync.failed"
AUDIT_ACTOR_LABEL: Final = "amazon-inventory-sync"


class InventorySyncError(Exception):
    """A run could not be performed. The run row, if one exists, is FAILED."""


class InventorySyncAlreadyRunning(InventorySyncError, SyncAlreadyRunning):  # noqa: N818
    """Another FBA_INVENTORY run is still RUNNING for this organization."""


class ListingsReportFormatError(InventorySyncError):
    """The listings report did not have the columns this ingestion depends on."""


class InventorySource(Protocol):
    """What the sync needs from a client — kept narrow for tests."""

    def iter_inventory_summaries(
        self,
        *,
        details: bool = True,
        page_delay_s: float = 0.0,
        seller_skus: Sequence[str] | None = None,
    ) -> Iterator[InventorySummary]: ...

    def fetch_report(
        self,
        report_type: str,
        data_start: datetime,
        data_end: datetime,
        report_options: Mapping[str, str] | None = None,
        *,
        poll_interval_s: float = 15.0,
        timeout_s: float = 1800.0,
    ) -> bytes: ...


# --- listings report --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedListing:
    """One row of the merchant listings report, as the ingestion reads it."""

    seller_sku: str
    asin: str | None
    #: ``None`` when the report left the column blank.
    quantity: int | None
    fulfillment_channel: str | None
    #: ``fulfillment-channel == "DEFAULT"``; anything else, including an
    #: unknown value, is not treated as merchant-fulfilled.
    is_fbm: bool
    product_id: str | None
    product_id_type: int | None
    #: ``ASIN`` / ``ISBN`` / ``UPC`` / ``EAN``, or ``None`` when the type is
    #: absent or not one Amazon documents.
    product_id_kind: str | None
    item_name: str | None
    status: str | None
    raw: dict[str, str]


@dataclass(slots=True)
class ListingsParseResult:
    listings: list[ParsedListing] = field(default_factory=list)
    rows_seen: int = 0
    rows_failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    encoding: str = "utf-8"


def parse_listings_report(content: bytes) -> ListingsParseResult:
    """Parse the merchant listings flat file.

    Same contract as the orders parser: a bad row is counted and described,
    the rest still loads; a missing required column raises.
    """
    text, encoding = decode_report(content)
    result = ListingsParseResult(encoding=encoding)

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    header = [h.strip() for h in reader.fieldnames or []]
    missing = [column for column in LISTINGS_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ListingsReportFormatError(
            "listings report is missing required columns: "
            + ", ".join(missing)
            + f"; columns present: {header}"
        )

    for row_number, row in enumerate(reader, start=1):
        result.rows_seen += 1
        try:
            result.listings.append(_parse_listing(row))
        except ValueError as exc:
            result.rows_failed += 1
            result.errors.append({"row": row_number, "reason": str(exc)})
    return result


def _parse_listing(row: Mapping[str, str | None]) -> ParsedListing:
    kept = {column: (row.get(column) or "").strip() for column in LISTINGS_REQUIRED_COLUMNS}

    sku = kept["seller-sku"]
    if not sku:
        raise ValueError("seller-sku is blank")

    quantity: int | None
    if kept["quantity"]:
        try:
            quantity = int(kept["quantity"])
        except ValueError:
            raise ValueError(f"quantity is not an integer: {kept['quantity']!r}") from None
        if quantity < 0:
            raise ValueError(f"quantity is negative: {quantity}")
    else:
        quantity = None

    id_type: int | None
    if kept["product-id-type"]:
        try:
            id_type = int(kept["product-id-type"])
        except ValueError:
            raise ValueError(
                f"product-id-type is not an integer: {kept['product-id-type']!r}"
            ) from None
    else:
        id_type = None

    channel = kept["fulfillment-channel"] or None
    return ParsedListing(
        seller_sku=sku,
        asin=kept["asin1"] or None,
        quantity=quantity,
        fulfillment_channel=channel,
        is_fbm=channel == FBM_CHANNEL,
        product_id=kept["product-id"] or None,
        product_id_type=id_type,
        product_id_kind=PRODUCT_ID_TYPES.get(id_type) if id_type is not None else None,
        item_name=kept["item-name"] or None,
        status=kept["status"] or None,
        raw=kept,
    )


# --- merging --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """One snapshot to write, already merged from both sources."""

    seller_sku: str
    asin: str
    fnsku: str | None
    condition: str | None
    fulfillable: int
    inbound_working: int
    inbound_shipped: int
    inbound_receiving: int
    reserved_total: int
    unfulfillable_total: int
    researching_total: int
    fbm_quantity: int | None
    amazon_last_updated_at: datetime | None
    raw: dict[str, Any]


@dataclass(slots=True)
class MergeResult:
    rows: list[SnapshotRow] = field(default_factory=list)
    rows_failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    #: How many SKUs came only from the listings report.
    fbm_only: int = 0


def fbm_quantities(listings: Sequence[ParsedListing]) -> dict[str, int | None]:
    """Seller SKU → FBM quantity, for merchant-fulfilled rows only.

    A blank quantity stays ``None`` — the listing is FBM but the report did
    not say how many. If the same SKU appears twice the later row wins,
    which mirrors how the report itself is read.
    """
    return {listing.seller_sku: listing.quantity for listing in listings if listing.is_fbm}


def merge_snapshot_rows(
    summaries: Sequence[InventorySummary], listings: Sequence[ParsedListing]
) -> MergeResult:
    """Combine FBA summaries and listings into one row per seller SKU.

    Every FBA summary produces a row; ``None`` quantities from the API become
    ``0`` here, explicitly, and ``raw`` keeps what the API actually said.
    A merchant-fulfilled listing whose SKU FBA did not return produces an
    FBM-only row — unless it has no ASIN, in which case the row cannot
    satisfy the table and is counted as failed rather than invented.
    """
    result = MergeResult()
    fbm = fbm_quantities(listings)
    seen: set[str] = set()

    for summary in summaries:
        if summary.seller_sku in seen:
            # The API should not repeat a SKU; if it does, the first wins and
            # the repeat is recorded rather than tripping the unique index.
            result.rows_failed += 1
            result.errors.append(
                {"seller_sku": summary.seller_sku, "reason": "duplicate FBA summary"}
            )
            continue
        if summary.asin is None:
            result.rows_failed += 1
            result.errors.append(
                {"seller_sku": summary.seller_sku, "reason": "FBA summary has no ASIN"}
            )
            continue
        seen.add(summary.seller_sku)
        result.rows.append(
            SnapshotRow(
                seller_sku=summary.seller_sku,
                asin=summary.asin,
                fnsku=summary.fnsku,
                condition=summary.condition,
                fulfillable=summary.fulfillable or 0,
                inbound_working=summary.inbound_working or 0,
                inbound_shipped=summary.inbound_shipped or 0,
                inbound_receiving=summary.inbound_receiving or 0,
                reserved_total=summary.reserved_total or 0,
                unfulfillable_total=summary.unfulfillable_total or 0,
                researching_total=summary.researching_total or 0,
                fbm_quantity=fbm.get(summary.seller_sku),
                amazon_last_updated_at=summary.last_updated,
                raw=_summary_raw(summary),
            )
        )

    for listing in listings:
        if not listing.is_fbm or listing.seller_sku in seen:
            continue
        if listing.asin is None:
            result.rows_failed += 1
            result.errors.append(
                {"seller_sku": listing.seller_sku, "reason": "FBM-only listing has no ASIN"}
            )
            continue
        seen.add(listing.seller_sku)
        result.fbm_only += 1
        result.rows.append(
            SnapshotRow(
                seller_sku=listing.seller_sku,
                asin=listing.asin,
                fnsku=None,
                condition=None,
                fulfillable=0,
                inbound_working=0,
                inbound_shipped=0,
                inbound_receiving=0,
                reserved_total=0,
                unfulfillable_total=0,
                researching_total=0,
                fbm_quantity=listing.quantity,
                amazon_last_updated_at=None,
                raw={"source": "listings_report", "listing": listing.raw},
            )
        )
    return result


def _summary_raw(summary: InventorySummary) -> dict[str, Any]:
    """The API's own values, ``None`` preserved, so a coerced 0 is traceable."""
    return {
        "source": "fba_inventory",
        "seller_sku": summary.seller_sku,
        "asin": summary.asin,
        "fnsku": summary.fnsku,
        "condition": summary.condition,
        "last_updated": summary.last_updated.isoformat() if summary.last_updated else None,
        "total": summary.total,
        "fulfillable": summary.fulfillable,
        "inbound_working": summary.inbound_working,
        "inbound_shipped": summary.inbound_shipped,
        "inbound_receiving": summary.inbound_receiving,
        "reserved_total": summary.reserved_total,
        "unfulfillable_total": summary.unfulfillable_total,
        "researching_total": summary.researching_total,
    }


# --- writing --------------------------------------------------------------------


def insert_snapshots(
    session: Session,
    *,
    organization_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    captured_at: datetime,
    rows: Sequence[SnapshotRow],
) -> int:
    """Append one snapshot per row. No upsert: earlier runs are never touched."""
    written = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        session.execute(
            insert(AmazonInventorySnapshot).values(
                [
                    {
                        "organization_id": organization_id,
                        "sync_run_id": sync_run_id,
                        "captured_at": captured_at,
                        "seller_sku": row.seller_sku,
                        "asin": row.asin,
                        "fnsku": row.fnsku,
                        "condition": row.condition,
                        "fulfillable": row.fulfillable,
                        "inbound_working": row.inbound_working,
                        "inbound_shipped": row.inbound_shipped,
                        "inbound_receiving": row.inbound_receiving,
                        "reserved_total": row.reserved_total,
                        "unfulfillable_total": row.unfulfillable_total,
                        "researching_total": row.researching_total,
                        "fbm_quantity": row.fbm_quantity,
                        "amazon_last_updated_at": row.amazon_last_updated_at,
                        "raw": row.raw,
                    }
                    for row in batch
                ]
            )
        )
        written += len(batch)
    return written


# --- the run ----------------------------------------------------------------------


def run_inventory_sync(
    session: Session,
    organization_id: uuid.UUID,
    trigger_type: TriggerType,
    triggered_by_user_id: uuid.UUID | None = None,
    client: InventorySource | None = None,
    *,
    marketplace_id: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AmazonSyncRun:
    """Perform one inventory ingestion run and return its closed run record.

    The parsed listings rows feed both the FBM quantities on the snapshots
    and the listings→product mapping, in the same transaction.

    Raises :class:`InventorySyncAlreadyRunning` if a run is in flight, and
    otherwise re-raises whatever stopped the run — after the run row has been
    closed as ``FAILED``. The row is never left ``RUNNING``.
    """
    started_at = now()
    settings = get_settings()
    if client is None:
        client = AmazonClient(AmazonConfig.from_settings(settings))
    marketplace_id = marketplace_id or settings.amazon_marketplace_id

    try:
        run = open_run(
            session,
            organization_id=organization_id,
            job_type=AmazonSyncJobType.FBA_INVENTORY,
            trigger_type=trigger_type,
            marketplace_id=marketplace_id,
            started_at=started_at,
            triggered_by_user_id=triggered_by_user_id,
        )
    except SyncAlreadyRunning as exc:
        raise InventorySyncAlreadyRunning(str(exc)) from exc

    failure: BaseException | None = None
    try:
        # The listings report has no data window; the times are required by
        # the API but ignored for this report type.
        content = client.fetch_report(LISTINGS_REPORT_TYPE, started_at, started_at)
        listings = parse_listings_report(content)
        active_listings = [
            listing
            for listing in listings.listings
            if (listing.status or "").strip().lower() == "active"
        ]
        active_skus = tuple(dict.fromkeys(listing.seller_sku for listing in active_listings))
        _logger.info(
            "amazon.inventory_sync.listings",
            run_id=str(run.id),
            rows_seen=listings.rows_seen,
            rows_failed=listings.rows_failed,
            active=len(active_listings),
            fbm=sum(1 for item in listings.listings if item.is_fbm),
            encoding=listings.encoding,
        )

        summaries = list(
            client.iter_inventory_summaries(
                details=True,
                page_delay_s=settings.amazon_inventory_page_delay_s,
                seller_skus=active_skus,
            )
        )
        _logger.info(
            "amazon.inventory_sync.summaries",
            run_id=str(run.id),
            requested_skus=len(active_skus),
            count=len(summaries),
        )

        # Inventory is operational state. Historical inactive listings stay
        # in marketplace_listings for traceability, but do not create current
        # inventory snapshots or inflate the exception queue.
        merged = merge_snapshot_rows(summaries, active_listings)
        rows_failed = listings.rows_failed + merged.rows_failed
        errors = listings.errors + merged.errors

        with transaction(session):
            written = insert_snapshots(
                session,
                organization_id=organization_id,
                sync_run_id=run.id,
                captured_at=started_at,
                rows=merged.rows,
            )
            # The same parsed rows, one transaction: listings are upserted and
            # resolved through the priority chain, or not at all.
            mapping = map_listings(
                session,
                organization_id,
                listings.listings,
                marketplace_id=marketplace_id,
                now=lambda: started_at,
            )
            close_run(
                session,
                run,
                status=SyncStatus.COMPLETED_WITH_ERRORS if rows_failed else SyncStatus.COMPLETED,
                audit_action=AUDIT_ACTION_COMPLETED,
                actor_label=AUDIT_ACTOR_LABEL,
                completed_at=now(),
                rows_seen=len(summaries) + listings.rows_seen,
                rows_created=written,
                rows_updated=0,
                rows_unchanged=0,
                rows_failed=rows_failed,
                error_details={
                    **({"rows": errors[:100]} if errors else {}),
                    "fbm_only": merged.fbm_only,
                    "listings_mapping": mapping.as_json(),
                },
            )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if run.status is SyncStatus.RUNNING:
            fail_run(
                session,
                run,
                failure,
                completed_at=now(),
                audit_action=AUDIT_ACTION_FAILED,
                actor_label=AUDIT_ACTOR_LABEL,
            )

    _logger.info(
        "amazon.inventory_sync.completed",
        run_id=str(run.id),
        status=run.status.value,
        rows_seen=run.rows_seen,
        rows_created=run.rows_created,
        rows_failed=run.rows_failed,
        fbm_only=merged.fbm_only,
    )
    return run
