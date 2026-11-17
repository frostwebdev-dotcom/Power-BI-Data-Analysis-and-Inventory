"""Real PostgreSQL source replacement lineage, immutable evidence and atomic review."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, get_storage
from app.core.config import Settings
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import AuditEvent, FinancialBindingRevision, FinancialImport
from app.repositories.financial import FinancialRepository
from app.schemas.financial_imports import (
    FinancialBindingRecordRequest,
    FinancialReplacementRecordRequest,
)
from app.services import audit
from app.services.financial_bindings import (
    preview_binding_revision,
    record_binding_revision,
    revised_financial_rows,
)
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportOutcome,
    FinancialInputInvalid,
    financial_rows,
    open_financial_raw,
)
from app.services.financial_replacements import preview_replacement, record_replacement
from tests.integration.conftest import downgrade_migrations
from tests.integration.test_financial_imports import Context, approved, setup_context, upload
from tests.integration.test_financial_imports import context as context
from tests.integration.test_financial_imports import isolated_engine as isolated_engine
from tests.unit.test_connectbooks import changed, profile, workbook

pytestmark = pytest.mark.integration
REASON = "Correct the reported product cost."


def replace(
    session: Session, ctx: Context, identifier: uuid.UUID, content: bytes
) -> FinancialImportOutcome:
    proposal = preview_replacement(
        session, ctx.storage, ctx.actor, identifier, "corrected.xlsx", content, 1, 25
    )
    return record_replacement(
        session,
        ctx.storage,
        ctx.actor,
        identifier,
        "corrected.xlsx",
        content,
        FinancialReplacementRecordRequest(preview_sha256=proposal.preview_sha256, reason=REASON),
    )


def test_preview_and_replacement_keep_originals_correct_money_and_audit_atomically(
    db_session: Session, context: Context
) -> None:
    approved(db_session, context)
    original_raw = workbook()
    source = upload(db_session, context, original_raw)
    original = financial_rows(db_session, context.actor, source.id, 1, 25)[0][0]
    raw = workbook([changed(Cost=45, Profit=45)])
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 25
    )
    assert proposal.preview_only and not proposal.purchasing_eligible
    cost = next(c for c in proposal.controls if c.field == "cost")
    assert (
        cost.previous_total,
        cost.replacement_total,
        cost.difference,
        cost.replacement_check,
    ) == (Decimal(40), Decimal(45), Decimal(5), "MATCH")
    assert FinancialRepository(db_session, context.org.id).history(1, 25)[1] == 1
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "financial_import.replaced")
        )
        == 0
    )
    outcome = replace(db_session, context, source.id, raw)
    replacement = outcome.batch
    db_session.execute(text("SET CONSTRAINTS financial_import_complete IMMEDIATE"))
    row = financial_rows(db_session, context.actor, replacement.id, 1, 25)[0][0]
    assert replacement.id != source.id and replacement.supersedes_import_id == source.id
    assert (
        replacement.replacement_reason == REASON
        and replacement.replacement_preview_sha256 == proposal.preview_sha256
    )
    assert (
        replacement.profile_snapshot == source.profile_snapshot
        and replacement.profile_sha256 == source.profile_sha256
    )
    assert (
        replacement.period_start == source.period_start
        and replacement.period_end == source.period_end
    )
    assert row.cost == 45 and row.computed_profit == 45 and original.cost == 40
    assert (
        original.import_id == source.id
        and not row.purchasing_eligible
        and not replacement.purchasing_eligible
    )
    assert open_financial_raw(context.storage, source) == original_raw
    assert open_financial_raw(context.storage, replacement) == raw
    events = list(
        db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == replacement.id))
    )
    assert len(events) == 1 and events[0].action == "financial_import.replaced"
    assert events[0].before and events[0].before["source_import_id"] == str(source.id)
    assert events[0].after and events[0].after["reason"] == REASON


def test_exact_successful_request_retry_returns_same_successor_without_second_audit(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    raw = workbook([changed(Cost=45, Profit=45)])
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 25
    )
    request = FinancialReplacementRecordRequest(
        preview_sha256=proposal.preview_sha256, reason=REASON
    )
    first = record_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, request
    )
    second = record_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, request
    )
    assert not first.duplicate and second.duplicate and first.batch.id == second.batch.id
    assert FinancialRepository(db_session, context.org.id).history(1, 25)[1] == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == first.batch.id)
        )
        == 1
    )


def test_multiversion_lineage_only_current_heads_and_unrelated_periods_stay_protected(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    raw = workbook([changed(Cost=45, Profit=45)])
    first = replace(db_session, context, source.id, raw).batch
    second = replace(db_session, context, first.id, workbook([changed(Cost=50, Profit=40)])).batch
    repository = FinancialRepository(db_session, context.org.id)
    assert repository.history(1, 25)[1] == 3
    assert [row.id for row in repository.history(1, 25, current_only=True)[0]] == [second.id]
    assert repository.successors([source.id, first.id, second.id]) == {
        source.id: first.id,
        first.id: second.id,
    }
    with pytest.raises(FinancialImportConflict) as stale:
        replace(db_session, context, source.id, workbook([changed(Cost=42, Profit=48)]))
    assert stale.value.details["code"] == "SOURCE_REPORT_REPLACED"
    with pytest.raises(FinancialImportConflict):
        upload(db_session, context, workbook([changed(Item="other")]))
    adjacent = upload(
        db_session,
        context,
        workbook([changed(Item="november")]),
        mapping=profile(reporting_month=11),
    )
    assert adjacent.period_start == second.period_end
    assert repository.history(1, 25, current_only=True)[1] == 2


def test_changed_file_mapping_and_source_binding_head_invalidate_review(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    raw = workbook([changed(Cost=45, Profit=45)])
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 25
    )
    request = FinancialReplacementRecordRequest(
        preview_sha256=proposal.preview_sha256, reason=REASON
    )
    with pytest.raises(FinancialImportConflict) as changed_file:
        record_replacement(
            db_session,
            context.storage,
            context.actor,
            source.id,
            "corrected.xlsx",
            workbook([changed(Cost=44, Profit=46)]),
            request,
        )
    assert changed_file.value.details["code"] == "REPLACEMENT_PROPOSAL_CHANGED"
    approved(db_session, context)
    db_session.commit()
    with pytest.raises(FinancialImportConflict) as changed_mapping:
        record_replacement(
            db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, request
        )
    assert changed_mapping.value.details["code"] == "REPLACEMENT_PROPOSAL_CHANGED"
    # Candidate SKU is different: applying source-only bindings still changes the head digest.
    other = workbook([changed(Item="candidate-only", Cost=44, Profit=46)])
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", other, 1, 25
    )
    links = preview_binding_revision(db_session, context.actor, source.id, 1, 25)
    record_binding_revision(
        db_session,
        context.actor,
        source.id,
        FinancialBindingRecordRequest(
            expected_revision_id=None,
            bindings_sha256=links.bindings_sha256,
            reason="Apply newly approved source links",
        ),
    )
    with pytest.raises(FinancialImportConflict) as changed_head:
        record_replacement(
            db_session,
            context.storage,
            context.actor,
            source.id,
            "corrected.xlsx",
            other,
            FinancialReplacementRecordRequest(
                preview_sha256=proposal.preview_sha256, reason=REASON
            ),
        )
    assert changed_head.value.details["code"] == "REPLACEMENT_PROPOSAL_CHANGED"


def test_unknown_zero_cost_unallocated_invalid_and_batch_warnings_survive_preview(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context, mapping=profile(advertising_coverage_confirmed=False))
    approved(db_session, context)
    raw = workbook(
        [
            changed(Cost=None),
            changed(Item="zero", Cost=0, Profit=90),
            changed(Item="Unitemized", **{"Item ID": ""}),
            changed(Item="invalid", Cost="broken"),
        ]
    )
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 2
    )
    assert proposal.previous_row_count == 1 and proposal.row_count == proposal.review_rows == 4
    assert proposal.added_skus == 2 and proposal.removed_skus == 0
    assert proposal.rows[0].cost is None and proposal.rows[0].computed_profit is None
    assert "ZERO_COST_WITH_POSITIVE_SALES" in proposal.rows[1].review_reasons
    assert proposal.status == "REVIEW_REQUIRED" and proposal.issues
    page2 = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 2, 2
    )
    assert page2.preview_sha256 == proposal.preview_sha256
    assert (
        page2.rows[0].mapping_status == "UNALLOCATED"
        and page2.rows[1].mapping_status == "INVALID_SOURCE"
    )
    child = replace(db_session, context, source.id, raw).batch
    assert child.review_rows == 4 and child.report_snapshot["issues"]


def test_added_removed_skus_and_exact_decimal_differences_are_visible(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    raw = workbook(
        [
            changed(
                Item="new-sku",
                Cost=Decimal("40.0000000000000001"),
                Profit=Decimal("49.9999999999999999"),
            )
        ],
        totals={"Cost": "$40.0000000000000001", "Profit": "$49.9999999999999999"},
    )
    proposal = preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 25
    )
    assert proposal.added_skus == proposal.removed_skus == 1
    assert next(
        control for control in proposal.controls if control.field == "cost"
    ).difference == Decimal("0.0000000000000001")
    assert proposal.rows[0].cost == Decimal("40.0000000000000001")
    assert proposal.rows[0].computed_profit == Decimal("49.9999999999999999")


def test_same_or_previously_recorded_bytes_cannot_be_a_new_source(
    db_session: Session, context: Context
) -> None:
    original_raw = workbook()
    source = upload(db_session, context, original_raw)
    with pytest.raises(FinancialImportConflict) as same:
        preview_replacement(
            db_session,
            context.storage,
            context.actor,
            source.id,
            "renamed.xlsx",
            original_raw,
            1,
            25,
        )
    assert same.value.details["code"] == "REPLACEMENT_FILE_ALREADY_RECORDED"


def test_audit_failure_rolls_back_successor_and_keeps_both_files(
    db_session: Session, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = upload(db_session, context)
    raw = workbook([changed(Cost=45, Profit=45)])
    preview_replacement(
        db_session, context.storage, context.actor, source.id, "corrected.xlsx", raw, 1, 25
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit"):
        replace(db_session, context, source.id, raw)
    assert FinancialRepository(db_session, context.org.id).history(1, 25)[1] == 1
    assert len(list(context.storage.root.rglob("*.xlsx"))) == 2


def test_corrupt_original_and_bad_replacement_bytes_never_record(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    with pytest.raises(FinancialInputInvalid):
        preview_replacement(
            db_session, context.storage, context.actor, source.id, "bad.xlsx", b"invalid", 1, 25
        )
    original_path = context.storage.root / source.storage_uri.partition("://")[2]
    original_path.write_bytes(b"corrupt")
    with pytest.raises(FinancialImportConflict) as corrupt:
        preview_replacement(
            db_session,
            context.storage,
            context.actor,
            source.id,
            "corrected.xlsx",
            workbook([changed(Cost=45, Profit=45)]),
            1,
            25,
        )
    assert corrupt.value.details["code"] == "RAW_INTEGRITY_MISMATCH"
    assert FinancialRepository(db_session, context.org.id).history(1, 25)[1] == 1


def test_historical_bindings_are_readable_but_new_bindings_are_blocked_on_replaced_source(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    approved(db_session, context)
    proposal = preview_binding_revision(db_session, context.actor, source.id, 1, 25)
    revision = record_binding_revision(
        db_session,
        context.actor,
        source.id,
        FinancialBindingRecordRequest(
            expected_revision_id=None,
            bindings_sha256=proposal.bindings_sha256,
            reason="Apply newly approved links",
        ),
    )
    replace(db_session, context, source.id, workbook([changed(Cost=45, Profit=45)]))
    assert revised_financial_rows(db_session, context.actor, source.id, revision.id, 1, 25)[1] == 1
    with pytest.raises(FinancialImportConflict) as old:
        preview_binding_revision(db_session, context.actor, source.id, 1, 25)
    assert old.value.details["code"] == "SOURCE_REPORT_REPLACED"
    values = {
        c.key: getattr(revision, c.key)
        for c in FinancialBindingRevision.__table__.columns
        if c.key not in {"id", "created_at", "updated_at", "recording_transaction_id"}
    }
    with pytest.raises(DBAPIError, match="current source report"), db_session.begin_nested():
        db_session.add(
            FinancialBindingRevision(
                **{
                    **values,
                    "id": uuid.uuid4(),
                    "revision_number": 2,
                    "supersedes_revision_id": revision.id,
                }
            )
        )
        db_session.flush()


@pytest.mark.parametrize("change", ["tenant", "profile", "period", "store", "same-bytes", "reason"])
def test_database_replacement_guard_prevents_context_or_ownership_bypass(
    db_session: Session, context: Context, tmp_path: Path, change: str
) -> None:
    source = upload(db_session, context)
    values = {
        c.key: getattr(source, c.key)
        for c in FinancialImport.__table__.columns
        if c.key not in {"id", "created_at", "updated_at", "recording_transaction_id"}
    }
    values.update(
        id=uuid.uuid4(),
        raw_sha256="f" * 64,
        supersedes_import_id=source.id,
        replacement_reason=REASON,
        replacement_preview_sha256="a" * 64,
    )
    if change == "tenant":
        foreign = setup_context(db_session, tmp_path / "foreign")
        values["supersedes_import_id"] = upload(db_session, foreign).id
    elif change == "profile":
        values["profile_sha256"] = "a" * 64
    elif change == "period":
        values["period_start"] = source.period_start + timedelta(days=1)
    elif change == "store":
        values["store_name"] = "Other"
    elif change == "same-bytes":
        values["raw_sha256"] = source.raw_sha256
    else:
        values["replacement_reason"] = "short"
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(FinancialImport(**values))
        db_session.flush()


def test_api_lineage_current_history_roles_and_tenant_access(
    db_session: Session, context: Context
) -> None:
    source = upload(db_session, context)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage] = lambda: context.storage
    app.dependency_overrides[get_current_principal] = lambda: context.actor
    raw = workbook([changed(Cost=45, Profit=45)])
    path = f"/api/v1/financial-imports/{source.id}"
    with TestClient(app) as client:
        proposal = client.post(
            path + "/replacement-preview", files={"file": ("corrected.xlsx", raw)}
        )
        assert proposal.status_code == 200, proposal.text
        data = {"preview_sha256": proposal.json()["preview_sha256"], "reason": REASON}
        result = client.post(
            path + "/replacements", files={"file": ("corrected.xlsx", raw)}, data=data
        )
        assert result.status_code == 201, result.text
        identifier = result.json()["batch"]["id"]
        assert client.get(path).json()["replaced_by_import_id"] == identifier
        assert result.json()["batch"]["supersedes_import_id"] == str(source.id)
        assert client.get("/api/v1/financial-imports?current_only=true").json()["total"] == 1
        assert (
            client.post(
                path + "/replacements", files={"file": ("corrected.xlsx", raw)}, data=data
            ).status_code
            == 200
        )
        viewer = Principal(
            context.actor.user_id,
            context.org.id,
            context.actor.email,
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.get(path).status_code == 200
        assert (
            client.post(
                path + "/replacement-preview", files={"file": ("corrected.xlsx", raw)}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path + "/replacements", files={"file": ("corrected.xlsx", raw)}, data=data
            ).status_code
            == 403
        )
        foreign = Principal(
            context.actor.user_id,
            uuid.uuid4(),
            context.actor.email,
            "Foreign",
            frozenset({RoleCode.ADMIN}),
        )
        app.dependency_overrides[get_current_principal] = lambda: foreign
        assert (
            client.post(
                path + "/replacement-preview", files={"file": ("corrected.xlsx", raw)}
            ).status_code
            == 404
        )
        assert (
            client.post(
                path + "/replacements", files={"file": ("corrected.xlsx", raw)}, data=data
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/financial-imports/{identifier}").status_code == 404


@pytest.mark.parametrize("identical", [True, False])
def test_concurrent_replacements_cannot_fork_and_identical_requests_replay(
    isolated_engine: Engine, tmp_path: Path, identical: bool
) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        ctx = setup_context(session, tmp_path)
        source = upload(session, ctx)
        first_raw = workbook([changed(Cost=45, Profit=45)])
        inputs = [first_raw, first_raw if identical else workbook([changed(Cost=50, Profit=40)])]
        proposals = [
            preview_replacement(
                session, ctx.storage, ctx.actor, source.id, "corrected.xlsx", raw, 1, 25
            )
            for raw in inputs
        ]

    def run(index: int) -> str:
        with Session(isolated_engine, expire_on_commit=False) as session:
            try:
                result = record_replacement(
                    session,
                    ctx.storage,
                    ctx.actor,
                    source.id,
                    "corrected.xlsx",
                    inputs[index],
                    FinancialReplacementRecordRequest(
                        preview_sha256=proposals[index].preview_sha256, reason=REASON
                    ),
                )
                return "duplicate" if result.duplicate else "recorded"
            except FinancialImportConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, range(2)))
    assert sorted(outcomes) == (
        ["duplicate", "recorded"] if identical else ["conflict", "recorded"]
    )
    with Session(isolated_engine) as session:
        assert FinancialRepository(session, ctx.org.id).history(1, 25)[1] == 2
        assert FinancialRepository(session, ctx.org.id).history(1, 25, current_only=True)[1] == 1


def test_downgrade_does_not_discard_recorded_replacement_history(
    isolated_engine: Engine, tmp_path: Path
) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        ctx = setup_context(session, tmp_path)
        source = upload(session, ctx)
        child = replace(session, ctx, source.id, workbook([changed(Cost=45, Profit=45)])).batch
        child_id = child.id
    with pytest.raises(DBAPIError, match="Cannot discard existing financial replacement history"):
        downgrade_migrations(isolated_engine.url, "a61117202602")
    with Session(isolated_engine) as session:
        assert session.get(FinancialImport, child_id) is not None
