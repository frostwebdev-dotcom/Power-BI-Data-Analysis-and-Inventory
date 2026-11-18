"""Read-only historical financial evidence for the complete known listing set."""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal, localcontext
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.security import Principal
from app.models.catalog import MarketplaceListing
from app.models.financial import FinancialBindingRow, FinancialImportRow
from app.repositories.financial import FinancialRepository
from app.schemas.amazon_readiness import AmazonReadinessResponse, ReadinessListing
from app.schemas.connectbooks import (
    ConnectBooksControl,
    ConnectBooksPreviewRow,
    ConnectBooksXlsxProfile,
)
from app.schemas.financial_context import (
    FinancialSelection,
    HistoricalFinancialContext,
    HistoricalFinancialRow,
)
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportNotFound,
    reporting_bounds,
)

MONEY = {
    "net_sales": "net_sales",
    "fees": "fees",
    "cost": "cost",
    "ad_spend": "ad_spend",
    "reported_profit": "profit",
    "computed_profit": "computed_profit",
}


def mapping_evidence(listing: MarketplaceListing) -> dict[str, Any]:
    return {
        "listing_id": str(listing.id),
        "product_id": str(listing.product_id),
        "seller_sku": listing.seller_sku,
        "asin": listing.asin,
        "approved_by_user_id": str(listing.approved_by_user_id)
        if listing.approved_by_user_id
        else None,
        "approved_at": listing.approved_at.isoformat() if listing.approved_at else None,
        "mapping_method": listing.mapping_method.value if listing.mapping_method else None,
        "match_priority": 4,
    }


def historical_row(
    listing: ReadinessListing,
    product_id: uuid.UUID,
    sources: list[FinancialImportRow],
    binding: FinancialBindingRow | None,
    revised: bool,
    current: MarketplaceListing | None,
) -> HistoricalFinancialRow:
    reasons: set[str] = set()
    source = sources[0] if len(sources) == 1 else None
    mapping = binding if revised else source
    current_evidence = mapping_evidence(current) if current else None
    parsed = None
    if source is None:
        reasons.add("FINANCIAL_SKU_MISSING" if not sources else "FINANCIAL_SKU_DUPLICATED")
    else:
        try:
            parsed = ConnectBooksPreviewRow.model_validate(source.source_snapshot)
        except ValidationError:
            reasons.add("FINANCIAL_SOURCE_INVALID")
        if parsed is not None:
            reasons.update(issue.code for issue in parsed.issues)
            if (
                parsed.row_number != source.source_row_number
                or parsed.seller_sku != listing.seller_sku
                or parsed.row_kind != "SKU"
                or parsed.status == "INVALID"
            ):
                reasons.add("FINANCIAL_SOURCE_INVALID")
            for column, key in MONEY.items():
                value = (
                    parsed.computed_profit
                    if key == "computed_profit"
                    else {str(k): v for k, v in parsed.amounts.items()}.get(key)
                )
                if getattr(source, column) != value:
                    reasons.add("FINANCIAL_SOURCE_INVALID")
        if (
            mapping is None
            or mapping.mapping_status != "LINKED"
            or mapping.match_priority != 4
            or mapping.listing_id != listing.listing_id
            or mapping.product_id != product_id
            or current is None
            or current.id != listing.listing_id
            or current.product_id != product_id
            or not current.asin
            or not current.approved_by_user_id
            or not current.approved_at
            or mapping.mapping_snapshot != current_evidence
        ):
            reasons.add("FINANCIAL_MAPPING_UNVERIFIED")
        if mapping is not None:
            reasons.update(mapping.review_reasons)
    invalid = {
        "FINANCIAL_SOURCE_INVALID",
        "FINANCIAL_MAPPING_UNVERIFIED",
        "FINANCIAL_SKU_MISSING",
        "FINANCIAL_SKU_DUPLICATED",
    } & reasons
    trusted = parsed if not invalid else None
    if trusted and any(
        {str(k): v for k, v in trusted.amounts.items()}.get(key) is None
        for key in ("net_sales", "fees", "cost", "ad_spend", "profit")
    ):
        reasons.add("FINANCIAL_AMOUNTS_INCOMPLETE")
    return HistoricalFinancialRow(
        listing_id=listing.listing_id,
        seller_sku=listing.seller_sku,
        source_row_id=source.id if source else None,
        source_row_number=source.source_row_number if source else None,
        mapping_snapshot=mapping.mapping_snapshot if mapping else None,
        current_mapping_snapshot=current_evidence,
        included_in_totals=trusted is not None and "FINANCIAL_AMOUNTS_INCOMPLETE" not in reasons,
        amounts=trusted.amounts if trusted else {},
        computed_profit=trusted.computed_profit if trusted else None,
        computed_margin_percent=trusted.computed_margin_percent if trusted else None,
        computed_roi_percent=trusted.computed_roi_percent if trusted else None,
        review_reasons=tuple(sorted(reasons)),
    )


