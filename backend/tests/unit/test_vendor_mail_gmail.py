"""Gmail protocol/consent boundaries and durable cursor replay; synthetic mail only."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.vendor_mail.gmail import GMAIL, READ_SCOPE, TOKEN_URL, GmailMailReader
from app.integrations.vendor_mail.types import IntakeError
from app.jobs.vendor_mail import run_vendor_mail_job
from app.services.vendor_mail import account_key

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
MAILBOX = "inventory@example.test"
RAW = b"SKU,Description,Qty,Cost\r\nA,Test item,5,10.50\r\n"


def settings(**overrides: Any) -> Settings:
    return Settings(
        **{
            "vendor_mail_provider": "gmail",
            "vendor_mail_gmail_client_id": "test.apps.googleusercontent.com",
            "vendor_mail_gmail_client_secret": "private-test-client-secret",
            "vendor_mail_gmail_refresh_token": "private-test-refresh-token",
            "vendor_mail_mailbox": MAILBOX,
            "vendor_mail_organization_slug": "test-org",
            **overrides,
        }
    )


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def message(identifier: str = "abc1", *, external: bool = False, **extra: Any) -> dict[str, Any]:
    attachment_body: dict[str, Any] = {"size": len(RAW)}
    attachment_body["attachmentId" if external else "data"] = "file1" if external else encoded(RAW)
    return {
        "id": identifier,
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Vendor Stock <Stock@Vendor.Example>"},
                {"name": "Subject", "value": "Inventory update"},
                {"name": "Date", "value": "Fri, 1 Jan 1999 00:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": encoded(
                                    b'<a href="https://files.vendor.example/list?sig=private">'
                                    b"Download Excel</a>"
                                )
                            },
                        },
                    ],
                },
                {"mimeType": "text/csv", "filename": "inventory.csv", "body": attachment_body},
                {
                    "mimeType": "image/png",
                    "filename": "pixel.png",
                    "headers": [
                        {"name": "Content-Disposition", "value": 'inline; filename="pixel.png"'}
                    ],
                    "body": {"size": 3, "data": encoded(b"PNG")},
                },
            ],
        },
        **extra,
    }


class Gmail:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.profile_email = MAILBOX
        self.profile_history = "100"
        self.scope = READ_SCOPE
        self.token_status = 200
        self.messages: dict[str, dict[str, Any]] = {"abc1": message()}
        self.list_pages: dict[str, dict[str, Any]] = {"": {"messages": [{"id": "abc1"}]}}
        self.history_pages: dict[str, dict[str, Any]] = {"": {"historyId": "200"}}
        self.history_status = 200
        self.file = RAW

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if str(request.url) == TOKEN_URL:
            assert request.method == "POST"
            assert "authorization" not in request.headers
            assert parse_qs(request.content.decode()) == {
                "client_id": ["test.apps.googleusercontent.com"],
                "client_secret": ["private-test-client-secret"],
                "refresh_token": ["private-test-refresh-token"],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(
                self.token_status,
                json={
                    "access_token": "private-access-token",
                    "expires_in": 3600,
                    "scope": self.scope,
                    "error_description": "private failure body",
                },
            )
        assert request.method == "GET"
        assert str(request.url).startswith(GMAIL + "/")
        assert request.headers["authorization"] == "Bearer private-access-token"
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        if path == "/profile":
            return httpx.Response(
                200, json={"emailAddress": self.profile_email, "historyId": self.profile_history}
            )
        if path == "/messages":
            return httpx.Response(
                200, json=self.list_pages[request.url.params.get("pageToken", "")]
            )
        if path == "/history":
            return httpx.Response(
                self.history_status,
                json=self.history_pages[request.url.params.get("pageToken", "")],
            )
        if path.endswith("/attachments/file1"):
            return httpx.Response(200, json={"data": encoded(self.file), "size": len(self.file)})
        if path.startswith("/messages/"):
            identifier = path.split("/")[2]
            return (
                httpx.Response(200, json=self.messages[identifier])
                if identifier in self.messages
                else httpx.Response(404)
            )
        raise AssertionError("unexpected read endpoint")

    def reader(self, **overrides: Any) -> GmailMailReader:
        return GmailMailReader(
            settings(**overrides), client=httpx.Client(transport=httpx.MockTransport(self.handle))
        )


def test_provider_configuration_is_independent_and_secrets_are_redacted() -> None:
    configured = settings()
    assert configured.vendor_mail_configured
    assert configured.vendor_mail_missing_settings == []
    assert run_vendor_mail_job(configured) == "DISABLED"
    assert "private-test" not in repr(configured)
    assert (
        Settings(
            vendor_mail_provider="gmail", vendor_mail_gmail_refresh_token=" "
        ).vendor_mail_gmail_refresh_token
        is None
    )
    missing = Settings(vendor_mail_provider="gmail").vendor_mail_missing_settings
    assert "VENDOR_MAIL_GMAIL_REFRESH_TOKEN" in missing
    assert "VENDOR_MAIL_TENANT_ID" not in missing
    with pytest.raises(ValidationError):
        settings(vendor_mail_provider="imap")


def test_provider_account_keys_keep_graph_compatible_and_separate_mailbox_label_client() -> None:
    graph = Settings(vendor_mail_mailbox=MAILBOX)
    assert account_key(graph) == hashlib.sha256(f"None|{MAILBOX}|Inbox".encode()).hexdigest()
    original = account_key(settings())
    assert original != account_key(graph)
    for override in (
        {"vendor_mail_mailbox": "other@example.test"},
        {"vendor_mail_gmail_label_id": "Label_123"},
        {"vendor_mail_gmail_client_id": "another-client"},
    ):
        assert account_key(settings(**override)) != original
    assert account_key(settings(vendor_mail_gmail_refresh_token="rotated")) == original


def test_wrong_google_account_is_refused_before_message_access() -> None:
    gmail = Gmail()
    gmail.profile_email = "somebody-else@example.test"
    with pytest.raises(IntakeError, match="GMAIL_MAILBOX_MISMATCH"):
        gmail.reader().page(None, NOW - timedelta(hours=1))
    assert [r.url.path for r in gmail.calls] == ["/token", "/gmail/v1/users/me/profile"]


@pytest.mark.parametrize(
    "status,scope,code",
    [
        (400, READ_SCOPE, "GMAIL_AUTHORIZATION_REQUIRED"),
        (200, "https://www.googleapis.com/auth/gmail.modify", "GMAIL_READONLY_SCOPE_REQUIRED"),
    ],
)
def test_revoked_or_overbroad_grant_does_not_read_mail(status: int, scope: str, code: str) -> None:
    gmail = Gmail()
    gmail.token_status, gmail.scope = status, scope
    with pytest.raises(IntakeError, match=code) as error:
        gmail.reader().page(None, NOW)
    assert len(gmail.calls) == 1
    assert "private" not in str(error.value)


def test_nested_mime_links_and_inline_attachment_bytes_are_preserved() -> None:
    gmail = Gmail()
    reader = gmail.reader()
    page = reader.page(None, NOW - timedelta(hours=1))
    (found,) = page.messages
    assert found.body == "" and not found.body_loaded
    assert not any(r.url.params.get("format") == "full" for r in gmail.calls)
    found = reader.message(found.id)
    assert found.sender == "stock@vendor.example"
    assert found.received_at == NOW  # internalDate, never the untrusted Date header
    assert "https://files.vendor.example/list?sig=private" in found.body
    assert found.has_attachments
    files = reader.attachments(found.id)
    inventory = next(a for a in files if a.name == "inventory.csv")
    assert reader.attachment(found.id, inventory.id, len(RAW)) == RAW
    assert next(a for a in files if a.name == "pixel.png").inline
    assert not any("attachments" in r.url.path for r in gmail.calls)
    assert len([r for r in gmail.calls if r.url.path == "/token"]) == 1
    assert json.loads(page.checkpoint.removeprefix("gmail:v1:")) == {
        "mode": "history",
        "history": "100",
    }


def test_external_attachment_body_and_bounds_are_checked_before_decoding() -> None:
    gmail = Gmail()
    gmail.messages["abc1"] = message(external=True)
    reader = gmail.reader()
    inventory = next(a for a in reader.attachments("abc1") if a.name == "inventory.csv")
    with pytest.raises(IntakeError, match="FILE_OR_MESSAGE_TOO_LARGE"):
        reader.attachment("abc1", inventory.id, len(RAW) - 1)
    assert not any("attachments" in r.url.path for r in gmail.calls)
    assert reader.attachment("abc1", inventory.id, len(RAW)) == RAW
    gmail.file = b"X" * (len(RAW) + 1)  # a lying attachment size cannot bypass the byte limit
    with pytest.raises(IntakeError, match="FILE_OR_MESSAGE_TOO_LARGE"):
        reader.attachment("abc1", inventory.id, len(RAW))


def test_initial_anchor_survives_restart_and_catches_arrivals_during_listing() -> None:
    gmail = Gmail()
    gmail.list_pages[""]["nextPageToken"] = "private-page"
    gmail.list_pages["private-page"] = {"messages": []}
    first = gmail.reader().page(None, NOW - timedelta(hours=1))
    assert first.more
    gmail.profile_history = "999"  # restart after new mail arrived during listing
    second = gmail.reader().page(first.checkpoint, NOW - timedelta(hours=1))
    assert not second.more
    assert json.loads(second.checkpoint.removeprefix("gmail:v1:"))["history"] == "100"
    gmail.messages["abc2"] = message("abc2")
    gmail.history_pages[""] = {
        "history": [{"messagesAdded": [{"message": {"id": "abc2"}}]}],
        "historyId": "999",
    }
    third = gmail.reader().page(second.checkpoint, NOW - timedelta(hours=1))
    assert [m.id for m in third.messages] == ["abc2"]
    assert gmail.calls[-2].url.params["startHistoryId"] == "100"


def test_history_pages_do_not_advance_anchor_early_or_duplicate_generic_changes() -> None:
    gmail = Gmail()
    gmail.history_pages = {
        "": {
            "historyId": "800",
            "nextPageToken": "next",
            "history": [
                {
                    "messages": [{"id": "abc1"}],
                    "messagesAdded": [{"message": {"id": "abc1"}}, {"message": {"id": "abc1"}}],
                }
            ],
        },
        "next": {
            "historyId": "900",
            "history": [
                {"messages": [{"id": "abc1"}], "labelsAdded": [{"message": {"id": "abc1"}}]}
            ],
        },
    }
    cursor = 'gmail:v1:{"mode":"history","history":"100"}'
    reader = gmail.reader()
    first = reader.page(cursor, NOW - timedelta(hours=1))
    assert len(first.messages) == 1 and first.more
    assert json.loads(first.checkpoint.removeprefix("gmail:v1:"))["history"] == "100"
    last = reader.page(first.checkpoint, NOW - timedelta(hours=1))
    assert last.messages == () and not last.more
    assert json.loads(last.checkpoint.removeprefix("gmail:v1:"))["history"] == "900"
    queries = [r.url.params for r in gmail.calls if r.url.path.endswith("/history")]
    assert [q["startHistoryId"] for q in queries] == ["100", "100"]
    assert all(q["historyTypes"] == "messageAdded" for q in queries)


@pytest.mark.parametrize(
    "labels", [["SENT"], ["DRAFT"], ["SPAM", "INBOX"], ["TRASH", "INBOX"], ["OTHER"]]
)
def test_sent_draft_spam_trash_and_wrong_label_do_not_route(labels: list[str]) -> None:
    gmail = Gmail()
    gmail.messages["abc1"]["labelIds"] = labels
    assert gmail.reader().page(None, NOW).messages == ()


def test_label_free_mode_and_lookback_do_not_import_old_or_deleted_mail() -> None:
    gmail = Gmail()
    gmail.messages["abc1"]["labelIds"] = ["Label_abc"]
    assert len(gmail.reader(vendor_mail_gmail_label_id="").page(None, NOW).messages) == 1
    assert (
        not gmail.reader(vendor_mail_gmail_label_id="")
        .page(None, NOW + timedelta(seconds=1))
        .messages
    )
    gmail.list_pages[""] = {"messages": [{"id": "deleted"}]}
    assert gmail.reader().page(None, NOW).messages == ()


def test_expired_history_requires_replay_and_unsafe_cursor_is_never_a_request_url() -> None:
    gmail = Gmail()
    gmail.history_status = 404
    reader = gmail.reader()
    with pytest.raises(IntakeError, match="GMAIL_CURSOR_EXPIRED"):
        reader.page('gmail:v1:{"mode":"history","history":"100"}', NOW)
    for invalid in ("https://attacker.example/token", 'gmail:v1:{"mode":"unknown"}', "gmail:v1:[]"):
        with pytest.raises(IntakeError, match="GMAIL_CURSOR_INVALID"):
            reader.page(invalid, NOW)
    assert all(r.url.host in ("gmail.googleapis.com", "oauth2.googleapis.com") for r in gmail.calls)


def test_retry_is_bounded_and_never_leaks_remote_error_body() -> None:
    sleep = Mock()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                503, text="private response body", headers={"Retry-After": "999"}
            )
        )
    )
    reader = GmailMailReader(settings(), client=client, sleep=sleep)
    with pytest.raises(IntakeError, match="GMAIL_TEMPORARILY_UNAVAILABLE") as error:
        reader.page(None, NOW)
    assert sleep.call_count == 2
    assert all(call.args == (30.0,) for call in sleep.call_args_list)
    assert error.value.retryable and "private" not in str(error.value)


def test_malformed_base64_nested_email_and_complex_mime_are_not_silently_imported() -> None:
    gmail = Gmail()
    gmail.messages["abc1"]["payload"]["parts"][1]["body"]["data"] = "@@bad!"
    reader = gmail.reader()
    inventory = next(a for a in reader.attachments("abc1") if a.name == "inventory.csv")
    with pytest.raises(IntakeError, match="GMAIL_MESSAGE_INVALID"):
        reader.attachment("abc1", inventory.id, 100)
    gmail.messages["abc1"] = message()
    gmail.messages["abc1"]["payload"] = {
        "mimeType": "message/rfc822",
        "filename": "forward.eml",
        "parts": [message()["payload"]],
    }
    assert not any(a.name == "inventory.csv" for a in gmail.reader().attachments("abc1"))
    root: dict[str, Any] = {"mimeType": "multipart/mixed"}
    node = root
    for _ in range(22):
        child: dict[str, Any] = {"mimeType": "multipart/mixed"}
        node["parts"] = [child]
        node = child
    gmail.messages["abc1"]["payload"] = root
    with pytest.raises(IntakeError, match="GMAIL_MIME_TOO_COMPLEX"):
        gmail.reader().attachments("abc1")


def test_external_html_body_is_loaded_only_when_a_routed_link_needs_it() -> None:
    gmail = Gmail()
    html = b'<a href="https://files.vendor.example/list">Download</a>'
    gmail.file = html
    gmail.messages["abc1"]["payload"]["parts"][0]["parts"][0]["body"] = {
        "size": len(html),
        "attachmentId": "file1",
    }
    reader = gmail.reader()
    assert not reader.page(None, NOW).messages[0].body_loaded
    assert not any("attachments" in r.url.path for r in gmail.calls)
    assert "https://files.vendor.example/list" in reader.message("abc1").body


def test_gmail_quota_403_is_retried_without_treating_it_as_revoked_access() -> None:
    gmail = Gmail()
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/history"):
            attempts += 1
            if attempts < 3:
                return httpx.Response(
                    403,
                    json={
                        "error": {
                            "errors": [
                                {
                                    "reason": "userRateLimitExceeded",
                                    "message": "private quota detail",
                                },
                            ]
                        }
                    },
                )
        return gmail.handle(request)

    sleep = Mock()
    reader = GmailMailReader(
        settings(), client=httpx.Client(transport=httpx.MockTransport(handle)), sleep=sleep
    )
    assert not reader.page('gmail:v1:{"mode":"history","history":"100"}', NOW).more
    assert attempts == 3 and sleep.call_count == 2
