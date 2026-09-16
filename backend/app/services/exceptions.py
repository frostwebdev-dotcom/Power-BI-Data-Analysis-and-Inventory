"""The exception queue: where a person decides what the engine could not
(phase 8, AC-8; CLAUDE.md §5.2, §6).

Three decisions, each one transaction with its audit rows:

* **approve** ``{product_id, supersede?, note?}`` — the vendor line (or the
  marketplace listing) becomes an APPROVED mapping to the product with
  ``MANUAL_APPROVAL``, the approver and the UTC moment: the permanent
  mapping priority 3 (or 4) reads on every later import. The item is
  APPROVED with the same approver; the originating row becomes MATCHED by
  ``MANUAL_APPROVAL`` at priority 5 and its job's counters follow. If the
  line already has an APPROVED mapping to a *different* product, the call
  is refused unless ``supersede`` is true; then the old mapping is first
  moved to SUPERSEDED — its own audit row, before/after naming the old
  product — and only then approved to the new one. Nothing is deleted; the
  audit log is the history (ADR 0010, database-schema.md §3.3).
* **reject** ``{note}`` — the item is REJECTED with the reason. No mapping is
  made; a PENDING suggestion the matcher had left on the line is cleared so
  the line is UNMAPPED again. The matcher will not re-queue the same
  evidence for a rejected line (see :mod:`app.services.matching`).
* **defer** ``{until, note?}`` — the item stays PENDING and leaves the
  default queue view until ``until``.

Reads for every role; decisions for PURCHASING_MANAGER (and ADMIN).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import (
    ExceptionReason,
    ExceptionStatus,
    MappingStatus,
    MatchMethod,
    MatchResult,
)
from app.models.ingestion import ImportJob
from app.models.matching import ProductMappingException
from app.models.vendor import VendorProduct
from app.repositories.exceptions import ExceptionPage, ExceptionRepository
from app.schemas.exceptions import ApproveRequest, DeferRequest, RejectRequest
from app.services import audit

_logger = get_logger(__name__)


class ExceptionNotFound(NotFoundError):  # noqa: N818
    code = "exception_not_found"
    message = "No such exception in this organization."


class ExceptionNotPending(ConflictError):  # noqa: N818
    code = "exception_not_pending"
    message = "Only a PENDING exception can be resolved or deferred."


class ProductNotFound(NotFoundError):  # noqa: N818
    code = "product_not_found"
    message = "No such active product in this organization."


class MappingSupersessionRequired(ConflictError):  # noqa: N818
    code = "mapping_supersession_required"
    message = (
        "This vendor SKU already has an approved mapping to a different product. "
        "Approving a new one supersedes it; pass supersede=true to do so explicitly."
    )


@dataclass(frozen=True, slots=True)
class Resolution:
    item: ProductMappingException
    superseded_product_id: uuid.UUID | None = None


# --- reads ---------------------------------------------------------------------------------


def list_exceptions(
    session: Session,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    status: ExceptionStatus | None,
    reason: ExceptionReason | None,
    vendor_id: uuid.UUID | None,
    import_job_id: uuid.UUID | None,
    min_age_hours: float | None,
    include_deferred: bool,
) -> ExceptionPage:
    return ExceptionRepository(session, principal.organization_id).list(
        page=page,
        page_size=page_size,
        status=status,
        reason=reason,
        vendor_id=vendor_id,
        import_job_id=import_job_id,
        min_age_hours=min_age_hours,
        include_deferred=include_deferred,
    )


def get_exception(
    session: Session, principal: Principal, exception_id: uuid.UUID
) -> ProductMappingException:
    item = ExceptionRepository(session, principal.organization_id).get(exception_id)
    if item is None:
        raise ExceptionNotFound()
    return item


def candidate_products(
    session: Session, principal: Principal, item: ProductMappingException
) -> dict[uuid.UUID, Product]:
    """The products named in the item's candidates, for display (AC-8.2)."""
    ids: list[uuid.UUID] = []
    for candidate in item.candidates.get("products", []):
        try:
            ids.append(uuid.UUID(str(candidate.get("product_id"))))
        except ValueError:
            continue
    return ExceptionRepository(session, principal.organization_id).products_by_ids(ids)


# --- decisions -----------------------------------------------------------------------------