def financial_context(
    session: Session,
    actor: Principal,
    selection: FinancialSelection,
    readiness: AmazonReadinessResponse,
) -> HistoricalFinancialContext:
    repository = FinancialRepository(session, actor.organization_id)
    batch = repository.get(selection.import_id)
    if batch is None:
        raise FinancialImportNotFound()
    repository.lock_marketplace(batch.marketplace_id)
    if repository.has_successor(batch.id):
        raise FinancialImportConflict(details={"code": "FINANCIAL_REPORT_REPLACED"})
    head = repository.binding_head(batch.id)
    if (head.id if head else None) != selection.expected_binding_revision_id:
        raise FinancialImportConflict(details={"code": "FINANCIAL_BINDING_CHANGED"})
    if batch.marketplace_id != readiness.request.marketplace_id:
        raise FinancialImportConflict(details={"code": "FINANCIAL_MARKETPLACE_MISMATCH"})
    try:
        profile = ConnectBooksXlsxProfile.model_validate(batch.profile_snapshot)
        start, end = reporting_bounds(profile)
        controls = tuple(
            ConnectBooksControl.model_validate(value)
            for value in batch.report_snapshot.get("controls", [])
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise FinancialImportConflict(details={"code": "FINANCIAL_REPORT_INVALID"}) from exc
    if (
        (start, end) != (batch.period_start, batch.period_end)
        or profile.marketplace_id != batch.marketplace_id
        or profile.store_name != batch.store_name
        or profile.currency != batch.currency
    ):
        raise FinancialImportConflict(details={"code": "FINANCIAL_REPORT_INVALID"})
    skus = {listing.seller_sku for listing in readiness.listings}
    sources, bindings = repository.evaluation_rows(batch.id, skus, head.id if head else None)
    current = repository.approved_listings(batch.marketplace_id, skus)
    current = {
        sku: listing
        for sku, listing in current.items()
        if listing.approved_by_user_id is not None
        and repository.actor_exists(listing.approved_by_user_id)
    }
    grouped: dict[str, list[FinancialImportRow]] = defaultdict(list)
    for source in sources:
        if source.seller_sku is not None:
            grouped[source.seller_sku].append(source)
    rows = tuple(
        historical_row(
            listing,
            readiness.request.product_id,
            grouped[listing.seller_sku],
            bindings.get(grouped[listing.seller_sku][0].id)
            if len(grouped[listing.seller_sku]) == 1
            else None,
            head is not None,
            current.get(listing.seller_sku),
        )
        for listing in readiness.listings
    )
    reasons = {reason for row in rows for reason in row.review_reasons}
    reasons.update(
        str(issue["code"])
        for issue in batch.report_snapshot.get("issues", [])
        if isinstance(issue, dict) and "code" in issue
    )
    if len(sources) > 200:
        reasons.add("FINANCIAL_ROW_LIMIT_EXCEEDED")
    if not rows:
        reasons.add("FINANCIAL_LISTINGS_UNKNOWN")
    if not controls or any(control.status != "MATCH" for control in controls):
        reasons.add("FINANCIAL_CONTROLS_UNCONFIRMED")
    if not profile.advertising_coverage_confirmed:
        reasons.add("ADVERTISING_COVERAGE_UNCONFIRMED")
    unallocated = int(batch.report_snapshot.get("unallocated_rows", 0))
    if unallocated:
        reasons.add("UNALLOCATED_REPORT_AMOUNTS_NOT_DISTRIBUTED")
    if batch.period_end > readiness.evaluated_at:
        reasons.add("FINANCIAL_REPORT_PERIOD_NOT_COMPLETED")
    if (batch.period_start, batch.period_end) != (
        readiness.demand_window_start,
        readiness.demand_window_end,
    ):
        reasons.add("FINANCIAL_AND_DEMAND_WINDOWS_DIFFER")
    totals = None
    if rows and all(row.included_in_totals for row in rows) and len(sources) <= 200:
        with localcontext() as ctx:
            ctx.prec = 80
            totals = {
                key: sum(
                    (value for row in rows if (value := row.amounts.get(key)) is not None),
                    Decimal(0),
                )
                for key in ("net_sales", "fees", "cost", "ad_spend", "profit")
            }
    return HistoricalFinancialContext(
        import_id=batch.id,
        binding_revision_id=head.id if head else None,
        original_filename=batch.original_filename,
        raw_sha256=batch.raw_sha256,
        profile_sha256=batch.profile_sha256,
        profile_snapshot=batch.profile_snapshot,
        marketplace_id=batch.marketplace_id,
        store_name=batch.store_name,
        currency="USD",
        period_start=batch.period_start,
        period_end=batch.period_end,
        received_at=batch.received_at,
        report_status=batch.status,
        controls=controls,
        unallocated_rows=unallocated,
        rows=rows,
        totals=totals,
        status="UNAVAILABLE" if totals is None else "REVIEW_REQUIRED" if reasons else "AVAILABLE",
        review_reasons=tuple(sorted(reasons)),
    )
