"""Explicit, reviewed SKU-link revisions; source money and import evidence never change."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.transaction import transaction
from app.models.financial import (
    FinancialBindingRevision,
    FinancialBindingRow,
    FinancialImport,
    FinancialImportRow,
)
from app.repositories.financial import FinancialRepository
from app.schemas.connectbooks import ConnectBooksPreviewRow
from app.schemas.financial_imports import (
    FinancialBindingChange,
    FinancialBindingEvidence,
    FinancialBindingPreview,
    FinancialBindingRecordRequest,
    FinancialRowResponse,
)
from app.services import audit
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportNotFound,
    _row,
    get_financial_import,
)


def evidence(row: FinancialImportRow | FinancialBindingRow) -> FinancialBindingEvidence:
    return FinancialBindingEvidence.model_validate(row)


def bindings_digest(
    identifier: uuid.UUID,
    head_id: uuid.UUID | None,
    values: list[tuple[uuid.UUID, FinancialBindingEvidence]],
) -> str:
    payload = {
        "version": "financial-bindings-v1",
        "import_id": str(identifier),
        "head_id": str(head_id) if head_id else None,
        "rows": [
            {"source_row_id": str(row_id), **item.model_dump(mode="json")}
            for row_id, item in sorted(values, key=lambda pair: str(pair[0]))
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class Proposal:
    batch: FinancialImport
    head: FinancialBindingRevision | None
    bindings: list[tuple[FinancialImportRow, FinancialBindingEvidence]]
    changes: list[FinancialBindingChange]
    digest: str
    status: Literal["RECORDED", "REVIEW_REQUIRED"]
    linked_rows: int
    review_rows: int


def _proposal(session: Session, principal: Principal, identifier: uuid.UUID) -> Proposal:
    # The same lock coordinates uploads and revisions. Shared mapping locks below
    # keep the approved evidence stable until the preview/read or record commits.
    batch = get_financial_import(session, principal, identifier)
    repository = FinancialRepository(session, principal.organization_id)
    if not repository.actor_exists(principal.user_id):
        raise AuthorizationError("The operator must be active in this organization.")
    repository.lock_marketplace(batch.marketplace_id)
    head = repository.binding_head(identifier)
    source = repository.source_rows(identifier)
    previous = repository.binding_rows(head.id) if head else {}
    listings = repository.approved_listings(
        batch.marketplace_id, {row.seller_sku for row in source if row.seller_sku}
    )
    bindings: list[tuple[FinancialImportRow, FinancialBindingEvidence]] = []
    changes: list[FinancialBindingChange] = []
    for row in source:
        parsed = ConnectBooksPreviewRow.model_validate(row.source_snapshot)
        proposed = evidence(_row(parsed, identifier, principal.organization_id, listings))
        before = evidence(previous[row.id] if head else row)
        bindings.append((row, proposed))
        if before != proposed:
            changes.append(
                FinancialBindingChange(
                    source_row_number=row.source_row_number,
                    seller_sku=row.seller_sku,
                    before=before,
                    after=proposed,
                )
            )
    review_rows = sum(item.review_required for _, item in bindings)
    linked_rows = sum(item.mapping_status == "LINKED" for _, item in bindings)
    status: Literal["RECORDED", "REVIEW_REQUIRED"] = (
        "REVIEW_REQUIRED"
        if review_rows or batch.report_snapshot.get("status") == "REVIEW_REQUIRED"
        else "RECORDED"
    )
    return Proposal(
        batch,
        head,
        bindings,
        changes,
        bindings_digest(
            identifier, head.id if head else None, [(row.id, item) for row, item in bindings]
        ),
        status,
        linked_rows,
        review_rows,
    )


def preview_binding_revision(
    session: Session, principal: Principal, identifier: uuid.UUID, page: int, page_size: int
) -> FinancialBindingPreview:
    principal.require_roles((RoleCode.DATA_OPERATOR,))
    with transaction(session):
        proposal = _proposal(session, principal, identifier)
        return FinancialBindingPreview(
            import_id=identifier,
            expected_revision_id=proposal.head.id if proposal.head else None,
            bindings_sha256=proposal.digest,
            status=proposal.status,
            row_count=proposal.batch.row_count,
            linked_rows=proposal.linked_rows,
            review_rows=proposal.review_rows,
            changed_rows=len(proposal.changes),
            changes=proposal.changes[(page - 1) * page_size : page * page_size],
            page=page,
            page_size=page_size,
        )


def record_binding_revision(
    session: Session,
    principal: Principal,
    identifier: uuid.UUID,
    request: FinancialBindingRecordRequest,
) -> FinancialBindingRevision:
    principal.require_roles((RoleCode.DATA_OPERATOR,))
    repository = FinancialRepository(session, principal.organization_id)
    try:
        with transaction(session):
            proposal = _proposal(session, principal, identifier)
            if (proposal.head.id if proposal.head else None) != request.expected_revision_id:
                raise FinancialImportConflict(details={"code": "BINDING_HEAD_CHANGED"})
            if proposal.digest != request.bindings_sha256:
                raise FinancialImportConflict(details={"code": "BINDING_PROPOSAL_CHANGED"})
            if not proposal.changes:
                raise FinancialImportConflict(details={"code": "BINDING_NO_CHANGES"})
            revision = FinancialBindingRevision(
                id=uuid.uuid4(),
                organization_id=principal.organization_id,
                import_id=identifier,
                revision_number=proposal.head.revision_number + 1 if proposal.head else 1,
                supersedes_revision_id=request.expected_revision_id,
                recorded_by_user_id=principal.user_id,
                reason=request.reason,
                bindings_sha256=proposal.digest,
                status=proposal.status,
                row_count=proposal.batch.row_count,
                linked_rows=proposal.linked_rows,
                review_rows=proposal.review_rows,
                changed_rows=len(proposal.changes),
                purchasing_eligible=False,
            )
            rows = [
                FinancialBindingRow(
                    id=uuid.uuid4(),
                    organization_id=principal.organization_id,
                    revision_id=revision.id,
                    import_id=identifier,
                    source_row_id=source.id,
                    purchasing_eligible=False,
                    **item.model_dump(),
                )
                for source, item in proposal.bindings
            ]
            repository.add_binding_revision(revision, rows)
            audit.record(
                session,
                organization_id=principal.organization_id,
                actor=principal,
                action="financial_import.bindings_revised",
                entity_type="financial_binding_revision",
                entity_id=revision.id,
                before={
                    "import_id": str(identifier),
                    "revision_id": str(request.expected_revision_id)
                    if request.expected_revision_id
                    else None,
                },
                after={
                    "import_id": str(identifier),
                    "revision_number": revision.revision_number,
                    "bindings_sha256": proposal.digest,
                    "linked_rows": revision.linked_rows,
                    "review_rows": revision.review_rows,
                    "changed_rows": revision.changed_rows,
                    "reason": request.reason,
                },
            )
    except IntegrityError:
        raise FinancialImportConflict(
            details={"code": "CONCURRENT_OR_INVALID_BINDING_REVISION"}
        ) from None
    return revision


def binding_history(
    session: Session, principal: Principal, identifier: uuid.UUID, page: int, page_size: int
) -> tuple[list[FinancialBindingRevision], int]:
    get_financial_import(session, principal, identifier)
    return FinancialRepository(session, principal.organization_id).binding_history(
        identifier, page, page_size
    )


def revised_financial_rows(
    session: Session,
    principal: Principal,
    identifier: uuid.UUID,
    revision_id: uuid.UUID,
    page: int,
    page_size: int,
    *,
    review_only: bool = False,
    mapping_status: str | None = None,
) -> tuple[list[FinancialRowResponse], int]:
    get_financial_import(session, principal, identifier)
    repository = FinancialRepository(session, principal.organization_id)
    if repository.binding_revision(identifier, revision_id) is None:
        raise FinancialImportNotFound()
    rows, total = repository.revised_rows(
        identifier,
        revision_id,
        page,
        page_size,
        review_only=review_only,
        mapping_status=mapping_status,
    )
    return [
        FinancialRowResponse.model_validate(source).model_copy(
            update={**evidence(binding).model_dump(), "binding_revision_id": revision_id}
        )
        for source, binding in rows
    ], total