def approve(
    session: Session, principal: Principal, exception_id: uuid.UUID, payload: ApproveRequest
) -> Resolution:
    item = _pending(session, principal, exception_id)
    repository = ExceptionRepository(session, principal.organization_id)
    product = repository.active_product(payload.product_id)
    if product is None:
        raise ProductNotFound()

    line = item.vendor_product
    listing = item.marketplace_listing
    superseded: uuid.UUID | None = None
    now = datetime.now(UTC)

    with transaction(session):
        if line is not None:
            superseded = _approve_line(session, principal, item, line, product, payload, now)
        elif listing is not None:
            superseded = _approve_listing(session, principal, item, listing, product, payload, now)
        # else: a row with neither a vendor SKU nor a listing — the row itself is
        # resolved below, and there is no line on which to make a mapping.

        before = audit.snapshot(item)
        item.status = ExceptionStatus.APPROVED
        item.resolved_product_id = product.id
        item.resolved_by_user_id = principal.user_id
        item.resolved_at = now
        item.resolution_note = payload.note
        item.deferred_until = None
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="mapping_exception.approved",
            instance=item,
            before=before,
            actor=principal,
            summary=(
                f"Exception ({item.reason.value}) approved to {product.catalog_item_number}"
                + (" superseding an earlier mapping" if superseded else "")
                + "."
            ),
        )
        _resolve_row(session, principal, item, product.id)
    _logger.info(
        "mapping_exception.approved",
        exception_id=str(item.id),
        product_id=str(product.id),
        superseded=str(superseded) if superseded else None,
    )
    return Resolution(item=item, superseded_product_id=superseded)


def reject(
    session: Session, principal: Principal, exception_id: uuid.UUID, payload: RejectRequest
) -> Resolution:
    item = _pending(session, principal, exception_id)
    now = datetime.now(UTC)
    with transaction(session):
        line = item.vendor_product
        if line is not None and line.mapping_status is MappingStatus.PENDING:
            # The matcher's suggestion was declined: the line is unmapped again.
            before_line = audit.snapshot(line)
            line.product_id = None
            line.mapping_status = MappingStatus.UNMAPPED
            line.mapping_method = None
            session.flush()
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="vendor_product.mapping_suggestion_rejected",
                instance=line,
                before=before_line,
                actor=principal,
                summary=f"Suggested mapping for {line.vendor_sku} rejected; line is unmapped.",
            )
        before = audit.snapshot(item)
        item.status = ExceptionStatus.REJECTED
        item.resolved_by_user_id = principal.user_id
        item.resolved_at = now
        item.resolution_note = payload.note
        item.deferred_until = None
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="mapping_exception.rejected",
            instance=item,
            before=before,
            actor=principal,
            summary=f"Exception ({item.reason.value}) rejected: {payload.note}",
        )
    return Resolution(item=item)


def defer(
    session: Session, principal: Principal, exception_id: uuid.UUID, payload: DeferRequest
) -> Resolution:
    item = _pending(session, principal, exception_id)
    with transaction(session):
        before = audit.snapshot(item)
        item.deferred_until = payload.until
        if payload.note:
            item.resolution_note = payload.note
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="mapping_exception.deferred",
            instance=item,
            before=before,
            actor=principal,
            summary=f"Exception ({item.reason.value}) deferred until {payload.until.isoformat()}.",
        )
    return Resolution(item=item)


# --- helpers -------------------------------------------------------------------------------


def _pending(
    session: Session, principal: Principal, exception_id: uuid.UUID
) -> ProductMappingException:
    item = get_exception(session, principal, exception_id)
    if item.status is not ExceptionStatus.PENDING:
        raise ExceptionNotPending(details={"status": item.status.value})
    return item


