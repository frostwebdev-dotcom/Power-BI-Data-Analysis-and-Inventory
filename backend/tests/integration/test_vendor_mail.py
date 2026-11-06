"""Email inventory ingestion through the real PostgreSQL/import pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.core.security import Principal, RoleCode
from app.db.session import get_db
from app.imports.profile_rules import compute_header_signature
from app.imports.storage import LocalStorageBackend, sha256_hex
from app.integrations.vendor_mail.types import IntakeError, MailAttachment, MailMessage, MailPage
from app.main import create_app
from app.models import AuditEvent, ImportFile, ImportJob, Organization, Vendor, VendorImportProfile
from app.models.inventory import VendorInventorySnapshot
from app.models.vendor_mail import VendorMailDelivery, VendorMailSource, VendorMailState
from app.repositories.vendor_mail import VendorMailRepository
from app.schemas.import_profiles import ImportProfileCreate
from app.schemas.vendor_mail import MailSourceWrite
from app.services.import_profiles import create_profile
from app.services.vendor_mail import (
    MailSourceConflict,
    mailbox_lock,
    retry_delivery,
    run_intake,
    save_source,
    source_response,
)
from tests.integration import factories

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 6, 16, tzinfo=UTC)
HEADERS = ["SKU", "Description", "Qty", "Cost"]


def csv(quantity: str = "5") -> bytes:
    return f"SKU,Description,Qty,Cost\nA,Test item,{quantity},10.50\n".encode()


def profile(
    session: Session, vendor: Vendor, actor: Principal, name: str = "Email inventory"
) -> VendorImportProfile:
    return create_profile(
        session,
        actor,
        vendor.id,
        ImportProfileCreate.model_validate(
            {
                "name": name,
                "file_format": "CSV",
                "delimiter": ",",
                "column_map": {
                    "columns": [
                        {"target": "vendor_sku", "source": 0},
                        {"target": "description", "source": 1},
                        {"target": "quantity_available", "source": 2},
                        {"target": "unit_cost", "source": 3},
                    ]
                },
                "availability_rules": {"available_when": "listed"},
                "header_signature": compute_header_signature(HEADERS),
            }
        ),
    )


@dataclass
class Context:
    org: Organization
    vendor: Vendor
    actor: Principal
    profile: VendorImportProfile
    source: VendorMailSource
    settings: Settings


@pytest.fixture
def context(db_session: Session) -> Context:
    org = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, org)
    user = factories.make_user(db_session, org)
    actor = Principal(user.id, org.id, user.email, user.display_name, frozenset({RoleCode.ADMIN}))
    p = profile(db_session, vendor, actor)
    source = save_source(
        db_session,
        actor,
        MailSourceWrite(
            name="Vendor daily stock",
            vendor_id=vendor.id,
            profile_id=p.id,
            sender_addresses=["stock@vendor.example"],
            filename_pattern="inventory*.csv",
            is_active=True,
            stale_after_hours=30,
        ),
    )
    source.starts_at = NOW - timedelta(days=1)
    db_session.commit()
    settings = Settings(
        app_env="test",
        vendor_mail_enabled=True,
        vendor_mail_tenant_id=uuid.UUID(int=1),
        vendor_mail_client_id=uuid.UUID(int=2),
        vendor_mail_client_secret="synthetic-test-secret",
        vendor_mail_mailbox="inventory@example.test",
        vendor_mail_organization_slug=org.slug,
    )
    return Context(org, vendor, actor, p, source, settings)


class FakeMail:
    def __init__(self) -> None:
        self.messages: list[MailMessage] = []
        self.files: dict[str, list[tuple[str, bytes]]] = {}
        self.fail_once: str | None = None
        self.downloads = 0

    def add(
        self,
        identifier: str,
        content: bytes,
        *,
        received: datetime = NOW,
        sender: str = "stock@vendor.example",
        body: str = "",
    ) -> None:
        self.messages.append(MailMessage(identifier, sender, "Inventory", received, True, body))
        self.files[identifier] = [("inventory.csv", content)]

    def page(self, checkpoint: str | None, since: datetime) -> MailPage:
        return MailPage(tuple(self.messages), "test-checkpoint", False)

    def message(self, message_id: str) -> MailMessage:
        return next(message for message in self.messages if message.id == message_id)

    def attachments(self, message_id: str) -> list[MailAttachment]:
        if self.fail_once == message_id:
            self.fail_once = None
            raise IntakeError("GRAPH_TEMPORARILY_UNAVAILABLE", retryable=True)
        return [
            MailAttachment(str(i), name, len(content))
            for i, (name, content) in enumerate(self.files[message_id])
        ]

    def attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> bytes:
        self.downloads += 1
        return self.files[message_id][int(attachment_id)][1]


def run(session: Session, ctx: Context, reader: FakeMail, root: Path, *, at: datetime = NOW) -> str:
    return run_intake(
        session, ctx.org.id, ctx.settings, reader, LocalStorageBackend(root), now=lambda: at
    )


def snapshots(session: Session, ctx: Context) -> list[VendorInventorySnapshot]:
    return list(
        session.scalars(
            select(VendorInventorySnapshot)
            .where(VendorInventorySnapshot.vendor_id == ctx.vendor.id)
            .order_by(VendorInventorySnapshot.created_at, VendorInventorySnapshot.id)
        )
    )


def test_attachment_import_repeat_message_and_repeat_bytes_are_idempotent(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    reader = FakeMail()
    reader.add("first", csv())
    assert run(db_session, context, reader, tmp_path) == "COMPLETED"
    assert len(snapshots(db_session, context)) == 1
    [file] = db_session.scalars(
        select(ImportFile).where(ImportFile.vendor_id == context.vendor.id)
    ).all()
    assert sha256_hex(LocalStorageBackend(tmp_path).open(file.storage_uri)) == sha256_hex(csv())
    assert run(db_session, context, reader, tmp_path) == "COMPLETED"
    assert reader.downloads == 1
    reader.add("same-bytes-new-email", csv(), received=NOW + timedelta(hours=1))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    repo = VendorMailRepository(db_session, context.org.id)
    rows, _ = repo.history(None, 1, 50)
    assert {row.status for row in rows} == {"COMPLETED", "DUPLICATE"}
    assert len(snapshots(db_session, context)) == 1
    assert context.source.last_success_at == NOW + timedelta(hours=1)
    assert all(row.import_job_id for row in rows)


def test_new_stock_applies_and_delayed_older_file_is_superseded(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    reader = FakeMail()
    reader.add("new", csv("8"), received=NOW + timedelta(hours=1))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    reader.add("old", csv("1"), received=NOW)
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=2))
    assert [row.quantity_available for row in snapshots(db_session, context)] == [Decimal(8)]
    deliveries, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert {row.status for row in deliveries} == {"COMPLETED", "SUPERSEDED"}


def test_invalid_rows_do_not_apply_partial_stock_and_raw_file_is_retained(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    reader = FakeMail()
    reader.add("good", csv())
    run(db_session, context, reader, tmp_path)
    reader.add("bad", csv("not-a-number"), received=NOW + timedelta(hours=1))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    assert len(snapshots(db_session, context)) == 1
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    failed = next(row for row in rows if row.message_id == "bad")
    assert failed.status == "NEEDS_REVIEW" and failed.error_code == "SOURCE_ROWS_INVALID"
    assert failed.import_job_id is not None
    failed_job = db_session.get(ImportJob, failed.import_job_id)
    assert failed_job is not None and failed_job.status.value == "FAILED"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ImportFile)
            .where(ImportFile.vendor_id == context.vendor.id)
        )
        == 2
    )
    assert source_response(context.source, NOW + timedelta(hours=1)).freshness == "awaiting_import"


def test_ambiguous_vendor_and_multiple_files_are_held_without_importing(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    other = factories.make_vendor(db_session, context.org)
    other_profile = profile(db_session, other, context.actor)
    second = save_source(
        db_session,
        context.actor,
        MailSourceWrite(
            name="Other vendor",
            vendor_id=other.id,
            profile_id=other_profile.id,
            sender_addresses=["stock@vendor.example"],
            filename_pattern="inventory*.csv",
            is_active=True,
        ),
    )
    second.starts_at = context.source.starts_at
    db_session.commit()
    reader = FakeMail()
    reader.add("ambiguous", csv())
    run(db_session, context, reader, tmp_path)
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert len(rows) == 2 and {row.error_code for row in rows} == {"AMBIGUOUS_VENDOR"}
    assert not snapshots(db_session, context)
    second.is_active = False
    db_session.commit()
    reader.add("multiple", csv(), received=NOW + timedelta(hours=1))
    reader.files["multiple"].append(("inventory-other.csv", csv("10")))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    assert not snapshots(db_session, context)
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert any(row.error_code == "MULTIPLE_MATCHING_FILES" for row in rows)


def test_failed_page_replays_without_losing_or_duplicating_deliveries(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    reader = FakeMail()
    reader.add("first", csv())
    reader.add("second", csv("6"), received=NOW + timedelta(minutes=1))
    reader.fail_once = "second"
    assert run(db_session, context, reader, tmp_path) == "POLL_FAILED"
    state = db_session.scalar(
        select(VendorMailState).where(VendorMailState.organization_id == context.org.id)
    )
    assert state is not None and state.checkpoint_url is None
    assert run(db_session, context, reader, tmp_path, at=NOW + timedelta(minutes=2)) == "COMPLETED"
    assert len(snapshots(db_session, context)) == 2
    rows, total = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert total == 2 and all(row.status == "COMPLETED" for row in rows)


def test_approved_download_link_uses_profile_and_does_not_persist_signed_url(
    db_session: Session,
    context: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context.source
    source.mode, source.download_hosts = "LINK", ["files.vendor.example"]
    source.download_path_prefix, source.download_filename = "/inventory/", "inventory.csv"
    db_session.commit()
    url = "https://files.vendor.example/inventory/get?sig=private-signed-link"
    reader = FakeMail()
    reader.add("link", csv(), body=f'<a href="{url}">Download</a>')
    monkeypatch.setattr("app.services.vendor_mail.download_file", lambda *args, **kwargs: csv())
    run(db_session, context, reader, tmp_path)
    [snapshot] = snapshots(db_session, context)
    assert snapshot.unit_cost == Decimal("10.50")
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert rows[0].status == "COMPLETED" and "private-signed-link" not in repr(rows[0].__dict__)
    audits = db_session.scalars(
        select(AuditEvent).where(AuditEvent.organization_id == context.org.id)
    ).all()
    assert all(
        "private-signed-link" not in str(event.after) + str(event.before) for event in audits
    )


def test_blocked_download_keeps_previous_inventory(
    db_session: Session,
    context: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeMail()
    reader.add("good", csv())
    run(db_session, context, reader, tmp_path)
    source = context.source
    source.mode, source.download_hosts = "LINK", ["files.vendor.example"]
    source.download_path_prefix, source.download_filename = "/inventory/", "inventory.csv"
    db_session.commit()
    reader.add(
        "login",
        csv(),
        received=NOW + timedelta(hours=1),
        body='<a href="https://files.vendor.example/inventory/list">Download</a>',
    )

    def blocked(*args: object, **kwargs: object) -> bytes:
        raise IntakeError("DOWNLOAD_LOGIN_REQUIRED", review=True)

    monkeypatch.setattr("app.services.vendor_mail.download_file", blocked)
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    assert len(snapshots(db_session, context)) == 1
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert any(row.error_code == "DOWNLOAD_LOGIN_REQUIRED" for row in rows)


def test_no_new_mail_keeps_stock_and_irregular_sources_do_not_go_overdue(
    db_session: Session,
    context: Context,
    tmp_path: Path,
) -> None:
    reader = FakeMail()
    reader.add("first", csv())
    run(db_session, context, reader, tmp_path)
    reader.messages = []
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(days=10))
    assert len(snapshots(db_session, context)) == 1
    assert source_response(context.source, NOW + timedelta(days=10)).freshness == "stale"
    context.source.stale_after_hours = None
    db_session.commit()
    assert source_response(context.source, NOW + timedelta(days=10)).freshness == "irregular"


def test_mailbox_lock_prevents_two_connections_running_together(
    db_session: Session,
    migrated_engine: Engine,
    context: Context,
) -> None:
    with mailbox_lock(db_session, context.org.id, "same-mailbox") as first:
        assert first
        with (
            Session(migrated_engine) as other,
            mailbox_lock(other, context.org.id, "same-mailbox") as second,
        ):
            assert not second
    with (
        Session(migrated_engine) as other,
        mailbox_lock(other, context.org.id, "same-mailbox") as after,
    ):
        assert after


def test_sources_cannot_reference_a_different_tenants_vendor_or_unvalidated_profile(
    db_session: Session,
    context: Context,
) -> None:
    other_org = factories.make_organization(db_session)
    other_vendor = factories.make_vendor(db_session, other_org)
    with pytest.raises(Exception, match="No such vendor"):
        save_source(
            db_session,
            context.actor,
            MailSourceWrite(
                name="Wrong tenant",
                vendor_id=other_vendor.id,
                profile_id=context.profile.id,
                sender_addresses=["stock@vendor.example"],
            ),
        )
    context.profile.header_signature = None
    db_session.commit()
    with pytest.raises(MailSourceConflict, match="Validate and pin"):
        save_source(
            db_session,
            context.actor,
            MailSourceWrite(
                name="Unvalidated",
                vendor_id=context.vendor.id,
                profile_id=context.profile.id,
                sender_addresses=["stock@vendor.example"],
                is_active=True,
            ),
        )


def test_automatic_import_api_permissions_tenant_scope_and_no_mailbox_credential_exposure(
    db_session: Session,
    context: Context,
) -> None:
    app = create_app(context.settings)
    app.dependency_overrides[get_db] = lambda: db_session
    actor = context.actor
    app.dependency_overrides[get_current_principal] = lambda: actor
    with TestClient(app) as client:
        response = client.get("/api/v1/vendor-mail/status")
        assert response.status_code == 200 and response.json()["configured"]
        assert "synthetic-test-secret" not in response.text
        actor = Principal(
            actor.user_id,
            actor.organization_id,
            actor.email,
            actor.display_name,
            frozenset({RoleCode.VIEWER}),
        )
        sources = client.get("/api/v1/vendor-mail/sources").json()
        assert sources["total"] == 1 and sources["items"][0]["profile_version"] == 1
        assert (
            client.post(
                "/api/v1/vendor-mail/sources",
                json=MailSourceWrite.model_validate(
                    context.source, from_attributes=True
                ).model_dump(mode="json"),
            ).status_code
            == 403
        )
        assert client.post("/api/v1/vendor-mail/poll").status_code == 403
        other_org = factories.make_organization(db_session)
        actor = Principal(
            actor.user_id,
            other_org.id,
            actor.email,
            actor.display_name,
            frozenset({RoleCode.ADMIN}),
        )
        assert client.get("/api/v1/vendor-mail/sources").json()["total"] == 0
        assert client.get("/api/v1/vendor-mail/status").json()["mailbox"] is None
        assert (
            client.patch(
                f"/api/v1/vendor-mail/sources/{context.source.id}",
                json=MailSourceWrite.model_validate(
                    context.source, from_attributes=True
                ).model_dump(mode="json"),
            ).status_code
            == 404
        )


def test_retry_preserves_pinned_profile_and_requires_explicit_rebinding(
    db_session: Session,
    context: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeMail()
    reader.add("first", csv())
    original = reader.attachment

    def temporary(message: str, attachment: str, maximum: int) -> bytes:
        raise IntakeError("GRAPH_CONNECTION_FAILED", retryable=True)

    monkeypatch.setattr(reader, "attachment", temporary)
    run(db_session, context, reader, tmp_path)
    [row] = db_session.scalars(
        select(VendorMailDelivery).where(VendorMailDelivery.organization_id == context.org.id)
    ).all()
    assert row.status == "FAILED" and row.next_attempt_at is not None
    newer_profile = profile(db_session, context.vendor, context.actor, "Replacement layout")
    context.source.profile_id = newer_profile.id
    db_session.commit()
    retry_delivery(db_session, context.actor, row.id)
    assert row.profile_id == context.profile.id
    monkeypatch.setattr(reader, "attachment", original)
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(minutes=5))
    assert row.status == "COMPLETED"
    assert row.import_job_id is not None
    job = db_session.get(ImportJob, row.import_job_id)
    assert job is not None and job.vendor_import_profile_id == context.profile.id


def test_two_worksheet_sources_share_raw_workbook_but_apply_both_sheets(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    from tests.integration.test_vendor_offer_imports import profile as xlsx_profile
    from tests.integration.test_vendor_offer_imports import workbook_bytes

    first = xlsx_profile(
        db_session, context.vendor, context.actor, "Designer", sheet_name="Designer Brands "
    )
    second = xlsx_profile(db_session, context.vendor, context.actor, "Niche", sheet_name="Niche")
    first.header_signature = second.header_signature = compute_header_signature(HEADERS)
    context.source.profile_id = first.id
    context.source.filename_pattern = "*.xlsx"
    other = save_source(
        db_session,
        context.actor,
        MailSourceWrite(
            name="Niche sheet",
            vendor_id=context.vendor.id,
            profile_id=second.id,
            sender_addresses=["stock@vendor.example"],
            filename_pattern="*.xlsx",
            is_active=True,
        ),
    )
    other.starts_at = context.source.starts_at
    db_session.commit()
    reader = FakeMail()
    reader.add("workbook", workbook_bytes())
    reader.files["workbook"] = [("inventory.xlsx", workbook_bytes())]
    run(db_session, context, reader, tmp_path)
    rows, total = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert total == 2 and all(row.status == "COMPLETED" for row in rows)
    assert {row.quantity_available for row in snapshots(db_session, context)} == {Decimal(2), None}
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ImportFile)
            .where(ImportFile.vendor_id == context.vendor.id)
        )
        == 1
    )
    assert len({row.import_job_id for row in rows}) == 2


def test_interrupted_snapshot_is_recovered_before_newer_stock_without_duplicates(
    db_session: Session, context: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.ingestion import ImportJobRow
    from app.services import snapshots as snapshot_service

    reader = FakeMail()
    reader.add("interrupted", csv() + b"B,Second item,9,20.00\n")
    monkeypatch.setattr(snapshot_service, "CHUNK_ROWS", 1)
    original = snapshot_service._Snapshotter._snapshot_chunk
    chunks = 0

    def interrupted(self: snapshot_service._Snapshotter, rows: list[ImportJobRow]) -> None:
        nonlocal chunks
        chunks += 1
        if chunks == 2:
            raise RuntimeError("synthetic process interruption")
        original(self, rows)

    monkeypatch.setattr(snapshot_service._Snapshotter, "_snapshot_chunk", interrupted)
    run(db_session, context, reader, tmp_path)
    assert len(snapshots(db_session, context)) == 1
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    first = rows[0]
    assert first.status == "NEEDS_REVIEW"
    reader.add("new", csv("12"), received=NOW + timedelta(hours=1))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    later = next(row for row in rows if row.message_id == "new")
    assert later.status == "FAILED" and later.error_code == "PRIOR_IMPORT_INCOMPLETE"
    monkeypatch.setattr(snapshot_service._Snapshotter, "_snapshot_chunk", original)
    retry_delivery(db_session, context.actor, first.id)
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=2))
    assert first.status == later.status == "COMPLETED"
    assert len(snapshots(db_session, context)) == 3
    assert (
        sum(row.import_job_id == first.import_job_id for row in snapshots(db_session, context)) == 2
    )


def test_operator_can_explicitly_retry_with_corrected_profile(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    reader = FakeMail()
    reader.add("wrong-layout", csv("bad"))
    run(db_session, context, reader, tmp_path)
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    [row] = rows
    replacement = profile(db_session, context.vendor, context.actor, "Corrected profile")
    context.source.profile_id = replacement.id
    db_session.commit()
    retry_delivery(db_session, context.actor, row.id, use_current_profile=True)
    assert (
        row.profile_id == replacement.id and row.import_job_id is None and row.status == "PENDING"
    )


def test_paused_backlog_cannot_occupy_the_active_delivery_queue(
    db_session: Session, context: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = FakeMail()
    reader.add("paused-backlog", csv())
    original = reader.attachment

    def unavailable(message: str, attachment: str, maximum: int) -> bytes:
        raise IntakeError("GRAPH_CONNECTION_FAILED", retryable=True)

    monkeypatch.setattr(reader, "attachment", unavailable)
    context.settings.vendor_mail_max_deliveries = 1
    run(db_session, context, reader, tmp_path)
    [paused] = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)[0]
    assert paused.status == "FAILED"
    context.source.is_active = False
    other_vendor = factories.make_vendor(db_session, context.org)
    other_profile = profile(db_session, other_vendor, context.actor)
    active = save_source(
        db_session,
        context.actor,
        MailSourceWrite(
            name="Active vendor",
            vendor_id=other_vendor.id,
            profile_id=other_profile.id,
            sender_addresses=["other@vendor.example"],
            filename_pattern="*.csv",
            is_active=True,
        ),
    )
    active.starts_at = NOW - timedelta(days=1)
    db_session.commit()
    monkeypatch.setattr(reader, "attachment", original)
    reader.add(
        "active", csv("12"), received=NOW + timedelta(hours=1), sender="other@vendor.example"
    )
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    applied = next(row for row in rows if row.message_id == "active")
    assert applied.status == "COMPLETED" and paused.status == "FAILED" and paused.attempts == 1
    assert not snapshots(db_session, context)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(VendorInventorySnapshot)
            .where(VendorInventorySnapshot.vendor_id == other_vendor.id)
        )
        == 1
    )


def test_retrying_reappeared_historical_content_cannot_falsely_refresh_the_source(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    reader = FakeMail()
    reader.add("a", csv("5"))
    run(db_session, context, reader, tmp_path)
    reader.add("b", csv("10"), received=NOW + timedelta(hours=1))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=1))
    reader.add("a-again", csv("5"), received=NOW + timedelta(hours=2))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=2))
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    old = next(row for row in rows if row.message_id == "a-again")
    assert old.status == "NEEDS_REVIEW" and old.error_code == "OLDER_CONTENT_REAPPEARED"
    assert old.import_job_id is not None
    retry_delivery(db_session, context.actor, old.id)
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=3))
    assert old.status == "NEEDS_REVIEW" and old.error_code == "OLDER_CONTENT_REAPPEARED"
    assert context.source.last_success_at == NOW + timedelta(hours=1)
    assert len(snapshots(db_session, context)) == 2
    reader.add("delayed-valid", csv("12"), received=NOW + timedelta(minutes=90))
    run(db_session, context, reader, tmp_path, at=NOW + timedelta(hours=4))
    rows, _ = VendorMailRepository(db_session, context.org.id).history(None, 1, 50)
    assert next(row for row in rows if row.message_id == "delayed-valid").status == "COMPLETED"
    assert context.source.last_success_at == NOW + timedelta(minutes=90)
    assert len(snapshots(db_session, context)) == 3
