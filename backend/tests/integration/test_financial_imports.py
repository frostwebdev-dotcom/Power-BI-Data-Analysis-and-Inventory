"""Real PostgreSQL financial recording, tenancy, approval evidence and immutability."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, get_storage
from app.core.config import Settings
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.storage import LocalStorageBackend
from app.main import create_app
from app.models import AuditEvent, MarketplaceListing, Organization, Product
from app.models.enums import MappingStatus, MatchMethod
from app.models.financial import FinancialImport, FinancialImportRow
from app.repositories.financial import FinancialRepository
from app.schemas.connectbooks import ConnectBooksXlsxProfile
from app.services import audit
from app.services.financial_imports import (
    FinancialImportConflict,
    FinancialImportNotFound,
    FinancialInputInvalid,
    financial_rows,
    get_financial_import,
    receive_financial_import,
)
from tests.integration import factories
from tests.integration.conftest import create_database, drop_database, run_migrations
from tests.unit.test_connectbooks import changed, profile, workbook

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 17, tzinfo=UTC)


@dataclass
class Context:
    org: Organization
    actor: Principal
    storage: LocalStorageBackend
    profile: ConnectBooksXlsxProfile


def setup_context(session: Session, root: Path) -> Context:
    org = factories.make_organization(session)
    user = factories.make_user(session, org)
    actor = Principal(user.id, org.id, user.email, user.display_name, frozenset({RoleCode.ADMIN}))
    session.commit()
    return Context(org, actor, LocalStorageBackend(root), profile())


@pytest.fixture
def context(db_session: Session, tmp_path: Path) -> Context:
    return setup_context(db_session, tmp_path)


def approved(
    session: Session, context: Context, **changes: object
) -> tuple[Product, MarketplaceListing]:
    product = factories.make_product(session, context.org)
    values: dict[str, object] = {
        "seller_sku": "0000123",
        "asin": "B000000001",
        "mapping_status": MappingStatus.APPROVED,
        "mapping_method": MatchMethod.MANUAL_APPROVAL,
        "approved_by_user_id": context.actor.user_id,
        "approved_at": NOW,
    }
    values.update(changes)
    listing = factories.make_marketplace_listing(session, context.org, product, **values)
    return product, listing


def upload(
    session: Session,
    context: Context,
    raw: bytes | None = None,
    *,
    mapping: ConnectBooksXlsxProfile | None = None,
) -> FinancialImport:
    return receive_financial_import(
        session,
        context.storage,
        principal=context.actor,
        filename="original.xlsx",
        content=raw if raw is not None else workbook(),
        profile=mapping or context.profile,
    ).batch


def test_recording_retains_bytes_links_approved_sku_and_audits_atomically(
    db_session: Session, context: Context
) -> None:
    product, listing = approved(db_session, context)
    raw = workbook()
    batch = upload(db_session, context, raw)
    rows, count = financial_rows(db_session, context.actor, batch.id, 1, 50)
    row = rows[0]
    assert count == batch.row_count == batch.linked_rows == 1
    assert batch.status == "RECORDED" and batch.review_rows == 0
    assert row.product_id == product.id and row.listing_id == listing.id and row.match_priority == 4
    assert row.mapping_snapshot["approved_by_user_id"] == str(context.actor.user_id)
    assert row.mapping_snapshot["approved_at"] == NOW.isoformat()
    assert row.seller_sku == "0000123" and row.computed_profit == Decimal(50)
    assert context.storage.open(batch.storage_uri) == raw
    assert not batch.purchasing_eligible and not row.purchasing_eligible
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == batch.id)))
    assert len(events) == 1 and events[0].action == "financial_import.recorded"
    db_session.execute(text("SET CONSTRAINTS financial_import_complete IMMEDIATE"))


def test_exact_replay_is_idempotent_and_does_not_rebind_after_mapping_change(
    db_session: Session, context: Context
) -> None:
    raw = workbook()
    first = upload(db_session, context, raw)
    approved(db_session, context)
    result = receive_financial_import(
        db_session,
        context.storage,
        principal=context.actor,
        filename="renamed.xlsx",
        content=raw,
        profile=context.profile,
    )
    assert result.duplicate and result.batch.id == first.id
    rows, _ = financial_rows(db_session, context.actor, first.id, 1, 50)
    assert rows[0].mapping_status == "UNRESOLVED" and rows[0].product_id is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == first.id)
        )
        == 1
    )


def test_relabeling_replay_or_overlapping_period_is_rejected(
    db_session: Session, context: Context
) -> None:
    raw = workbook()
    first = upload(db_session, context, raw)
    with pytest.raises(FinancialImportConflict) as replay:
        upload(db_session, context, raw, mapping=profile(reporting_month=9))
    assert replay.value.details["code"] == "REPLAY_CONTEXT_MISMATCH"
    with pytest.raises(FinancialImportConflict) as overlap:
        upload(db_session, context, workbook([changed(Item="another")]))
    assert overlap.value.details["code"] == "OVERLAPPING_REPORTING_PERIOD"
    second = upload(
        db_session,
        context,
        workbook([changed(Item="another")]),
        mapping=profile(reporting_month=11),
    )
    assert second.period_start == first.period_end


@pytest.mark.parametrize(
    "listing_changes",
    [
        {"mapping_status": MappingStatus.PENDING},
        {"is_active": False},
        {"seller_sku": "0000123X"},
        {"marketplace_id": "OTHER"},
    ],
)
def test_only_exact_active_approved_marketplace_mapping_can_link(
    db_session: Session, context: Context, listing_changes: dict[str, object]
) -> None:
    approved(db_session, context, **listing_changes)
    batch = upload(db_session, context)
    rows, _ = financial_rows(db_session, context.actor, batch.id, 1, 50)
    assert rows[0].mapping_status == "UNRESOLVED" and rows[0].product_id is None


def test_conflicting_asin_and_inactive_product_require_review(
    db_session: Session, context: Context
) -> None:
    product, listing = approved(db_session, context, asin="B000000099")
    batch = upload(db_session, context)
    rows, _ = financial_rows(db_session, context.actor, batch.id, 1, 50)
    assert rows[0].mapping_status == "ASIN_CONFLICT" and rows[0].product_id is None
    product.is_active = False
    listing.asin = "B000000001"
    second = upload(
        db_session,
        context,
        workbook([changed(Title="new period")]),
        mapping=profile(reporting_month=11),
    )
    rows, _ = financial_rows(db_session, context.actor, second.id, 1, 50)
    assert rows[0].mapping_status == "UNRESOLVED"


def test_cost_and_unallocated_review_survive_even_with_an_approved_sku(
    db_session: Session, context: Context
) -> None:
    approved(db_session, context)
    raw = workbook([changed(Cost=0, Profit=90), changed(Item="Unitemized", **{"Item ID": ""})])
    batch = upload(db_session, context, raw)
    rows, total = financial_rows(db_session, context.actor, batch.id, 1, 10, review_only=True)
    assert total == batch.review_rows == 2 and batch.linked_rows == 1
    assert (
        rows[0].mapping_status == "LINKED"
        and "ZERO_COST_WITH_POSITIVE_SALES" in rows[0].review_reasons
    )
    assert (
        rows[1].mapping_status == "UNALLOCATED"
        and rows[1].seller_sku is None
        and rows[1].product_id is None
    )
    assert batch.status == "REVIEW_REQUIRED"


def test_foreign_tenant_approved_sku_never_links_and_batch_reads_are_scoped(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    foreign = setup_context(db_session, tmp_path / "other")
    approved(db_session, foreign)
    batch = upload(db_session, context)
    rows, _ = financial_rows(db_session, context.actor, batch.id, 1, 10)
    assert rows[0].mapping_status == "UNRESOLVED"
    with pytest.raises(FinancialImportNotFound):
        get_financial_import(db_session, foreign.actor, batch.id)
    assert FinancialRepository(db_session, foreign.org.id).history(1, 10) == ([], 0)


def test_audit_failure_rolls_back_all_records_but_keeps_original_bytes(
    db_session: Session, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit"):
        upload(db_session, context)
    assert FinancialRepository(db_session, context.org.id).history(1, 10) == ([], 0)
    assert len(list(context.storage.root.rglob("*.xlsx"))) == 1


def test_bad_workbook_is_retained_but_not_reported_as_an_import(
    db_session: Session, context: Context
) -> None:
    with pytest.raises(FinancialInputInvalid):
        upload(db_session, context, b"invalid original bytes")
    assert FinancialRepository(db_session, context.org.id).history(1, 10) == ([], 0)
    assert next(context.storage.root.rglob("*.xlsx")).read_bytes() == b"invalid original bytes"


@pytest.mark.parametrize("table", ["financial_imports", "financial_import_rows"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_database_rejects_snapshot_edits_and_deletes(
    db_session: Session, context: Context, table: str, operation: str
) -> None:
    batch = upload(db_session, context)
    statement = (
        f"UPDATE {table} SET purchasing_eligible = false"
        if operation == "UPDATE"
        else f"DELETE FROM {table}"
    )
    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement))
    assert get_financial_import(db_session, context.actor, batch.id).row_count == 1


@pytest.fixture(scope="module")
def isolated_engine(migrated_engine: Engine) -> Iterator[Engine]:
    url = migrated_engine.url.set(database=f"prms_financial_{uuid.uuid4().hex[:12]}_test")
    create_database(url)
    engine = create_engine(url)
    try:
        run_migrations(url)
        yield engine
    finally:
        engine.dispose()
        drop_database(url)


def test_committed_import_rejects_later_row_appends(
    isolated_engine: Engine, tmp_path: Path
) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        context = setup_context(session, tmp_path)
        batch = upload(session, context)
        row = FinancialRepository(session, context.org.id).rows(batch.id, 1, 50)[0][0]
        values = {
            c.key: getattr(row, c.key)
            for c in FinancialImportRow.__table__.columns
            if c.key not in {"id", "created_at", "updated_at"}
        }
    with Session(isolated_engine) as session:
        session.add(FinancialImportRow(**{**values, "id": uuid.uuid4(), "source_row_number": 99}))
        with pytest.raises(DBAPIError, match="own import transaction"):
            session.commit()


def test_concurrent_identical_uploads_record_once(isolated_engine: Engine, tmp_path: Path) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        context = setup_context(session, tmp_path)
    raw = workbook()

    def receive() -> tuple[uuid.UUID, bool]:
        with Session(isolated_engine, expire_on_commit=False) as session:
            outcome = receive_financial_import(
                session,
                context.storage,
                principal=context.actor,
                filename="report.xlsx",
                content=raw,
                profile=context.profile,
            )
            return outcome.batch.id, outcome.duplicate

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: receive(), range(2)))
    assert results[0][0] == results[1][0] and sorted(r[1] for r in results) == [False, True]
    with Session(isolated_engine) as session:
        assert FinancialRepository(session, context.actor.organization_id).history(1, 50)[1] == 1


def test_concurrent_overlapping_uploads_cannot_both_commit(
    isolated_engine: Engine, tmp_path: Path
) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        context = setup_context(session, tmp_path)

    def receive(raw: bytes) -> str:
        with Session(isolated_engine, expire_on_commit=False) as session:
            try:
                upload(session, context, raw)
                return "recorded"
            except FinancialImportConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(receive, [workbook(), workbook([changed(Item="other")])]))
    assert sorted(results) == ["conflict", "recorded"]


def test_authenticated_api_upload_history_review_and_raw_are_tenant_scoped(
    db_session: Session, context: Context
) -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage] = lambda: context.storage
    app.dependency_overrides[get_current_principal] = lambda: context.actor
    raw = workbook()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/financial-imports",
            files={"file": ("report.xlsx", raw)},
            data={"profile_json": context.profile.model_dump_json()},
        )
        assert response.status_code == 201, response.text
        identifier = response.json()["batch"]["id"]
        assert client.get("/api/v1/financial-imports").json()["total"] == 1
        assert (
            client.get(f"/api/v1/financial-imports/{identifier}/rows?review_only=true").json()[
                "total"
            ]
            == 1
        )
        assert client.get(f"/api/v1/financial-imports/{identifier}/raw").content == raw
        replay = client.post(
            "/api/v1/financial-imports",
            files={"file": ("renamed.xlsx", raw)},
            data={"profile_json": context.profile.model_dump_json()},
        )
        assert replay.status_code == 200 and replay.json()["duplicate"]
        viewer = Principal(
            context.actor.user_id,
            context.actor.organization_id,
            context.actor.email,
            "Viewer",
            frozenset({RoleCode.VIEWER}),
        )
        app.dependency_overrides[get_current_principal] = lambda: viewer
        assert client.get(f"/api/v1/financial-imports/{identifier}").status_code == 200
        assert (
            client.post(
                "/api/v1/financial-imports",
                files={"file": ("report.xlsx", raw)},
                data={"profile_json": context.profile.model_dump_json()},
            ).status_code
            == 403
        )
        foreign = Principal(
            context.actor.user_id,
            uuid.uuid4(),
            context.actor.email,
            "Other",
            frozenset({RoleCode.ADMIN}),
        )
        app.dependency_overrides[get_current_principal] = lambda: foreign
        for suffix in ("", "/rows", "/raw"):
            assert client.get(f"/api/v1/financial-imports/{identifier}{suffix}").status_code == 404
        assert client.get("/api/v1/financial-imports").json()["total"] == 0


def test_invalid_source_never_gets_a_product_link(db_session: Session, context: Context) -> None:
    approved(db_session, context)
    batch = upload(db_session, context, workbook([changed(Cost="invalid amount")]))
    row = FinancialRepository(db_session, context.org.id).rows(batch.id, 1, 10)[0][0]
    assert row.mapping_status == "INVALID_SOURCE" and row.product_id is None
    assert row.cost is None and row.computed_profit is None


def test_database_guards_reject_foreign_links_and_unrecorded_row_counts(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    foreign = setup_context(db_session, tmp_path / "foreign")
    product, listing = approved(db_session, foreign)
    batch = upload(db_session, context)
    original = FinancialRepository(db_session, context.org.id).rows(batch.id, 1, 10)[0][0]
    values = {
        c.key: getattr(original, c.key)
        for c in FinancialImportRow.__table__.columns
        if c.key not in {"id", "created_at", "updated_at"}
    }
    with pytest.raises(DBAPIError, match="approved mapping"), db_session.begin_nested():
        db_session.add(
            FinancialImportRow(
                **{
                    **values,
                    "id": uuid.uuid4(),
                    "source_row_number": 99,
                    "mapping_status": "LINKED",
                    "listing_id": listing.id,
                    "product_id": product.id,
                    "match_priority": 4,
                }
            )
        )
        db_session.flush()
    batch_values = {
        c.key: getattr(batch, c.key)
        for c in FinancialImport.__table__.columns
        if c.key not in {"id", "created_at", "updated_at", "recording_transaction_id"}
    }
    with pytest.raises(DBAPIError, match="must not overlap"), db_session.begin_nested():
        db_session.add(
            FinancialImport(**{**batch_values, "id": uuid.uuid4(), "raw_sha256": "a" * 64})
        )
        db_session.flush()
    with pytest.raises(DBAPIError, match="counters must match"), db_session.begin_nested():
        db_session.add(
            FinancialImport(
                **{
                    **batch_values,
                    "id": uuid.uuid4(),
                    "raw_sha256": "b" * 64,
                    "period_start": datetime(2026, 12, 1, tzinfo=UTC),
                    "period_end": datetime(2027, 1, 1, tzinfo=UTC),
                }
            )
        )
        db_session.flush()
        db_session.execute(text("SET CONSTRAINTS financial_import_complete IMMEDIATE"))


def test_duplicate_can_restore_missing_raw_object_without_creating_a_new_record(
    db_session: Session, context: Context
) -> None:
    raw = workbook()
    batch = upload(db_session, context, raw)
    next(context.storage.root.rglob("*.xlsx")).unlink()
    replay = upload(db_session, context, raw)
    assert replay.id == batch.id and context.storage.open(batch.storage_uri) == raw
