"""Retain and record signed financial reports without changing inventory or purchasing."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationFailedError
from app.core.security import Principal, RoleCode
from app.db.transaction import transaction
from app.imports.storage import StorageBackend, StorageError, StorageObjectMissing, sha256_hex
from app.models.catalog import MarketplaceListing
from app.models.financial import FinancialImport, FinancialImportRow
from app.repositories.financial import FinancialRepository
from app.schemas.connectbooks import ConnectBooksPreviewRow, ConnectBooksXlsxProfile
from app.services import audit
from app.services.connectbooks import MAX_XLSX_BYTES, preview_connectbooks_xlsx
from app.services.profitability import ProfitabilityPreviewError

FINANCIAL_READ_ROLES: tuple[RoleCode, ...] = (
    RoleCode.ADMIN,
    RoleCode.PURCHASING_MANAGER,
    RoleCode.DATA_OPERATOR,
    RoleCode.VIEWER,
)


class FinancialImportNotFound(NotFoundError):  # noqa: N818
    code = "financial_import_not_found"
    message = "No such financial import in this organization."


class FinancialImportConflict(ConflictError):  # noqa: N818
    code = "financial_import_conflict"
    message = "This financial import conflicts with an existing record."


class FinancialContextMissing(ValidationFailedError):  # noqa: N818
    code = "financial_reporting_context_required"
    message = "Confirm the reporting year and timezone before recording a financial import."


class FinancialInputInvalid(ValidationFailedError):  # noqa: N818
    code = "financial_input_invalid"
    message = "The financial input could not be accepted."


@dataclass(frozen=True)
class FinancialImportOutcome:
    batch: FinancialImport
    duplicate: bool


def reporting_bounds(profile: ConnectBooksXlsxProfile) -> tuple[datetime, datetime]:
    if profile.reporting_year is None or profile.reporting_timezone is None:
        raise FinancialContextMissing()
    year, month = profile.reporting_year, profile.reporting_month
    zone = ZoneInfo(profile.reporting_timezone)
    local = (datetime(year, month, 1), datetime(year + (month == 12), month % 12 + 1, 1))
    bounds: list[datetime] = []
    for value in local:
        zoned = value.replace(tzinfo=zone)
        utc = zoned.astimezone(UTC)
        if (
            utc.astimezone(zone).replace(tzinfo=None) != value
            or zoned.utcoffset() != zoned.replace(fold=1).utcoffset()
        ):
            raise FinancialInputInvalid(details={"code": "AMBIGUOUS_MONTH_BOUNDARY"})
        bounds.append(utc)
    return bounds[0], bounds[1]


def _row(
    source: ConnectBooksPreviewRow,
    batch_id: uuid.UUID,
    organization_id: uuid.UUID,
    listings: dict[str, MarketplaceListing],
) -> FinancialImportRow:
    listing = listings.get(source.seller_sku or "")
    status = "UNRESOLVED"
    if source.row_kind == "UNALLOCATED":
        status = "UNALLOCATED"
    elif source.status == "INVALID":
        status = "INVALID_SOURCE"
    elif listing is not None:
        status = (
            "ASIN_CONFLICT"
            if source.asin and listing.asin and source.asin != listing.asin
            else "LINKED"
        )
    linked = listing if status == "LINKED" else None
    reasons = [issue.code for issue in source.issues]
    if status != "LINKED":
        reasons.append(f"MAPPING_{status}")
    mapping = (
        {
            "listing_id": str(linked.id),
            "product_id": str(linked.product_id),
            "seller_sku": linked.seller_sku,
            "asin": linked.asin,
            "approved_by_user_id": str(linked.approved_by_user_id),
            "approved_at": linked.approved_at.isoformat() if linked.approved_at else None,
            "mapping_method": linked.mapping_method.value if linked.mapping_method else None,
            "match_priority": 4,
        }
        if linked is not None
        else {}
    )
    return FinancialImportRow(
        id=uuid.uuid4(),
        organization_id=organization_id,
        import_id=batch_id,
        source_row_number=source.row_number,
        seller_sku=source.seller_sku,
        row_kind=source.row_kind,
        mapping_status=status,
        listing_id=linked.id if linked else None,
        product_id=linked.product_id if linked else None,
        match_priority=4 if linked else None,
        mapping_snapshot=mapping,
        source_snapshot=source.model_dump(mode="json"),
        review_reasons=sorted(set(reasons)),
        review_required=source.status != "RECONCILED" or status != "LINKED",
        purchasing_eligible=False,
        net_sales=source.amounts["net_sales"],
        fees=source.amounts["fees"],
        cost=source.amounts["cost"],
        ad_spend=source.amounts["ad_spend"],
        reported_profit=source.amounts["profit"],
        computed_profit=source.computed_profit,
    )


def financial_filename(filename: str, content: bytes) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if (
        not name
        or len(name) > 255
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or PurePosixPath(name).suffix.lower() != ".xlsx"
        or not content
        or len(content) > MAX_XLSX_BYTES
    ):
        raise FinancialInputInvalid(details={"code": "INVALID_FILENAME_OR_SIZE"})
    return name


def prepare_financial_import(
    repository: FinancialRepository,
    principal: Principal,
    name: str,
    content: bytes,
    profile: ConnectBooksXlsxProfile,
    uri: str,
    received_at: datetime,
) -> tuple[FinancialImport, list[FinancialImportRow]]:
    """Parse and snapshot approved links inside the caller's locked transaction."""
    start, end = reporting_bounds(profile)
    digest = sha256_hex(content)
    profile_data = profile.model_dump(mode="json")
    profile_digest = hashlib.sha256(
        json.dumps(profile_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        preview = preview_connectbooks_xlsx(content, profile)
    except ProfitabilityPreviewError as exc:
        raise FinancialInputInvalid(details={"code": str(exc)}) from None
    listings = repository.approved_listings(
        profile.marketplace_id,
        {r.seller_sku for r in preview.rows if r.seller_sku is not None and r.status != "INVALID"},
    )
    identifier = uuid.uuid4()
    rows = [_row(r, identifier, principal.organization_id, listings) for r in preview.rows]
    review_rows = sum(r.review_required for r in rows)
    batch = FinancialImport(
        id=identifier,
        organization_id=principal.organization_id,
        original_filename=name,
        storage_uri=uri,
        raw_sha256=digest,
        profile_sha256=profile_digest,
        profile_snapshot=profile_data,
        report_snapshot=preview.model_dump(mode="json", exclude={"rows", "profile"}),
        marketplace_id=profile.marketplace_id,
        store_name=profile.store_name,
        currency=profile.currency,
        period_start=start,
        period_end=end,
        received_at=received_at,
        uploaded_by_user_id=principal.user_id,
        status="REVIEW_REQUIRED"
        if review_rows or preview.status == "REVIEW_REQUIRED"
        else "RECORDED",
        row_count=len(rows),
        linked_rows=sum(r.mapping_status == "LINKED" for r in rows),
        review_rows=review_rows,
        purchasing_eligible=False,
    )
    return batch, rows


def receive_financial_import(
    session: Session,
    storage: StorageBackend,
    *,
    principal: Principal,
    filename: str,
    content: bytes,
    profile: ConnectBooksXlsxProfile,
) -> FinancialImportOutcome:
    principal.require_roles((RoleCode.DATA_OPERATOR,))
    start, end = reporting_bounds(profile)
    name = financial_filename(filename, content)
    digest = sha256_hex(content)
    profile_data = profile.model_dump(mode="json")
    profile_digest = hashlib.sha256(
        json.dumps(profile_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    repository = FinancialRepository(session, principal.organization_id)
    try:
        with transaction(session):
            if not repository.actor_exists(principal.user_id):
                raise AuthorizationError("The uploader must be active in this organization.")
            repository.lock_marketplace(profile.marketplace_id)
            existing = repository.by_digest(digest)
            if existing is not None:
                if existing.profile_sha256 != profile_digest:
                    raise FinancialImportConflict(details={"code": "REPLAY_CONTEXT_MISMATCH"})
                try:
                    retained = storage.open(existing.storage_uri)
                except StorageObjectMissing:
                    restored = storage.put(
                        content,
                        existing.original_filename,
                        organization_id=principal.organization_id,
                        at=existing.received_at,
                    )
                    if restored != existing.storage_uri:
                        raise FinancialImportConflict(
                            details={"code": "RAW_RESTORE_URI_MISMATCH"}
                        ) from None
                    retained = storage.open(existing.storage_uri)
                if sha256_hex(retained) != digest:
                    raise FinancialImportConflict(details={"code": "RAW_INTEGRITY_MISMATCH"})
                return FinancialImportOutcome(existing, True)
            if repository.overlap(profile.marketplace_id, start, end):
                raise FinancialImportConflict(details={"code": "OVERLAPPING_REPORTING_PERIOD"})
            received_at = datetime.now(UTC)
            uri = storage.put(
                content, name, organization_id=principal.organization_id, at=received_at
            )
            batch, rows = prepare_financial_import(
                repository, principal, name, content, profile, uri, received_at
            )
            review_rows = batch.review_rows
            repository.add(batch, rows)
            audit.record(
                session,
                organization_id=principal.organization_id,
                actor=principal,
                action="financial_import.recorded",
                entity_type="financial_import",
                entity_id=batch.id,
                after={
                    "raw_sha256": digest,
                    "profile_sha256": profile_digest,
                    "row_count": batch.row_count,
                    "linked_rows": batch.linked_rows,
                    "review_rows": review_rows,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
            )
    except IntegrityError:
        raise FinancialImportConflict(details={"code": "CONCURRENT_OR_INVALID_RECORD"}) from None
    return FinancialImportOutcome(batch, False)


def get_financial_import(
    session: Session, principal: Principal, identifier: uuid.UUID
) -> FinancialImport:
    principal.require_roles(FINANCIAL_READ_ROLES)
    row = FinancialRepository(session, principal.organization_id).get(identifier)
    if row is None:
        raise FinancialImportNotFound()
    return row


def open_financial_raw(storage: StorageBackend, batch: FinancialImport) -> bytes:
    try:
        content = storage.open(batch.storage_uri)
    except StorageError:
        raise FinancialInputInvalid(details={"code": "RAW_OBJECT_UNAVAILABLE"}) from None
    if sha256_hex(content) != batch.raw_sha256:
        raise FinancialImportConflict(details={"code": "RAW_INTEGRITY_MISMATCH"})
    return content


def financial_history(
    session: Session, principal: Principal, page: int, page_size: int, *, current_only: bool = False
) -> tuple[list[FinancialImport], int]:
    principal.require_roles(FINANCIAL_READ_ROLES)
    return FinancialRepository(session, principal.organization_id).history(
        page, page_size, current_only=current_only
    )


def financial_rows(
    session: Session,
    principal: Principal,
    identifier: uuid.UUID,
    page: int,
    page_size: int,
    *,
    review_only: bool = False,
    mapping_status: str | None = None,
) -> tuple[list[FinancialImportRow], int]:
    get_financial_import(session, principal, identifier)
    return FinancialRepository(session, principal.organization_id).rows(
        identifier, page, page_size, review_only=review_only, mapping_status=mapping_status
    )
