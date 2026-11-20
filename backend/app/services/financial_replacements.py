"""Reviewed append-only replacement of a financial source report."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import localcontext

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.transaction import transaction
from app.imports.storage import StorageBackend, sha256_hex
from app.models.financial import FinancialImport, FinancialImportRow
from app.repositories.financial import FinancialRepository
from app.schemas.connectbooks import ConnectBooksControl, ConnectBooksXlsxProfile
from app.schemas.financial_imports import (
    FinancialReplacementControl,
    FinancialReplacementPreview,
    FinancialReplacementRecordRequest,
    FinancialRowResponse,
)
from app.schemas.profitability import ProfitabilityIssue
from app.services import audit
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportOutcome,
    financial_filename,
    get_financial_import,
    open_financial_raw,
    prepare_financial_import,
    reporting_bounds,
)


@dataclass
class ReplacementProposal:
    source: FinancialImport
    batch: FinancialImport
    rows: list[FinancialImportRow]
    digest: str
    binding_head_id: uuid.UUID | None


def _source(
    session: Session, principal: Principal, identifier: uuid.UUID
) -> tuple[FinancialRepository, FinancialImport]:
    source = get_financial_import(session, principal, identifier)
    repository = FinancialRepository(session, principal.organization_id)
    if not repository.actor_exists(principal.user_id):
        raise AuthorizationError("The operator must be active in this organization.")
    repository.lock_marketplace(source.marketplace_id)
    return repository, source


def _proposal(
    repository: FinancialRepository,
    source: FinancialImport,
    storage: StorageBackend,
    principal: Principal,
    filename: str,
    content: bytes,
) -> ReplacementProposal:
    if repository.has_successor(source.id):
        raise FinancialImportConflict(details={"code": "SOURCE_REPORT_REPLACED"})
    name = financial_filename(filename, content)
    if repository.by_digest(sha256_hex(content)) is not None:
        raise FinancialImportConflict(details={"code": "REPLACEMENT_FILE_ALREADY_RECORDED"})
    open_financial_raw(storage, source)
    profile = ConnectBooksXlsxProfile.model_validate(source.profile_snapshot)
    reporting_bounds(profile)
    received_at = datetime.now(UTC)
    uri = storage.put(content, name, organization_id=principal.organization_id, at=received_at)
    batch, rows = prepare_financial_import(
        repository, principal, name, content, profile, uri, received_at
    )
    head = repository.binding_head(source.id)
    head_id = head.id if head else None
    payload = {
        "rule": "financial-source-replacement-v1",
        "source_import_id": str(source.id),
        "source_binding_revision_id": str(head_id) if head_id else None,
        "profile_sha256": batch.profile_sha256,
        "raw_sha256": batch.raw_sha256,
        "filename": name,
        "report": batch.report_snapshot,
        "rows": [
            FinancialRowResponse.model_validate(row).model_dump(
                mode="json", exclude={"id", "import_id", "binding_revision_id"}
            )
            for row in rows
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ReplacementProposal(source, batch, rows, digest, head_id)


def preview_replacement(
    session: Session,
    storage: StorageBackend,
    principal: Principal,
    identifier: uuid.UUID,
    filename: str,
    content: bytes,
    page: int,
    page_size: int,
) -> FinancialReplacementPreview:
    principal.require_roles((RoleCode.DATA_OPERATOR,))
    with transaction(session):
        repository, source = _source(session, principal, identifier)
        proposal = _proposal(repository, source, storage, principal, filename, content)
        previous = {
            control.field: control
            for control in (
                ConnectBooksControl.model_validate(value)
                for value in source.report_snapshot["controls"]
            )
        }
        controls: list[FinancialReplacementControl] = []
        with localcontext() as context:
            context.prec = 80
            for value in proposal.batch.report_snapshot["controls"]:
                control = ConnectBooksControl.model_validate(value)
                old = previous[control.field].reported_total
                new = control.reported_total
                controls.append(
                    FinancialReplacementControl(
                        field=control.field,
                        previous_total=old,
                        replacement_total=new,
                        difference=new - old if new is not None and old is not None else None,
                        replacement_detail_total=control.detail_total,
                        replacement_check=control.status,
                    )
                )
        old_skus = {
            row.seller_sku
            for row in repository.source_rows(identifier)
            if row.seller_sku is not None
        }
        new_skus = {row.seller_sku for row in proposal.rows if row.seller_sku is not None}
        return FinancialReplacementPreview(
            source_import_id=identifier,
            preview_sha256=proposal.digest,
            original_filename=proposal.batch.original_filename,
            raw_sha256=proposal.batch.raw_sha256,
            status="REVIEW_REQUIRED" if proposal.batch.status == "REVIEW_REQUIRED" else "RECORDED",
            previous_row_count=source.row_count,
            row_count=proposal.batch.row_count,
            linked_rows=proposal.batch.linked_rows,
            review_rows=proposal.batch.review_rows,
            added_skus=len(new_skus - old_skus),
            removed_skus=len(old_skus - new_skus),
            controls=controls,
            issues=[
                ProfitabilityIssue.model_validate(value)
                for value in proposal.batch.report_snapshot["issues"]
            ],
            rows=[
                FinancialRowResponse.model_validate(row)
                for row in proposal.rows[(page - 1) * page_size : page * page_size]
            ],
            page=page,
            page_size=page_size,
        )


def record_replacement(
    session: Session,
    storage: StorageBackend,
    principal: Principal,
    identifier: uuid.UUID,
    filename: str,
    content: bytes,
    request: FinancialReplacementRecordRequest,
) -> FinancialImportOutcome:
    principal.require_roles((RoleCode.DATA_OPERATOR,))
    name = financial_filename(filename, content)
    try:
        with transaction(session):
            repository, source = _source(session, principal, identifier)
            existing = repository.by_digest(sha256_hex(content))
            if (
                existing is not None
                and existing.supersedes_import_id == identifier
                and existing.replacement_preview_sha256 == request.preview_sha256
                and existing.replacement_reason == request.reason
                and existing.original_filename == name
            ):
                open_financial_raw(storage, existing)
                return FinancialImportOutcome(existing, True)
            proposal = _proposal(repository, source, storage, principal, name, content)
            if proposal.digest != request.preview_sha256:
                raise FinancialImportConflict(details={"code": "REPLACEMENT_PROPOSAL_CHANGED"})
            batch = proposal.batch
            batch.supersedes_import_id = identifier
            batch.replacement_reason = request.reason
            batch.replacement_preview_sha256 = proposal.digest
            repository.add(batch, proposal.rows)
            audit.record(
                session,
                organization_id=principal.organization_id,
                actor=principal,
                action="financial_import.replaced",
                entity_type="financial_import",
                entity_id=batch.id,
                before={
                    "source_import_id": str(identifier),
                    "source_raw_sha256": source.raw_sha256,
                    "source_binding_revision_id": str(proposal.binding_head_id)
                    if proposal.binding_head_id
                    else None,
                },
                after={
                    "raw_sha256": batch.raw_sha256,
                    "profile_sha256": batch.profile_sha256,
                    "preview_sha256": proposal.digest,
                    "reason": request.reason,
                    "row_count": batch.row_count,
                    "linked_rows": batch.linked_rows,
                    "review_rows": batch.review_rows,
                },
            )
    except IntegrityError:
        raise FinancialImportConflict(
            details={"code": "CONCURRENT_OR_INVALID_REPLACEMENT"}
        ) from None
    return FinancialImportOutcome(batch, False)
