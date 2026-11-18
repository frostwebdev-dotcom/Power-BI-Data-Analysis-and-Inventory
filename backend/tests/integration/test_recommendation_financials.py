"""Real report/binding/policy snapshots and immutable historical financial saves."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.imports.storage import LocalStorageBackend
from app.models import AuditEvent, ReplenishmentRecommendation
from app.models.financial import FinancialImport
from app.schemas.financial_context import FinancialSelection
from app.schemas.recommendations import (
    FinancialRecommendationSnapshot,
    RecommendationPreview,
    RecommendationPreviewRequest,
)
from app.services import recommendations as service
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportNotFound,
    receive_financial_import,
)
from app.services.purchasing_policies import digest
from tests.integration import test_recommendations as evaluations
from tests.integration.test_recommendations import isolated_engine as isolated_engine
from tests.unit.test_connectbooks import changed, profile, workbook

pytestmark = pytest.mark.integration


def upload(
    session: Session, ctx: evaluations.Context, path: Path, *, unknown: bool = False
) -> FinancialImport:
    return receive_financial_import(
        session,
        LocalStorageBackend(path),
        principal=ctx.policy.operator,
        filename="synthetic.xlsx",
        content=workbook(
            [
                changed(
                    Item="OTHER" if unknown else ctx.policy.listing.seller_sku,
                    **{"Item ID": ctx.policy.listing.asin},
                )
            ]
        ),
        profile=profile(),
    ).batch


def preview(
    session: Session,
    ctx: evaluations.Context,
    batch: FinancialImport,
    binding: uuid.UUID | None = None,
) -> RecommendationPreview:
    return service.preview_recommendation(
        session,
        ctx.policy.operator,
        ctx.policy_id,
        RecommendationPreviewRequest(
            expected_approved_revision_id=ctx.approval,
            financial=FinancialSelection(import_id=batch.id, expected_binding_revision_id=binding),
        ),
        now=lambda: ctx.at,
    )


def test_report_is_explicit_complete_exact_and_historical(
    db_session: Session, tmp_path: Path
) -> None:
    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    legacy = evaluations.preview(db_session, ctx)
    result = preview(db_session, ctx, batch)
    assert isinstance(result.snapshot, FinancialRecommendationSnapshot)
    f = result.snapshot.financial
    assert result.snapshot.pools == legacy.snapshot.pools and result.snapshot.status == "BLOCKED"
    assert f.totals is not None and f.totals["profit"] == Decimal(50)
    assert f.rows[0].included_in_totals and f.rows[0].seller_sku == ctx.policy.listing.seller_sku
    assert "FINANCIAL_AND_DEMAND_WINDOWS_DIFFER" in f.review_reasons
    assert f.basis == "HISTORICAL_REPORT_ONLY" and not result.snapshot.purchasing_eligible
    selection = {"import_id": str(batch.id), "expected_binding_revision_id": None}
    request = evaluations.request(result, financial=selection)
    saved = evaluations.record(db_session, ctx, request)
    row = db_session.get(ReplenishmentRecommendation, saved.id)
    assert (
        row is not None
        and row.financial_import_id == batch.id
        and row.financial_binding_revision_id is None
    )
    assert saved.snapshot_sha256 == digest(saved.snapshot.model_dump(mode="json"))
    assert (
        len(list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == saved.id))))
        == 1
    )
    ctx.policy.listing.asin = "B000000009"
    db_session.commit()
    assert evaluations.record(db_session, ctx, request) == saved
    assert service.get_recommendation(db_session, ctx.policy.operator, saved.id) == saved
    changed_result = preview(db_session, ctx, batch)
    assert isinstance(changed_result.snapshot, FinancialRecommendationSnapshot)
    assert changed_result.snapshot.financial.totals is None
    assert changed_result.evidence_sha256 != result.evidence_sha256


def test_missing_rows_remain_unknown(db_session: Session, tmp_path: Path) -> None:
    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path, unknown=True)
    result = preview(db_session, ctx, batch)
    assert isinstance(result.snapshot, FinancialRecommendationSnapshot)
    assert result.snapshot.financial.totals is None
    assert result.snapshot.financial.rows[0].amounts == {}
    assert "FINANCIAL_SKU_MISSING" in result.snapshot.financial.review_reasons


def test_foreign_missing_and_stale_binding_fail_closed(db_session: Session, tmp_path: Path) -> None:
    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    with pytest.raises(FinancialImportConflict):
        preview(db_session, ctx, batch, uuid.uuid4())
    db_session.rollback()
    other = evaluations.setup(db_session)
    with pytest.raises(FinancialImportNotFound):
        preview(db_session, other, batch)
    db_session.rollback()
    with pytest.raises(FinancialImportNotFound):
        preview(db_session, ctx, FinancialImport(id=uuid.uuid4()))
    db_session.rollback()


def test_database_rejects_missing_or_foreign_financial_reference(
    db_session: Session, tmp_path: Path
) -> None:
    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    result = preview(db_session, ctx, batch)
    saved = evaluations.record(
        db_session,
        ctx,
        evaluations.request(
            result, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
        ),
    )
    original = db_session.get(ReplenishmentRecommendation, saved.id)
    assert original is not None
    values = {column.name: getattr(original, column.name) for column in original.__table__.columns}
    for financial_id in (None, uuid.uuid4()):
        values.update(id=uuid.uuid4(), request_id=uuid.uuid4(), financial_import_id=financial_id)
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(insert(ReplenishmentRecommendation).values(**values))
    db_session.rollback()


def test_binding_revisions_force_selection_and_retain_original_retry(
    db_session: Session, tmp_path: Path
) -> None:
    from datetime import timedelta

    from app.schemas.financial_imports import FinancialBindingRecordRequest
    from app.services.financial_bindings import preview_binding_revision, record_binding_revision

    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    initial = preview(db_session, ctx, batch)
    payload = evaluations.request(
        initial, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
    )
    saved = evaluations.record(db_session, ctx, payload)
    ctx.policy.listing.approved_at = ctx.at - timedelta(seconds=1)
    db_session.commit()
    proposal = preview_binding_revision(db_session, ctx.policy.operator, batch.id, 1, 25)
    revision = record_binding_revision(
        db_session,
        ctx.policy.operator,
        batch.id,
        FinancialBindingRecordRequest(
            expected_revision_id=None,
            bindings_sha256=proposal.bindings_sha256,
            reason="Synthetic mapping approval evidence changed",
        ),
    )
    assert evaluations.record(db_session, ctx, payload).id == saved.id
    with pytest.raises(FinancialImportConflict):
        preview(db_session, ctx, batch)
    db_session.rollback()
    latest = preview(db_session, ctx, batch, revision.id)
    assert isinstance(latest.snapshot, FinancialRecommendationSnapshot)
    assert latest.snapshot.financial.binding_revision_id == revision.id
    assert latest.snapshot.financial.rows[0].included_in_totals
    recorded = evaluations.record(
        db_session,
        ctx,
        evaluations.request(
            latest,
            financial={
                "import_id": str(batch.id),
                "expected_binding_revision_id": str(revision.id),
            },
        ),
    )
    row = db_session.get(ReplenishmentRecommendation, recorded.id)
    assert row is not None and row.financial_binding_revision_id == revision.id


def test_replaced_report_rejected_but_saved_history_and_retry_remain(
    db_session: Session, tmp_path: Path
) -> None:
    from app.schemas.financial_imports import FinancialReplacementRecordRequest
    from app.services.financial_replacements import preview_replacement, record_replacement

    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    initial = preview(db_session, ctx, batch)
    payload = evaluations.request(
        initial, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
    )
    saved = evaluations.record(db_session, ctx, payload)
    content = workbook(
        [
            changed(
                Item=ctx.policy.listing.seller_sku,
                **{"Item ID": ctx.policy.listing.asin, "Cost": 45, "Profit": 45},
            )
        ]
    )
    storage = LocalStorageBackend(tmp_path)
    proposal = preview_replacement(
        db_session, storage, ctx.policy.operator, batch.id, "corrected.xlsx", content, 1, 25
    )
    record_replacement(
        db_session,
        storage,
        ctx.policy.operator,
        batch.id,
        "corrected.xlsx",
        content,
        FinancialReplacementRecordRequest(
            preview_sha256=proposal.preview_sha256, reason="Synthetic corrected cost source report"
        ),
    )
    assert evaluations.record(db_session, ctx, payload) == saved
    with pytest.raises(FinancialImportConflict):
        preview(db_session, ctx, batch)
    db_session.rollback()


def test_v3_snapshot_and_audit_rollback_atomically(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    result = preview(db_session, ctx, batch)
    before = evaluations.count(db_session)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.audit.record", unavailable)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        evaluations.record(
            db_session,
            ctx,
            evaluations.request(
                result, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
            ),
        )
    assert evaluations.count(db_session) == before


def test_v3_api_roundtrip_scope_roles_and_stale_save(db_session: Session, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_principal
    from app.core.config import Settings
    from app.core.security import Principal, RoleCode
    from app.db.session import get_db
    from app.main import create_app

    ctx = evaluations.setup(db_session)
    batch = upload(db_session, ctx, tmp_path)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: ctx.policy.operator
    base = f"/api/v1/purchasing-policies/{ctx.policy_id}"
    selection = {"import_id": str(batch.id), "expected_binding_revision_id": None}
    with TestClient(app) as client:
        response = client.post(
            base + "/recommendation-preview",
            json={"expected_approved_revision_id": str(ctx.approval), "financial": selection},
        )
        assert response.status_code == 200, response.text
        parsed = RecommendationPreview.model_validate(response.json())
        assert isinstance(parsed.snapshot, FinancialRecommendationSnapshot)
        assert parsed.snapshot.financial.rows[0].amounts["fees"] == Decimal(-15)
        request = evaluations.request(parsed, financial=selection).model_dump(mode="json")
        ctx.policy.listing.asin = "B000000009"
        db_session.commit()
        assert client.post(base + "/recommendations", json=request).status_code == 409
        viewer = Principal(
            ctx.policy.operator.user_id,
            ctx.policy.org.id,
            "viewer@example.test",
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.post(base + "/recommendations", json=request).status_code == 403
        assert (
            client.post(
                base + "/recommendation-preview",
                json={"expected_approved_revision_id": str(ctx.approval), "financial": selection},
            ).status_code
            == 200
        )


def test_v3_downgrade_refuses_discarding_financial_evidence(
    isolated_engine: Engine, tmp_path: Path
) -> None:
    from tests.integration.conftest import downgrade_migrations

    with Session(isolated_engine) as session:
        ctx = evaluations.setup(session)
        batch = upload(session, ctx, tmp_path)
        result = preview(session, ctx, batch)
        evaluations.record(
            session,
            ctx,
            evaluations.request(
                result, financial={"import_id": str(batch.id), "expected_binding_revision_id": None}
            ),
        )
    with pytest.raises(DBAPIError, match="Cannot discard historical financial evaluation evidence"):
        downgrade_migrations(isolated_engine.url, "a61117202606")