def _approve_line(
    session: Session,
    principal: Principal,
    item: ProductMappingException,
    line: VendorProduct,
    product: Product,
    payload: ApproveRequest,
    now: datetime,
) -> uuid.UUID | None:
    superseded: uuid.UUID | None = None
    if line.mapping_status is MappingStatus.APPROVED and line.product_id != product.id:
        if not payload.supersede:
            raise MappingSupersessionRequired(
                details={
                    "vendor_product_id": str(line.id),
                    "current_product_id": str(line.product_id),
                    "requested_product_id": str(product.id),
                }
            )
        # Step 1 of 2: the old mapping is superseded — its own audited change,
        # so the history shows exactly which product it pointed at and who
        # replaced it (AC-8.6).
        superseded = line.product_id
        before = audit.snapshot(line)
        line.mapping_status = MappingStatus.SUPERSEDED
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="vendor_product.mapping_superseded",
            instance=line,
            before=before,
            actor=principal,
            summary=(
                f"Approved mapping of {line.vendor_sku} to product {superseded} superseded "
                f"in favour of {product.catalog_item_number}."
            ),
        )
    # Step 2: the new permanent mapping.
    before = audit.snapshot(line)
    line.product_id = product.id
    line.mapping_status = MappingStatus.APPROVED
    line.mapping_method = MatchMethod.MANUAL_APPROVAL
    line.mapping_approved_by_user_id = principal.user_id
    line.mapping_approved_at = now
    session.flush()
    audit.record_change(
        session,
        organization_id=principal.organization_id,
        action="vendor_product.mapping_approved",
        instance=line,
        before=before,
        actor=principal,
        summary=(
            f"Vendor SKU {line.vendor_sku} mapped to {product.catalog_item_number} "
            f"by {principal.email}."
        ),
    )
    return superseded


def _approve_listing(
    session: Session,
    principal: Principal,
    item: ProductMappingException,
    listing: MarketplaceListing,
    product: Product,
    payload: ApproveRequest,
    now: datetime,
) -> uuid.UUID | None:
    superseded: uuid.UUID | None = None
    if listing.mapping_status is MappingStatus.APPROVED and listing.product_id != product.id:
        if not payload.supersede:
            raise MappingSupersessionRequired(
                details={
                    "marketplace_listing_id": str(listing.id),
                    "current_product_id": str(listing.product_id),
                    "requested_product_id": str(product.id),
                }
            )
        superseded = listing.product_id
        before = audit.snapshot(listing)
        listing.mapping_status = MappingStatus.SUPERSEDED
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="marketplace_listing.mapping_superseded",
            instance=listing,
            before=before,
            actor=principal,
            summary=(
                f"Approved mapping of listing {listing.seller_sku} to product {superseded} "
                f"superseded in favour of {product.catalog_item_number}."
            ),
        )
    before = audit.snapshot(listing)
    listing.product_id = product.id
    listing.mapping_status = MappingStatus.APPROVED
    listing.mapping_method = MatchMethod.MANUAL_APPROVAL
    listing.approved_by_user_id = principal.user_id
    listing.approved_at = now
    session.flush()
    audit.record_change(
        session,
        organization_id=principal.organization_id,
        action="marketplace_listing.mapping_approved",
        instance=listing,
        before=before,
        actor=principal,
        summary=f"Listing {listing.seller_sku} mapped to {product.catalog_item_number}.",
    )
    return superseded


def _resolve_row(
    session: Session, principal: Principal, item: ProductMappingException, product_id: uuid.UUID
) -> None:
    """The originating row becomes MATCHED by MANUAL_APPROVAL at priority 5, and
    its job's counters move one row from exceptions to matches (AC-9.4)."""
    row = item.import_job_row
    if row is None:
        return
    before = audit.snapshot(row)
    was_exception = row.match_result in (
        MatchResult.AMBIGUOUS,
        MatchResult.UNMATCHED,
        MatchResult.SUGGESTION_ONLY,
    )
    row.match_result = MatchResult.MATCHED
    row.matched_by = MatchMethod.MANUAL_APPROVAL
    row.match_priority = 5
    row.product_id = product_id
    data: dict[str, Any] = dict(row.normalized_data)
    data["resolution"] = {
        "exception_id": str(item.id),
        "product_id": str(product_id),
        "method": MatchMethod.MANUAL_APPROVAL.value,
    }
    row.normalized_data = data
    session.flush()
    audit.record_change(
        session,
        organization_id=principal.organization_id,
        action="import_job_row.matched_manually",
        instance=row,
        before=before,
        actor=principal,
        summary=f"Row {row.row_number} matched by manual approval.",
    )
    job = session.get(ImportJob, row.import_job_id)
    if job is not None and was_exception:
        job.matched_rows += 1
        job.exception_rows = max(0, job.exception_rows - 1)
