"""Read-only, evidence-gated normalization of known Amazon product listings."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.security import Principal, RoleCode
from app.models.amazon import AmazonInventorySnapshot, AmazonSyncRun
from app.models.catalog import MarketplaceListing
from app.models.enums import (
    AmazonSyncJobType,
    ListingStatus,
    MappingStatus,
    ProductStatus,
    SyncStatus,
)
from app.repositories.amazon_readiness import (
    MAX_LISTINGS,
    MAX_ORDER_LINES,
    MAX_RUNS,
    AmazonReadinessRepository,
)
from app.schemas.amazon_readiness import (
    AmazonReadinessRequest,
    AmazonReadinessResponse,
    ReadinessIssue,
    ReadinessListing,
    ReadinessPool,
    ReadinessRun,
    StockPoolChoice,
)
from app.services.amazon_orders import ORDER_DATE_REPORT_TYPE, ORDER_EVIDENCE_VERSION, REPORT_TYPE


class ReadinessProductNotFound(NotFoundError):  # noqa: N818
    code = "amazon_readiness_product_not_found"
    message = "No such product in this organization."


def covers_window(
    intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> bool:
    cursor = start
    for left, right in sorted(intervals):
        if left > cursor:
            return False
        if right > cursor:
            cursor = right
        if cursor >= end:
            return True
    return False


def _time(value: datetime | None, at: datetime) -> TypeGuard[datetime]:
    return (
        value is not None
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value <= at
    )


def _clean(run: AmazonSyncRun, at: datetime) -> bool:
    return (
        run.status == SyncStatus.COMPLETED
        and run.rows_failed == 0
        and _time(run.started_at, at)
        and _time(run.completed_at, at)
        and run.started_at <= run.completed_at
    )


def _order_contract(run: AmazonSyncRun) -> str | None:
    evidence = run.error_details.get("readiness", {})
    if not isinstance(evidence, dict) or evidence.get("version") != ORDER_EVIDENCE_VERSION:
        return None
    basis = evidence.get("report_basis")
    if basis == "ORDER_DATE" and evidence.get("report_type") == ORDER_DATE_REPORT_TYPE:
        return "ORDER_DATE"
    if basis == "LAST_UPDATE" and evidence.get("report_type") == REPORT_TYPE:
        return "LAST_UPDATE"
    return None


def _reference(value: str | None) -> bool:
    return value is not None and len(value.strip()) >= 10


def _raw_int(value: object, stored: int | None) -> int | None:
    return value if type(value) is int and value >= 0 and value == stored else None


def _listing_quantity(raw: dict[str, Any], stored: int | None) -> int | None:
    value = raw.get("quantity")
    if not isinstance(value, str) or not value:
        return None
    try:
        quantity = int(value)
    except ValueError:
        return None
    return quantity if quantity >= 0 and quantity == stored else None


def _run_response(run: AmazonSyncRun | None) -> ReadinessRun | None:
    return (
        ReadinessRun(
            id=run.id,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            window_start=run.window_start,
            window_end=run.window_end,
        )
        if run
        else None
    )


def check_amazon_readiness(
    session: Session,
    principal: Principal,
    request: AmazonReadinessRequest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AmazonReadinessResponse:
    principal.require_roles(
        (RoleCode.ADMIN, RoleCode.PURCHASING_MANAGER, RoleCode.DATA_OPERATOR, RoleCode.VIEWER)
    )
    at = now()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Evaluation time must be timezone-aware")
    at = at.astimezone(UTC)
    repository = AmazonReadinessRepository(session, principal.organization_id)
    product = repository.product(request.product_id)
    if product is None:
        raise ReadinessProductNotFound()
    issues: list[ReadinessIssue] = []

    def issue(code: str, listing: uuid.UUID | None = None, pool: str | None = None) -> None:
        value = ReadinessIssue(code=code, listing_id=listing, pool_id=pool)
        if value not in issues:
            issues.append(value)

    if not product.is_active or product.status != ProductStatus.ACTIVE:
        issue("PRODUCT_INACTIVE")
    listings = repository.listings(product.id, request.marketplace_id)
    if len(listings) > MAX_LISTINGS:
        issue("LISTING_READ_LIMIT_EXCEEDED")
        listings = listings[:MAX_LISTINGS]
    by_id = {listing.id: listing for listing in listings}
    by_sku = {listing.seller_sku: listing for listing in listings}
    if not listings:
        issue("NO_KNOWN_PRODUCT_LISTINGS")
    choices = {choice.listing_id: choice for choice in request.listing_units}
    if set(choices) != set(by_id):
        issue("LISTING_CONFIGURATION_INCOMPLETE")
    factors: dict[uuid.UUID, int | None] = {}
    for listing in listings:
        valid_mapping = (
            listing.mapping_status == MappingStatus.APPROVED
            and listing.is_active
            and listing.listing_status == ListingStatus.ACTIVE
        )
        if not valid_mapping:
            issue("MAPPING_NOT_ACTIVE_APPROVED", listing.id)
        if (
            not listing.approved_by_user_id
            or not listing.mapping_method
            or not _time(listing.approved_at, at)
        ):
            issue("MAPPING_EVIDENCE_UNCONFIRMED", listing.id)
            valid_mapping = False
        choice = choices.get(listing.id)
        factor = choice.base_units_per_listing_unit if choice else None
        if factor is None or choice is None or not _reference(choice.confirmation_reference):
            issue("UNIT_CONVERSION_UNCONFIRMED", listing.id)
            factor = None
        factors[listing.id] = factor if valid_mapping else None

    orders_runs = repository.runs(request.marketplace_id, AmazonSyncJobType.ORDERS_REPORT)
    inventory_runs = repository.runs(request.marketplace_id, AmazonSyncJobType.FBA_INVENTORY)

    def latest(runs: list[AmazonSyncRun], name: str) -> AmazonSyncRun | None:
        if len(runs) > MAX_RUNS:
            issue(name + "_RUN_READ_LIMIT_EXCEEDED")
        if not runs:
            issue(name + "_RUN_MISSING")
            return None
        if any(run.started_at is None for run in runs):
            issue(name + "_RUN_TIME_UNCONFIRMED")
        run = runs[0]
        if len(runs) > 1 and runs[1].started_at == run.started_at:
            issue(name + "_RUN_TIME_AMBIGUOUS")
        if not _clean(run, at):
            issue(name + "_RUN_NOT_CLEAN_COMPLETE")
        return run

    latest_orders = latest(orders_runs, "ORDERS")
    inventory = latest(inventory_runs, "INVENTORY")
    baselines = [
        run
        for run in orders_runs
        if _clean(run, at)
        and _order_contract(run) == "ORDER_DATE"
        and _time(run.window_start, at)
        and _time(run.window_end, at)
        and run.window_start < run.window_end
        and run.completed_at is not None
        and run.window_end <= run.completed_at
        and run.window_end - run.window_start <= timedelta(days=30)
    ]
    end = max((run.window_end for run in baselines if run.window_end), default=at)
    start = end - timedelta(days=request.velocity_window_days)
    coverage = [
        run
        for run in baselines
        if run.window_end is not None
        and run.window_start is not None
        and run.window_end > start
        and run.window_start < end
    ]
    if not baselines:
        issue("PURCHASE_WINDOW_BASELINE_UNPROVEN")
    elif not covers_window(
        [
            (run.window_start, run.window_end)
            for run in coverage
            if run.window_start and run.window_end
        ],
        start,
        end,
    ):
        issue("PURCHASE_WINDOW_COVERAGE_GAP")
    if at - end > timedelta(seconds=request.orders_max_age_seconds):
        issue("ORDERS_DATA_STALE")
    if inventory and (
        not _time(inventory.started_at, at)
        or at - inventory.started_at > timedelta(seconds=request.inventory_max_age_seconds)
    ):
        issue("INVENTORY_DATA_STALE_OR_FUTURE")

    snapshots = repository.snapshots(inventory.id, set(by_sku)) if inventory else {}
    rows = repository.orders(request.marketplace_id, set(by_sku), start, end)
    if len(rows) > MAX_ORDER_LINES:
        issue("ORDER_LINE_READ_LIMIT_EXCEEDED")
    demand: dict[tuple[uuid.UUID, str], int | None] = {
        (identifier, channel): 0 for identifier in by_id for channel in ("FBA", "FBM")
    }
    invalid_demand: set[uuid.UUID] = set()
    for line, run in rows[:MAX_ORDER_LINES]:
        listing = by_sku[line.seller_sku]
        if line.sales_channel is not None and line.sales_channel != request.sales_channel:
            continue
        raw = line.raw.get("_prms_readiness", {})
        latest_baseline_observation = max(
            (
                baseline.completed_at
                for baseline in coverage
                if baseline.window_start is not None
                and baseline.window_end is not None
                and baseline.completed_at is not None
                and baseline.window_start <= line.purchase_date < baseline.window_end
            ),
            default=None,
        )
        valid = (
            isinstance(raw, dict)
            and raw.get("version") == ORDER_EVIDENCE_VERSION
            and raw.get("run_id") == str(line.last_seen_sync_run_id)
            and raw.get("aggregated") is False
        )
        valid = (
            valid
            and run is not None
            and _clean(run, at)
            and _order_contract(run) is not None
            and _time(run.completed_at, at)
            and _time(run.window_start, at)
            and _time(run.window_end, at)
            and run.window_start < run.window_end
            and run.window_end <= run.completed_at
            and (
                latest_baseline_observation is None
                or run.completed_at >= latest_baseline_observation
            )
            and _time(line.last_updated_at, at)
            and line.last_updated_at <= run.completed_at
            and run.window_start
            <= (
                line.purchase_date if _order_contract(run) == "ORDER_DATE" else line.last_updated_at
            )
            <= run.window_end
        )
        if not valid:
            issue("ORDER_LINE_PROVENANCE_OR_AGGREGATION_UNCONFIRMED", listing.id)
            invalid_demand.add(listing.id)
            continue
        if line.sales_channel is None:
            issue("ORDER_MARKETPLACE_UNCONFIRMED", listing.id)
            invalid_demand.add(listing.id)
            continue
        if line.asin is None or listing.asin is None or line.asin != listing.asin:
            issue("ORDER_ASIN_CONFLICT_OR_MISSING", listing.id)
            invalid_demand.add(listing.id)
            continue
        if line.order_status in (
            "Canceled",
            "Cancelled",
            "Pending",
            "PendingAvailability",
            "Unshipped",
            "PartiallyShipped",
            "InvoiceUnconfirmed",
            "Unfulfillable",
        ):
            continue
        if line.order_status != "Shipped" or line.item_status != "Shipped":
            issue("ORDER_SHIPPED_STATUS_UNCONFIRMED", listing.id)
            invalid_demand.add(listing.id)
            continue
        channel = {"Amazon": "FBA", "Merchant": "FBM"}.get(line.fulfillment_channel or "")
        if channel is None:
            issue("ORDER_FULFILLMENT_CHANNEL_UNCONFIRMED", listing.id)
            invalid_demand.add(listing.id)
            continue
        key = (listing.id, channel)
        previous = demand[key]
        assert previous is not None
        demand[key] = previous + line.quantity_ordered
    order_global_codes = {
        value.code
        for value in issues
        if value.listing_id is None
        and (
            value.code.startswith("ORDERS_")
            or value.code.startswith("PURCHASE_WINDOW_")
            or value.code.startswith("ORDER_LINE_READ")
        )
    }
    for identifier, channel in demand:
        if identifier in invalid_demand or order_global_codes:
            demand[(identifier, channel)] = None

    assignments = {
        (identifier, pool.channel): pool.pool_id
        for pool in request.stock_pools
        for identifier in pool.listing_ids
    }
    for listing in listings:
        snapshot = snapshots.get(listing.seller_sku)
        if snapshot is None:
            issue("LATEST_RUN_SKU_MISSING", listing.id)
            continue
        if snapshot.asin != listing.asin or not listing.asin:
            issue("INVENTORY_ASIN_CONFLICT_OR_MISSING", listing.id)
        if (
            not _time(snapshot.captured_at, at)
            or not inventory
            or not inventory.started_at
            or not inventory.completed_at
            or snapshot.captured_at < inventory.started_at
            or snapshot.captured_at > inventory.completed_at
            or at - snapshot.captured_at > timedelta(seconds=request.inventory_max_age_seconds)
        ):
            issue("SNAPSHOT_TIME_STALE_FUTURE_OR_INVALID", listing.id)
        if snapshot.amazon_last_updated_at and not _time(snapshot.amazon_last_updated_at, at):
            issue("INVENTORY_CHANGE_TIME_FUTURE_OR_INVALID", listing.id)
        listing_raw = snapshot.raw.get("listing", {})
        for channel, present in (
            ("FBA", snapshot.raw.get("source") == "fba_inventory"),
            (
                "FBM",
                isinstance(listing_raw, dict)
                and listing_raw.get("fulfillment-channel") == "DEFAULT",
            ),
        ):
            if (present or (demand[(listing.id, channel)] or 0) > 0) and (
                listing.id,
                channel,
            ) not in assignments:
                issue("STOCK_POOL_ASSIGNMENT_MISSING", listing.id)

    pools: list[ReadinessPool] = []
    identities: dict[tuple[str, str | None], str] = {}
    for pool in request.stock_pools:
        result = _pool(pool, by_id, snapshots, factors, demand, issue)
        pools.append(result)
        if pool.channel == "FBA" and pool.stock_source_listing_id in by_id:
            snapshot = snapshots.get(by_id[pool.stock_source_listing_id].seller_sku)
            if snapshot and snapshot.fnsku:
                identity_key = (snapshot.fnsku, snapshot.condition)
                if identity_key in identities:
                    issue("FBA_STOCK_POOL_OVERLAP", pool=pool.pool_id)
                    issue("FBA_STOCK_POOL_OVERLAP", pool=identities[identity_key])
                identities[identity_key] = pool.pool_id
    if not pools:
        issue("STOCK_POOLS_UNCONFIRMED")
    pools = [
        pool.model_copy(
            update={
                "quantity_input_ready": all(
                    value is not None
                    for value in (
                        pool.sales_base_units,
                        pool.on_hand_base_units,
                        pool.usable_inbound_base_units,
                    )
                )
                and not any(
                    value.pool_id == pool.pool_id
                    or value.listing_id in pool.listing_ids
                    or (value.pool_id is None and value.listing_id is None)
                    for value in issues
                )
            }
        )
        for pool in pools
    ]
    return AmazonReadinessResponse(
        request=request,
        evaluated_at=at,
        demand_window_start=start,
        demand_window_end=end,
        status="DATA_AVAILABLE"
        if pools and not issues and all(pool.quantity_input_ready for pool in pools)
        else "BLOCKED",
        issues=tuple(
            sorted(
                issues,
                key=lambda value: (value.code, str(value.listing_id or ""), value.pool_id or ""),
            )
        ),
        latest_orders_run=_run_response(latest_orders),
        order_coverage_run_ids=tuple(sorted((run.id for run in coverage), key=str)),
        inventory_run=_run_response(inventory),
        listings=tuple(
            ReadinessListing(
                listing_id=listing.id,
                seller_sku=listing.seller_sku,
                asin=listing.asin,
                mapping_status=listing.mapping_status.value,
                mapping_method=listing.mapping_method.value if listing.mapping_method else None,
                approved_by_user_id=listing.approved_by_user_id,
                approved_at=listing.approved_at,
                base_units_per_listing_unit=factors[listing.id],
                snapshot_id=snapshots[listing.seller_sku].id
                if listing.seller_sku in snapshots
                else None,
                captured_at=snapshots[listing.seller_sku].captured_at
                if listing.seller_sku in snapshots
                else None,
                fba_sold_listing_units=demand[(listing.id, "FBA")],
                fbm_sold_listing_units=demand[(listing.id, "FBM")],
            )
            for listing in listings
        ),
        pools=tuple(sorted(pools, key=lambda value: value.pool_id)),
    )


def _pool(
    pool: StockPoolChoice,
    listings: dict[uuid.UUID, MarketplaceListing],
    snapshots: dict[str, AmazonInventorySnapshot],
    factors: dict[uuid.UUID, int | None],
    demand: dict[tuple[uuid.UUID, str], int | None],
    issue: Callable[..., None],
) -> ReadinessPool:
    sales: int | None = 0
    on_hand: int | None = None
    inbound: int | None = None
    usable: int | None = None
    if not _reference(pool.distinct_stock_reference):
        issue("STOCK_POOL_DISTINCTNESS_UNCONFIRMED", pool=pool.pool_id)
    members = [listings[identifier] for identifier in pool.listing_ids if identifier in listings]
    if len(members) != len(pool.listing_ids):
        issue("POOL_LISTING_NOT_IN_PRODUCT", pool=pool.pool_id)
    for listing in members:
        factor = factors.get(listing.id)
        sold = demand[(listing.id, pool.channel)]
        sales = (
            sales + sold * factor
            if sales is not None and sold is not None and factor is not None
            else None
        )
    source = listings.get(pool.stock_source_listing_id)
    snapshot = snapshots.get(source.seller_sku) if source else None
    factor = factors.get(pool.stock_source_listing_id)
    if snapshot is None or source is None:
        issue("POOL_STOCK_SOURCE_MISSING", pool=pool.pool_id)
    elif pool.channel == "FBA":
        if (
            snapshot.raw.get("source") != "fba_inventory"
            or snapshot.raw.get("seller_sku") != source.seller_sku
            or snapshot.raw.get("asin") != snapshot.asin
        ):
            issue("FBA_STOCK_PROVENANCE_UNCONFIRMED", pool=pool.pool_id)
        else:
            quantity = _raw_int(snapshot.raw.get("fulfillable"), snapshot.fulfillable)
            parts = tuple(
                _raw_int(snapshot.raw.get(name), getattr(snapshot, name))
                for name in ("inbound_working", "inbound_shipped", "inbound_receiving")
            )
            on_hand = quantity * factor if quantity is not None and factor is not None else None
            inbound = (
                sum(value for value in parts if value is not None) * factor
                if all(value is not None for value in parts) and factor is not None
                else None
            )
            usable = 0 if inbound == 0 else None
            if quantity is None:
                issue("FBA_FULFILLABLE_UNKNOWN", pool=pool.pool_id)
            if inbound is None:
                issue("FBA_INBOUND_UNKNOWN", pool=pool.pool_id)
            elif inbound > 0:
                issue("INBOUND_ARRIVAL_UNCONFIRMED", pool=pool.pool_id)
        for member in members:
            candidate = snapshots.get(member.seller_sku)
            if (
                not candidate
                or not snapshot.fnsku
                or not snapshot.condition
                or candidate.fnsku != snapshot.fnsku
                or candidate.condition != snapshot.condition
                or candidate.raw.get("source") != "fba_inventory"
            ):
                issue("FBA_POOL_IDENTITY_UNCONFIRMED", member.id, pool.pool_id)
            elif (
                factors.get(member.id) != factor
                or any(
                    candidate.raw.get(name) != snapshot.raw.get(name)
                    for name in (
                        "fulfillable",
                        "inbound_working",
                        "inbound_shipped",
                        "inbound_receiving",
                    )
                )
                or candidate.captured_at != snapshot.captured_at
            ):
                issue("FBA_POOL_QUANTITY_OR_UNITS_CONFLICT", member.id, pool.pool_id)
    else:
        for member in members:
            candidate = snapshots.get(member.seller_sku)
            raw = candidate.raw.get("listing", {}) if candidate else {}
            if (
                not candidate
                or not isinstance(raw, dict)
                or raw.get("fulfillment-channel") != "DEFAULT"
                or raw.get("seller-sku") != member.seller_sku
                or raw.get("asin1") != candidate.asin
            ):
                issue("FBM_STOCK_PROVENANCE_UNCONFIRMED", member.id, pool.pool_id)
        raw = snapshot.raw.get("listing", {})
        quantity = _listing_quantity(raw, snapshot.fbm_quantity) if isinstance(raw, dict) else None
        on_hand = quantity * factor if quantity is not None and factor is not None else None
        if quantity is None:
            issue("FBM_STOCK_QUANTITY_UNKNOWN", pool=pool.pool_id)
        issue("FBM_INBOUND_NOT_INGESTED", pool=pool.pool_id)
    return ReadinessPool(
        pool_id=pool.pool_id,
        channel=pool.channel,
        listing_ids=pool.listing_ids,
        stock_source_listing_id=pool.stock_source_listing_id,
        sales_base_units=sales,
        on_hand_base_units=on_hand,
        observed_inbound_base_units=inbound,
        usable_inbound_base_units=usable,
        quantity_input_ready=False,
    )
