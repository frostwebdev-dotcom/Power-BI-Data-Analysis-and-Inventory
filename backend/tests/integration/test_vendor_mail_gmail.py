"""Real Gmail adapter into the PostgreSQL/import pipeline, with simulated Google I/O."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.db.session import get_db
from app.imports.storage import LocalStorageBackend
from app.main import create_app
from app.models import AuditEvent, ImportFile, ImportJob
from app.models.vendor_mail import VendorMailDelivery, VendorMailState
from app.services.vendor_mail import run_intake
from tests.integration.test_vendor_mail import Context, snapshots
from tests.integration.test_vendor_mail import context as context
from tests.unit.test_vendor_mail_gmail import NOW, RAW, Gmail, settings
from tests.unit.test_vendor_mail_gmail import message as gmail_message

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("known_sender", [False, True])
def test_invalid_or_unmatched_email_does_not_block_a_later_valid_vendor_file(
    db_session: Session,
    context: Context,
    tmp_path: Path,
    known_sender: bool,
) -> None:
    ctx = context
    ctx.settings = settings(app_env="test", vendor_mail_organization_slug=ctx.org.slug)
    gmail = Gmail()
    gmail.messages["abc1"]["payload"]["parts"][1]["filename"] = "X" * 1001
    if not known_sender:
        gmail.messages["abc1"]["payload"]["headers"][0]["value"] = "newsletter@unmatched.example"
    gmail.messages["abc2"] = gmail_message("abc2")
    gmail.list_pages[""] = {"messages": [{"id": "abc1"}, {"id": "abc2"}]}
    reader = gmail.reader()
    assert (
        run_intake(
            db_session,
            ctx.org.id,
            ctx.settings,
            reader,
            LocalStorageBackend(tmp_path),
            now=lambda: NOW,
        )
        == "COMPLETED"
    )
    assert len(snapshots(db_session, ctx)) == 1
    deliveries = db_session.scalars(select(VendorMailDelivery)).all()
    assert len(deliveries) == (2 if known_sender else 1)
    if known_sender:
        assert any(
            d.status == "NEEDS_REVIEW" and d.error_code == "GMAIL_MESSAGE_INVALID"
            for d in deliveries
        )
    else:
        assert not any(
            r.url.path.endswith("/messages/abc1") and r.url.params.get("format") == "full"
            for r in gmail.calls
        )


@pytest.mark.parametrize("mode", ["ATTACHMENT", "LINK"])
def test_gmail_arrival_replay_and_expired_history_keep_one_import_and_stock(
    db_session: Session,
    context: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    ctx = context
    ctx.settings = settings(
        app_env="test", vendor_mail_enabled=True, vendor_mail_organization_slug=ctx.org.slug
    )
    ctx.source.mode = mode
    if mode == "LINK":
        ctx.source.download_hosts = ["files.vendor.example"]
        ctx.source.download_path_prefix = "/list"
        ctx.source.download_filename = "inventory.csv"
        monkeypatch.setattr("app.services.vendor_mail.download_file", lambda *a, **k: RAW)
    db_session.commit()
    gmail = Gmail()
    storage = LocalStorageBackend(tmp_path)
    reader = gmail.reader(vendor_mail_enabled=True, vendor_mail_organization_slug=ctx.org.slug)

    def poll() -> str:
        return run_intake(db_session, ctx.org.id, ctx.settings, reader, storage, now=lambda: NOW)

    assert poll() == "COMPLETED"
    assert len(snapshots(db_session, ctx)) == 1
    assert snapshots(db_session, ctx)[0].quantity_available == 5
    gmail.history_pages[""] = {
        "historyId": "200",
        "history": [
            {"messagesAdded": [{"message": {"id": "abc1"}}]},
        ],
    }
    assert poll() == "COMPLETED"
    assert db_session.scalar(select(func.count()).select_from(ImportFile)) == 1
    assert db_session.scalar(select(func.count()).select_from(ImportJob)) == 1
    assert db_session.scalar(select(func.count()).select_from(VendorMailDelivery)) == 1
    state = db_session.scalar(select(VendorMailState))
    assert state is not None
    gmail.history_status = 404
    assert poll() == "POLL_FAILED"
    assert state.error_code == "GMAIL_CURSOR_EXPIRED" and state.checkpoint_url is None
    gmail.history_status = 200
    assert poll() == "COMPLETED"
    recovered = db_session.scalar(select(VendorMailState))
    assert recovered is not None and recovered.error_code is None
    assert recovered.checkpoint_url is not None
    assert len(snapshots(db_session, ctx)) == 1
    events = db_session.scalars(select(AuditEvent)).all()
    assert "private" not in repr([(e.before, e.after) for e in events])


def test_gmail_status_shows_provider_but_no_credentials(
    db_session: Session,
    context: Context,
) -> None:
    config = settings(app_env="test", vendor_mail_organization_slug=context.org.slug)
    app = create_app(config)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: context.actor
    with TestClient(app) as client:
        response = client.get("/api/v1/vendor-mail/status")
        assert response.status_code == 200
        assert response.json()["provider"] == "gmail"
        assert response.json()["configured"]
        assert not response.json()["enabled"]
        assert "private" not in response.text
