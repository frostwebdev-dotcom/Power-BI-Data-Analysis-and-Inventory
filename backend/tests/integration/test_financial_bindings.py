"""Real PostgreSQL reviewed link revisions, immutable source and concurrent heads."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, get_storage
from app.core.config import Settings
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuditEvent,
    FinancialBindingRevision,
    FinancialBindingRow,
    User,
)
from app.repositories.financial import FinancialRepository
from app.schemas.financial_imports import FinancialBindingRecordRequest
from app.services import audit
from app.services.financial_bindings import (
    binding_history,
    preview_binding_revision,
    record_binding_revision,
    revised_financial_rows,
)
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportNotFound,
    financial_rows,
)
from tests.integration.test_financial_imports import (
    Context,
    approved,
    setup_context,
    upload,
)
from tests.integration.test_financial_imports import (
    context as context,
)
from tests.integration.test_financial_imports import (
    isolated_engine as isolated_engine,
)
from tests.unit.test_connectbooks import changed, profile, workbook

pytestmark = pytest.mark.integration


def record(session: Session, ctx: Context, identifier: uuid.UUID) -> FinancialBindingRevision:
    proposal = preview_binding_revision(session, ctx.actor, identifier, 1, 25)
    return record_binding_revision(
        session,
        ctx.actor,
        identifier,
        FinancialBindingRecordRequest(
            expected_revision_id=proposal.expected_revision_id,
            bindings_sha256=proposal.bindings_sha256,
            reason="Apply newly approved SKU links.",
        ),
    )


def test_preview_writes_nothing_and_revision_preserves_source_money_bytes_and_audits(
    db_session: Session, context: Context
) -> None:
    raw = workbook()
    batch = upload(db_session, context, raw)
    original = financial_rows(db_session, context.actor, batch.id, 1, 25)[0][0]
    original_snapshot = dict(original.source_snapshot)
    product, listing = approved(db_session, context)
    proposal = preview_binding_revision(db_session, context.actor, batch.id, 1, 25)
    assert (
        proposal.changed_rows == proposal.linked_rows == 1 and proposal.expected_revision_id is None
    )
    assert (
        proposal.changes[0].before.mapping_status == "UNRESOLVED"
        and proposal.changes[0].after.product_id == product.id
    )
    assert binding_history(db_session, context.actor, batch.id, 1, 25) == ([], 0)
    revision = record(db_session, context, batch.id)
    db_session.execute(text("SET CONSTRAINTS financial_binding_complete IMMEDIATE"))
    rows, total = revised_financial_rows(db_session, context.actor, batch.id, revision.id, 1, 25)
    assert total == 1 and rows[0].mapping_status == "LINKED" and rows[0].listing_id == listing.id
    assert (
        rows[0].binding_revision_id == revision.id
        and rows[0].source_snapshot.model_dump(mode="json") == original_snapshot
    )
    assert rows[0].cost == original.cost and rows[0].computed_profit == original.computed_profit
    assert not rows[0].purchasing_eligible and not revision.purchasing_eligible
    assert original.mapping_status == "UNRESOLVED" and batch.linked_rows == 0
    assert context.storage.open(batch.storage_uri) == raw
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == revision.id)))
    assert len(events) == 1 and events[0].action == "financial_import.bindings_revised"
    assert events[0].after is not None and events[0].after["reason"] == revision.reason
    assert len(list(context.storage.root.rglob("*.xlsx"))) == 1


def test_revision_chain_preserves_old_links_and_removes_no_longer_approved_links(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context)
    _, listing = approved(db_session, context)
    first = record(db_session, context, batch.id)
    listing.is_active = False
    second = record(db_session, context, batch.id)
    assert second.revision_number == 2 and second.supersedes_revision_id == first.id
    assert second.linked_rows == 0 and second.review_rows == 1
    history, count = binding_history(db_session, context.actor, batch.id, 1, 1)
    assert count == 2 and history[0].id == second.id
    assert (
        revised_financial_rows(db_session, context.actor, batch.id, first.id, 1, 25)[0][
            0
        ].mapping_status
        == "LINKED"
    )
    assert (
        revised_financial_rows(
            db_session,
            context.actor,
            batch.id,
            second.id,
            1,
            25,
            mapping_status="UNRESOLVED",
            review_only=True,
        )[1]
        == 1
    )
    assert (
        revised_financial_rows(
            db_session, context.actor, batch.id, second.id, 1, 25, mapping_status="LINKED"
        )[1]
        == 0
    )


def test_noop_and_stale_head_and_changed_proposal_are_rejected(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context)
    empty = preview_binding_revision(db_session, context.actor, batch.id, 1, 25)
    request = FinancialBindingRecordRequest(
        expected_revision_id=None,
        bindings_sha256=empty.bindings_sha256,
        reason="Apply approved SKU mappings.",
    )
    with pytest.raises(FinancialImportConflict) as noop:
        record_binding_revision(db_session, context.actor, batch.id, request)
    assert noop.value.details["code"] == "BINDING_NO_CHANGES"
    approved(db_session, context)
    # Mapping approval is a separate committed staff action. A rejected record
    # request must not roll that action back along with its own transaction.
    db_session.commit()
    with pytest.raises(FinancialImportConflict) as changed_proposal:
        record_binding_revision(db_session, context.actor, batch.id, request)
    assert changed_proposal.value.details["code"] == "BINDING_PROPOSAL_CHANGED"
    proposal = preview_binding_revision(db_session, context.actor, batch.id, 1, 25)
    request = request.model_copy(update={"bindings_sha256": proposal.bindings_sha256})
    record_binding_revision(db_session, context.actor, batch.id, request)
    with pytest.raises(FinancialImportConflict) as stale:
        record_binding_revision(db_session, context.actor, batch.id, request)
    assert stale.value.details["code"] == "BINDING_HEAD_CHANGED"
    assert binding_history(db_session, context.actor, batch.id, 1, 25)[1] == 1


def test_source_warnings_unallocated_invalid_rows_and_batch_review_survive(
    db_session: Session, context: Context
) -> None:
    batch = upload(
        db_session,
        context,
        workbook(
            [
                changed(Cost=0, Profit=90),
                changed(Item="Unitemized", **{"Item ID": ""}),
                changed(Item="bad", Cost="invalid"),
            ]
        ),
        mapping=profile(advertising_coverage_confirmed=False),
    )
    approved(db_session, context)
    approved(db_session, context, seller_sku="bad")
    revision = record(db_session, context, batch.id)
    rows, count = revised_financial_rows(db_session, context.actor, batch.id, revision.id, 1, 25)
    assert count == revision.row_count == revision.review_rows == 3
    assert (
        rows[0].mapping_status == "LINKED"
        and "ZERO_COST_WITH_POSITIVE_SALES" in rows[0].review_reasons
    )
    assert rows[1].mapping_status == "UNALLOCATED" and rows[1].product_id is None
    assert rows[2].mapping_status == "INVALID_SOURCE" and rows[2].product_id is None
    assert revision.status == "REVIEW_REQUIRED" and revision.changed_rows == 1
    assert batch.report_snapshot["issues"]
    db_session.execute(text("SET CONSTRAINTS financial_binding_complete IMMEDIATE"))


def test_batch_only_warnings_keep_revision_in_review_when_all_rows_link(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context, mapping=profile(advertising_coverage_confirmed=False))
    approved(db_session, context)
    revision = record(db_session, context, batch.id)
    assert revision.review_rows == 0 and revision.status == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "changes",
    [
        {"asin": "B000000099"},
        {"seller_sku": "0000123x"},
        {"marketplace_id": "OTHER"},
        {"is_active": False},
    ],
)
def test_revisions_reuse_exact_active_approved_matching(
    db_session: Session, context: Context, changes: dict[str, object]
) -> None:
    batch = upload(db_session, context)
    approved(db_session, context, **changes)
    proposal = preview_binding_revision(db_session, context.actor, batch.id, 1, 25)
    if "asin" in changes:
        assert (
            proposal.changed_rows == 1
            and proposal.changes[0].after.mapping_status == "ASIN_CONFLICT"
        )
        assert record(db_session, context, batch.id).linked_rows == 0
    else:
        assert proposal.changed_rows == 0 and proposal.linked_rows == 0


def test_preview_pages_and_digest_cover_the_complete_proposal(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context, workbook([changed(), changed(Item="second")]))
    approved(db_session, context)
    approved(db_session, context, seller_sku="second")
    first = preview_binding_revision(db_session, context.actor, batch.id, 1, 1)
    second = preview_binding_revision(db_session, context.actor, batch.id, 2, 1)
    assert (
        first.changed_rows == second.changed_rows == 2
        and first.bindings_sha256 == second.bindings_sha256
    )
    assert first.changes[0].source_row_number == 2 and second.changes[0].source_row_number == 3


def test_audit_failure_rolls_back_revision_and_rows(
    db_session: Session, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = upload(db_session, context)
    approved(db_session, context)
    preview_binding_revision(db_session, context.actor, batch.id, 1, 25)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit"):
        record(db_session, context, batch.id)
    assert binding_history(db_session, context.actor, batch.id, 1, 25)[1] == 0
    assert db_session.scalar(select(func.count()).select_from(FinancialBindingRow)) == 0


@pytest.mark.parametrize("table", ["financial_binding_revisions", "financial_binding_rows"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_revision_snapshots_cannot_be_edited_or_deleted(
    db_session: Session, context: Context, table: str, operation: str
) -> None:
    batch = upload(db_session, context)
    approved(db_session, context)
    record(db_session, context, batch.id)
    statement = (
        f"UPDATE {table} SET purchasing_eligible = false"
        if operation == "UPDATE"
        else f"DELETE FROM {table}"
    )
    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement))


def test_revision_reads_and_proposals_are_tenant_scoped(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    batch = upload(db_session, context)
    approved(db_session, context)
    revision = record(db_session, context, batch.id)
    foreign = setup_context(db_session, tmp_path / "other")
    foreign_batch = upload(db_session, foreign)
    for call in (
        lambda: binding_history(db_session, foreign.actor, batch.id, 1, 25),
        lambda: preview_binding_revision(db_session, foreign.actor, batch.id, 1, 25),
        lambda: revised_financial_rows(db_session, foreign.actor, batch.id, revision.id, 1, 25),
        lambda: revised_financial_rows(
            db_session, foreign.actor, foreign_batch.id, revision.id, 1, 25
        ),
    ):
        with pytest.raises(FinancialImportNotFound):
            call()
    with pytest.raises(FinancialImportNotFound):
        revised_financial_rows(db_session, context.actor, batch.id, uuid.uuid4(), 1, 25)


def test_inactive_actor_cannot_preview_or_record(db_session: Session, context: Context) -> None:
    batch = upload(db_session, context)
    user = db_session.get(User, context.actor.user_id)
    assert user is not None
    user.is_active = False
    db_session.commit()
    with pytest.raises(AuthorizationError):
        preview_binding_revision(db_session, context.actor, batch.id, 1, 25)


def test_api_roles_preview_record_history_and_historical_rows(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context)
    approved(db_session, context)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage] = lambda: context.storage
    app.dependency_overrides[get_current_principal] = lambda: context.actor
    path = f"/api/v1/financial-imports/{batch.id}/binding-revisions"
    with TestClient(app) as client:
        proposal = client.post(path + "/preview").json()
        assert proposal["changed_rows"] == 1 and proposal["expected_revision_id"] is None
        response = client.post(
            path,
            json={
                "expected_revision_id": None,
                "bindings_sha256": proposal["bindings_sha256"],
                "reason": "Apply newly approved SKU mappings.",
            },
        )
        assert response.status_code == 201, response.text
        revision = response.json()
        assert client.get(path).json()["items"][0]["id"] == revision["id"]
        assert (
            client.get(f"/api/v1/financial-imports/{batch.id}/rows").json()["items"][0][
                "mapping_status"
            ]
            == "UNRESOLVED"
        )
        assert (
            client.get(
                f"/api/v1/financial-imports/{batch.id}/rows?binding_revision_id={revision['id']}"
            ).json()["items"][0]["mapping_status"]
            == "LINKED"
        )
        assert client.post(path + "/preview?page_size=201").status_code == 422
        viewer = Principal(
            context.actor.user_id,
            context.org.id,
            context.actor.email,
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.get(path).status_code == 200
        assert client.post(path + "/preview").status_code == 403
        assert client.post(path, json={}).status_code == 403
        foreign = Principal(
            context.actor.user_id,
            uuid.uuid4(),
            context.actor.email,
            "Other",
            frozenset({RoleCode.ADMIN}),
        )
        app.dependency_overrides[get_current_principal] = lambda: foreign
        assert client.get(path).status_code == 404
        assert client.post(path + "/preview").status_code == 404


def test_concurrent_records_cannot_fork_head(isolated_engine: Engine, tmp_path: Path) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        ctx = setup_context(session, tmp_path)
        batch = upload(session, ctx)
        approved(session, ctx)
        proposal = preview_binding_revision(session, ctx.actor, batch.id, 1, 25)
        request = FinancialBindingRecordRequest(
            expected_revision_id=None,
            bindings_sha256=proposal.bindings_sha256,
            reason="Apply reviewed approved SKU links.",
        )

    def run() -> str:
        with Session(isolated_engine, expire_on_commit=False) as session:
            try:
                record_binding_revision(session, ctx.actor, batch.id, request)
                return "recorded"
            except FinancialImportConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == ["conflict", "recorded"]
    with Session(isolated_engine) as session:
        assert binding_history(session, ctx.actor, batch.id, 1, 25)[1] == 1


def test_committed_revision_rejects_later_binding_appends(
    isolated_engine: Engine, tmp_path: Path
) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        ctx = setup_context(session, tmp_path)
        batch = upload(session, ctx)
        approved(session, ctx)
        revision = record(session, ctx, batch.id)
        row = next(
            iter(FinancialRepository(session, ctx.org.id).binding_rows(revision.id).values())
        )
        values = {
            column.key: getattr(row, column.key)
            for column in FinancialBindingRow.__table__.columns
            if column.key not in {"id", "created_at", "updated_at"}
        }
    with Session(isolated_engine) as session:
        session.add(FinancialBindingRow(**values, id=uuid.uuid4()))
        with pytest.raises(DBAPIError, match="own revision transaction"):
            session.commit()


def test_database_rejects_stale_ancestry_and_incomplete_binding_sets(
    db_session: Session, context: Context
) -> None:
    batch = upload(db_session, context, workbook([changed(), changed(Item="second")]))
    approved(db_session, context)
    revision = record(db_session, context, batch.id)
    values = {
        column.key: getattr(revision, column.key)
        for column in FinancialBindingRevision.__table__.columns
        if column.key not in {"id", "created_at", "updated_at", "recording_transaction_id"}
    }
    with pytest.raises(DBAPIError, match="current head"), db_session.begin_nested():
        db_session.add(FinancialBindingRevision(**values, id=uuid.uuid4()))
        db_session.flush()
    with pytest.raises(DBAPIError, match="counters must match"), db_session.begin_nested():
        db_session.add(
            FinancialBindingRevision(
                **{
                    **values,
                    "id": uuid.uuid4(),
                    "supersedes_revision_id": revision.id,
                    "revision_number": 2,
                }
            )
        )
        db_session.flush()
        db_session.execute(text("SET CONSTRAINTS financial_binding_complete IMMEDIATE"))
