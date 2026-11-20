"""Amazon orders ingestion — the sales data behind 7/14/30-day velocity.

One run: open a ``RUNNING`` row in ``amazon_sync_runs``, fetch the all-orders
flat-file report for a window, parse it, upsert one line per (order, seller
SKU), and close the run with counts — ``COMPLETED``, ``COMPLETED_WITH_ERRORS``
if any row was rejected, or ``FAILED``. A crash of any kind still closes the
run as ``FAILED`` with the exception recorded, so a dangling ``RUNNING`` row
cannot block the next scheduled run.

**What is kept from the report, and what is not.** Exactly twelve columns are
read; they are listed in :data:`REQUIRED_COLUMNS`. The report also carries
``ship-city``, ``ship-state``, ``ship-postal-code`` and ``ship-country``.
Those are never read, never stored, and never appear in ``raw`` — the
retained subset is built from :data:`REQUIRED_COLUMNS`, not from the row
(ADR 0011: no buyer PII).

**Why (order, SKU) is the grain.** The flat-file report has no order-item id,
and one order can list the same SKU on more than one line. Lines are
aggregated per pair before the upsert: quantities are summed, and the
statuses, price and timestamps come from the line with the latest
``last-updated-date``.

The report is requested through :class:`AmazonClient` (read-only, ADR 0011)
and the run's outcome is audited in the same transaction as its final update
(ADR 0006).
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal, Protocol

from sqlalchemy import and_, case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.transaction import transaction
from app.integrations.amazon import AmazonClient, AmazonConfig
from app.models.amazon import AmazonOrderLine, AmazonSyncRun
from app.models.enums import AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_runs import SyncAlreadyRunning, close_run, fail_run, open_run

_logger = get_logger(__name__)

#: The report that lists every order line changed in a window.
REPORT_TYPE: Final = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL"
ORDER_DATE_REPORT_TYPE: Final = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"
ORDER_EVIDENCE_VERSION: Final = "amazon-order-line-v1"

#: Amazon caps this report's window. A longer request is rejected here rather
#: than by Amazon, with a message that says why.
MAX_WINDOW_DAYS: Final = 30

#: The only columns read from the report. ``raw`` is built from this list, so
#: a ship-* column cannot reach the database by accident.
REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "amazon-order-id",
    "purchase-date",
    "last-updated-date",
    "order-status",
    "fulfillment-channel",
    "sales-channel",
    "sku",
    "asin",
    "item-status",
    "quantity",
    "currency",
    "item-price",
)

#: Rows are upserted in batches of this size.
BATCH_SIZE: Final = 500

AUDIT_ACTION_COMPLETED: Final = "amazon.orders_sync.completed"
AUDIT_ACTION_FAILED: Final = "amazon.orders_sync.failed"
AUDIT_ACTOR_LABEL: Final = "amazon-orders-sync"


class OrdersSyncError(Exception):
    """A run could not be performed. The run row, if one exists, is FAILED."""


class OrdersSyncAlreadyRunning(OrdersSyncError, SyncAlreadyRunning):  # noqa: N818
    """Another ORDERS_REPORT run is still RUNNING for this organization."""


class ReportFormatError(OrdersSyncError):
    """The report did not have the columns this ingestion depends on."""


class ReportFetcher(Protocol):
    """The one thing the sync needs from a client — kept narrow for tests."""

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


# --- windows -------------------------------------------------------------------


def validate_window(window_start: datetime, window_end: datetime, *, now: datetime) -> None:
    """Refuse a window Amazon would reject, or one that asks about the future."""
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError(f"{name} must be a timezone-aware UTC datetime")
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")
    if window_end > now:
        raise ValueError("window_end must not be in the future")
    if window_end - window_start > timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError(
            f"the orders report accepts at most {MAX_WINDOW_DAYS} days; "
            f"this window is {(window_end - window_start).days} days"
        )


def trailing_window(
    *, now: datetime | None = None, days: int = MAX_WINDOW_DAYS
) -> tuple[datetime, datetime]:
    """``[now - days, now]`` for the scheduler.

    Capped at :data:`MAX_WINDOW_DAYS`, the report's own limit; a scheduler
    that wants deeper history runs more than one window.
    """
    if days <= 0 or days > MAX_WINDOW_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_WINDOW_DAYS}")
    end = now or datetime.now(UTC)
    return end - timedelta(days=days), end


# --- parsing -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedOrderLine:
    """One (order, SKU) line after aggregation, ready to upsert."""

    amazon_order_id: str
    seller_sku: str
    asin: str | None
    quantity_ordered: int
    purchase_date: datetime
    last_updated_at: datetime
    order_status: str
    item_status: str | None
    fulfillment_channel: str | None
    sales_channel: str | None
    currency: str | None
    item_price: Decimal | None
    #: The retained columns of the latest line, verbatim. Never a ship-* field.
    raw: dict[str, str]
    aggregated: bool = False


@dataclass(slots=True)
class ParseResult:
    lines: list[ParsedOrderLine] = field(default_factory=list)
    rows_seen: int = 0
    rows_failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    encoding: str = "utf-8"


def decode_report(content: bytes) -> tuple[str, str]:
    """UTF-8 (BOM tolerated), falling back to Latin-1 which never fails."""
    try:
        return content.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return content.decode("latin-1"), "latin-1"


def parse_orders_report(content: bytes) -> ParseResult:
    """Parse the flat-file report into aggregated (order, SKU) lines.

    A row that cannot be interpreted is counted in ``rows_failed`` and
    described in ``errors`` with its 1-based data row number; the rest of the
    report still loads. A missing required column is different: nothing can
    be trusted, so it raises :class:`ReportFormatError`.
    """
    text, encoding = decode_report(content)
    result = ParseResult(encoding=encoding)

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    header = [h.strip() for h in reader.fieldnames or []]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ReportFormatError(
            "orders report is missing required columns: "
            + ", ".join(missing)
            + f"; columns present: {header}"
        )

    aggregated: dict[tuple[str, str], _Aggregate] = {}
    for row_number, row in enumerate(reader, start=1):
        result.rows_seen += 1
        try:
            parsed = _parse_row(row)
        except ValueError as exc:
            result.rows_failed += 1
            result.errors.append({"row": row_number, "reason": str(exc)})
            continue

        key = (parsed.amazon_order_id, parsed.seller_sku)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = _Aggregate(latest=parsed, quantity=parsed.quantity_ordered)
        else:
            existing.count += 1
            existing.quantity += parsed.quantity_ordered
            if parsed.last_updated_at >= existing.latest.last_updated_at:
                existing.latest = parsed

    for aggregate in aggregated.values():
        latest = aggregate.latest
        result.lines.append(
            ParsedOrderLine(
                amazon_order_id=latest.amazon_order_id,
                seller_sku=latest.seller_sku,
                asin=latest.asin,
                quantity_ordered=aggregate.quantity,
                purchase_date=latest.purchase_date,
                last_updated_at=latest.last_updated_at,
                order_status=latest.order_status,
                item_status=latest.item_status,
                fulfillment_channel=latest.fulfillment_channel,
                sales_channel=latest.sales_channel,
                currency=latest.currency,
                item_price=latest.item_price,
                raw=latest.raw,
                aggregated=aggregate.count > 1,
            )
        )
    return result


@dataclass(slots=True)
class _Aggregate:
    latest: ParsedOrderLine
    quantity: int
    count: int = 1


def _parse_row(row: Mapping[str, str | None]) -> ParsedOrderLine:
    # Only the columns we keep are ever read from the row. Building `raw` from
    # REQUIRED_COLUMNS rather than from `row` is what keeps ship-* out.
    kept = {column: (row.get(column) or "").strip() for column in REQUIRED_COLUMNS}

    order_id = kept["amazon-order-id"]
    sku = kept["sku"]
    if not order_id:
        raise ValueError("amazon-order-id is blank")
    if not sku:
        raise ValueError("sku is blank")

    quantity = _parse_int(kept["quantity"], "quantity")
    if quantity < 0:
        raise ValueError(f"quantity is negative: {quantity}")

    currency = kept["currency"].upper() or None
    price = _parse_decimal(kept["item-price"], "item-price")
    if price is not None and currency is None:
        # A price with no currency cannot satisfy the table's check; the price
        # is dropped, the raw column keeps what Amazon sent.
        price = None

    return ParsedOrderLine(
        amazon_order_id=order_id,
        seller_sku=sku,
        asin=kept["asin"] or None,
        quantity_ordered=quantity,
        purchase_date=_parse_timestamp(kept["purchase-date"], "purchase-date"),
        last_updated_at=_parse_timestamp(kept["last-updated-date"], "last-updated-date"),
        order_status=kept["order-status"] or "Unknown",
        item_status=kept["item-status"] or None,
        fulfillment_channel=kept["fulfillment-channel"] or None,
        sales_channel=kept["sales-channel"] or None,
        currency=currency,
        item_price=price,
        raw=kept,
    )


def _parse_int(value: str, column: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{column} is not an integer: {value!r}") from None


def _parse_decimal(value: str, column: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{column} is not a number: {value!r}") from None


def _parse_timestamp(value: str, column: str) -> datetime:
    """Amazon writes ISO 8601 with an offset (``2026-09-10T14:03:22+00:00``).

    A naive value is treated as UTC rather than rejected: the report has
    never been observed to omit the offset, and refusing the whole row for
    it would lose a sale over a formatting quirk. The result is always UTC.
    """
    if not value:
        raise ValueError(f"{column} is blank")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{column} is not an ISO 8601 timestamp: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# --- upsert --------------------------------------------------------------------


@dataclass(slots=True)
class UpsertCounts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


def upsert_order_lines(
    session: Session,
    *,
    organization_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    marketplace_id: str,
    lines: Sequence[ParsedOrderLine],
) -> UpsertCounts:
    """Insert new lines, update stale ones, touch the rest.

    Two rules, both enforced in SQL as well as counted in Python:

    * data columns change only when the incoming ``last_updated_at`` is newer
      than the stored one — a re-run of an old window cannot regress a line;
    * ``last_seen_sync_run_id`` is set on every line the report contained,
      changed or not, so "when did Amazon last mention this line" is a query.

    Counts are classified from a read of the existing keys in the same
    transaction. The one-RUNNING-run index makes concurrent writers to the
    same tenant impossible, so the read cannot go stale mid-batch.
    """
    counts = UpsertCounts()
    for start in range(0, len(lines), BATCH_SIZE):
        batch = lines[start : start + BATCH_SIZE]
        existing = _existing_last_updated(session, organization_id, batch)

        for line in batch:
            stored = existing.get((line.amazon_order_id, line.seller_sku))
            if stored is None:
                counts.created += 1
            elif line.last_updated_at > stored:
                counts.updated += 1
            else:
                counts.unchanged += 1

        session.execute(_upsert_statement(organization_id, sync_run_id, marketplace_id, batch))
    return counts


def _existing_last_updated(
    session: Session, organization_id: uuid.UUID, batch: Iterable[ParsedOrderLine]
) -> dict[tuple[str, str], datetime]:
    order_ids = {line.amazon_order_id for line in batch}
    rows = session.execute(
        select(
            AmazonOrderLine.amazon_order_id,
            AmazonOrderLine.seller_sku,
            AmazonOrderLine.last_updated_at,
        ).where(
            AmazonOrderLine.organization_id == organization_id,
            AmazonOrderLine.amazon_order_id.in_(order_ids),
        )
    ).all()
    return {(order_id, sku): last_updated for order_id, sku, last_updated in rows}


def _upsert_statement(
    organization_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    marketplace_id: str,
    batch: Sequence[ParsedOrderLine],
) -> Any:
    values = [
        {
            "organization_id": organization_id,
            "amazon_order_id": line.amazon_order_id,
            "seller_sku": line.seller_sku,
            "asin": line.asin,
            "quantity_ordered": line.quantity_ordered,
            "purchase_date": line.purchase_date,
            "last_updated_at": line.last_updated_at,
            "order_status": line.order_status,
            "item_status": line.item_status,
            "fulfillment_channel": line.fulfillment_channel,
            "sales_channel": line.sales_channel,
            "marketplace_id": marketplace_id,
            "currency": line.currency,
            "item_price": line.item_price,
            "first_seen_sync_run_id": sync_run_id,
            "last_seen_sync_run_id": sync_run_id,
            "raw": {
                **line.raw,
                "_prms_readiness": {
                    "version": ORDER_EVIDENCE_VERSION,
                    "run_id": str(sync_run_id),
                    "aggregated": line.aggregated,
                },
            },
        }
        for line in batch
    ]
    statement = insert(AmazonOrderLine).values(values)
    excluded = statement.excluded
    is_newer = excluded.last_updated_at > AmazonOrderLine.last_updated_at
    same_values = and_(
        *(
            getattr(AmazonOrderLine, name).is_not_distinct_from(getattr(excluded, name))
            for name in (
                "asin",
                "quantity_ordered",
                "purchase_date",
                "last_updated_at",
                "order_status",
                "item_status",
                "fulfillment_channel",
                "sales_channel",
                "marketplace_id",
                "currency",
                "item_price",
            )
        )
    )

    def only_if_newer(column: Any) -> Any:
        return case((is_newer, getattr(excluded, column.key)), else_=column)

    return statement.on_conflict_do_update(
        index_elements=[
            AmazonOrderLine.organization_id,
            AmazonOrderLine.amazon_order_id,
            AmazonOrderLine.seller_sku,
        ],
        set_={
            "asin": only_if_newer(AmazonOrderLine.asin),
            "quantity_ordered": only_if_newer(AmazonOrderLine.quantity_ordered),
            "purchase_date": only_if_newer(AmazonOrderLine.purchase_date),
            "last_updated_at": only_if_newer(AmazonOrderLine.last_updated_at),
            "order_status": only_if_newer(AmazonOrderLine.order_status),
            "item_status": only_if_newer(AmazonOrderLine.item_status),
            "fulfillment_channel": only_if_newer(AmazonOrderLine.fulfillment_channel),
            "sales_channel": only_if_newer(AmazonOrderLine.sales_channel),
            "currency": only_if_newer(AmazonOrderLine.currency),
            "item_price": only_if_newer(AmazonOrderLine.item_price),
            # Certify an equal observation without replacing any business values.
            # Ignored older/conflicting observations retain their original marker.
            "raw": case((is_newer | same_values, excluded.raw), else_=AmazonOrderLine.raw),
            # Every line the report mentioned was seen by this run.
            "last_seen_sync_run_id": excluded.last_seen_sync_run_id,
        },
    )


# --- the run ---------------------------------------------------------------------


def run_orders_sync(
    session: Session,
    organization_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
    trigger_type: TriggerType,
    triggered_by_user_id: uuid.UUID | None = None,
    client: ReportFetcher | None = None,
    *,
    marketplace_id: str | None = None,
    report_basis: Literal["LAST_UPDATE", "ORDER_DATE"] = "LAST_UPDATE",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AmazonSyncRun:
    """Perform one orders ingestion run and return its closed run record.

    Raises :class:`OrdersSyncAlreadyRunning` if a run is already in flight,
    ``ValueError`` for a bad window, and otherwise re-raises whatever stopped
    the run — after the run row has been closed as ``FAILED`` with the
    exception recorded. The row is never left ``RUNNING``.
    """
    started_at = now()
    if report_basis not in ("LAST_UPDATE", "ORDER_DATE"):
        raise ValueError("Unsupported orders report basis")
    validate_window(window_start, window_end, now=started_at)

    settings = get_settings()
    if client is None:
        client = AmazonClient(AmazonConfig.from_settings(settings))
    marketplace_id = marketplace_id or settings.amazon_marketplace_id

    try:
        run = open_run(
            session,
            organization_id=organization_id,
            job_type=AmazonSyncJobType.ORDERS_REPORT,
            trigger_type=trigger_type,
            marketplace_id=marketplace_id,
            started_at=started_at,
            window_start=window_start,
            window_end=window_end,
            triggered_by_user_id=triggered_by_user_id,
        )
    except SyncAlreadyRunning as exc:
        raise OrdersSyncAlreadyRunning(str(exc)) from exc

    failure: BaseException | None = None
    try:
        report_type = ORDER_DATE_REPORT_TYPE if report_basis == "ORDER_DATE" else REPORT_TYPE
        content = client.fetch_report(report_type, window_start, window_end)
        parsed = parse_orders_report(content)
        _logger.info(
            "amazon.orders_sync.parsed",
            run_id=str(run.id),
            rows_seen=parsed.rows_seen,
            rows_failed=parsed.rows_failed,
            lines=len(parsed.lines),
            encoding=parsed.encoding,
        )

        with transaction(session):
            counts = upsert_order_lines(
                session,
                organization_id=organization_id,
                sync_run_id=run.id,
                marketplace_id=marketplace_id,
                lines=parsed.lines,
            )
            close_run(
                session,
                run,
                status=(
                    SyncStatus.COMPLETED_WITH_ERRORS if parsed.rows_failed else SyncStatus.COMPLETED
                ),
                audit_action=AUDIT_ACTION_COMPLETED,
                actor_label=AUDIT_ACTOR_LABEL,
                completed_at=now(),
                rows_seen=parsed.rows_seen,
                rows_created=counts.created,
                rows_updated=counts.updated,
                rows_unchanged=counts.unchanged,
                rows_failed=parsed.rows_failed,
                error_details={
                    "rows": parsed.errors[:100],
                    "readiness": {
                        "version": ORDER_EVIDENCE_VERSION,
                        "report_basis": report_basis,
                        "report_type": report_type,
                    },
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
        "amazon.orders_sync.completed",
        run_id=str(run.id),
        status=run.status.value,
        rows_seen=run.rows_seen,
        rows_created=run.rows_created,
        rows_updated=run.rows_updated,
        rows_unchanged=run.rows_unchanged,
        rows_failed=run.rows_failed,
    )
    return run
