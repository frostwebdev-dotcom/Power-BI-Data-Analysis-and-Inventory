"""Mailbox boundaries, download isolation and source validation; no network."""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.vendor_mail.downloads import (
    DownloadResponse,
    approved_initial_link,
    checked_url,
    download_file,
    email_links,
    public_address,
)
from app.integrations.vendor_mail.graph import GraphMailReader
from app.integrations.vendor_mail.types import IntakeError
from app.jobs.vendor_mail import register_vendor_mail_jobs, run_vendor_mail_job
from app.schemas.vendor_mail import MailSourceWrite
from tests.unit.test_jobs import FakeRunner

NOW = datetime(2026, 11, 6, 16, tzinfo=UTC)


def configured(**extra: Any) -> Settings:
    return Settings(
        vendor_mail_tenant_id=uuid.UUID(int=1),
        vendor_mail_client_id=uuid.UUID(int=2),
        vendor_mail_client_secret="private-client-secret",
        vendor_mail_mailbox="inventory@example.test",
        vendor_mail_organization_slug="test-org",
        **extra,
    )


def test_disabled_intake_does_not_open_a_connection() -> None:
    assert run_vendor_mail_job(Settings()) == "DISABLED"
    assert not Settings().vendor_mail_configured
    assert "VENDOR_MAIL_MAILBOX" in Settings().vendor_mail_missing_settings
    assert "private-client-secret" not in repr(configured())


def test_mail_job_has_an_independent_interval() -> None:
    runner = FakeRunner()
    register_vendor_mail_jobs(runner, Settings(vendor_mail_poll_minutes=7))
    assert [(job.job_id, job.interval_minutes) for job in runner.jobs] == [
        ("vendor.inventory.email", 7)
    ]


def source_payload(**extra: Any) -> dict[str, Any]:
    return dict(
        name="Test inventory",
        vendor_id=str(uuid.uuid4()),
        profile_id=str(uuid.uuid4()),
        sender_addresses=["Stock@Vendor.Example"],
        **extra,
    )


def test_exact_sender_routing_and_link_rules_are_validated() -> None:
    assert MailSourceWrite.model_validate(source_payload()).sender_addresses == [
        "stock@vendor.example"
    ]
    with pytest.raises(ValidationError):
        MailSourceWrite.model_validate(
            {**source_payload(), "sender_addresses": ["*@vendor.example"]}
        )
    with pytest.raises(ValidationError):
        MailSourceWrite.model_validate(source_payload(mode="LINK"))
    with pytest.raises(ValidationError):
        MailSourceWrite.model_validate(
            source_payload(
                mode="LINK",
                download_hosts=["*.example.com"],
                download_path_prefix="/files/",
                download_filename="inventory.xlsx",
            )
        )


def test_extracts_anchors_and_plain_links_without_loading_email_resources() -> None:
    body = '<a href="https://files.example.com/stock?a=1&amp;b=2">Get stock</a><img src="https://tracker.test/pixel">'
    assert email_links(body) == ["https://files.example.com/stock?a=1&b=2"]
    assert email_links("Download https://files.example.com/files/stock.xlsx.") == [
        "https://files.example.com/files/stock.xlsx"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.com/file.xlsx",
        "https://evil.test/file.xlsx",
        "https://user:pass@files.example.com/file.xlsx",
        "https://files.example.com:444/file.xlsx",
        "https://files.example.com/files/../private.xlsx",
        "https://files.example.com/a#fragment",
        "https://files.example.com/\r\ninjected",
        "file:///etc/passwd",
    ],
)
def test_unapproved_urls_are_refused_before_fetch(url: str) -> None:
    fetch = Mock()
    with pytest.raises(IntakeError):
        download_file(url, hosts=["files.example.com"], max_bytes=100, fetch=fetch)
    fetch.assert_not_called()


def test_link_path_rule_excludes_unrelated_links() -> None:
    assert approved_initial_link(
        "https://files.example.com/stock/list?sig=private", ["files.example.com"], "/stock/"
    )
    assert not approved_initial_link(
        "https://files.example.com/unsubscribe", ["files.example.com"], "/stock/"
    )
    checked_url("https://files.example.com/stock/list?sig=private", ["files.example.com"])


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "fc00::1"]
)
def test_dns_must_resolve_only_to_public_addresses(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(IntakeError, match="DOWNLOAD_ADDRESS_NOT_PUBLIC"):
        public_address("files.example.com")


def test_safe_redirects_are_revalidated_and_private_urls_never_fetch() -> None:
    calls: list[tuple[str, str]] = []

    def fetch(url: str, address: str, _limit: int) -> DownloadResponse:
        calls.append((url, address))
        return DownloadResponse(302, {"location": "https://127.0.0.1/secret"}, b"")

    with pytest.raises(IntakeError, match="DOWNLOAD_LINK_NOT_APPROVED"):
        download_file(
            "https://files.example.com/stock",
            hosts=["files.example.com"],
            max_bytes=100,
            resolve=lambda _: "1.1.1.1",
            fetch=fetch,
        )
    assert calls == [("https://files.example.com/stock", "1.1.1.1")]


def test_redirected_file_uses_approved_host_and_pinned_address() -> None:
    calls: list[str] = []

    def fetch(url: str, address: str, _limit: int) -> DownloadResponse:
        assert address == "1.1.1.1"
        calls.append(url)
        return (
            DownloadResponse(302, {"location": "/file.csv"}, b"")
            if len(calls) == 1
            else DownloadResponse(200, {"content-type": "text/csv"}, b"sku,qty\na,5\n")
        )

    content = download_file(
        "https://files.example.com/stock",
        hosts=["files.example.com"],
        max_bytes=100,
        resolve=lambda _: "1.1.1.1",
        fetch=fetch,
    )
    assert content == b"sku,qty\na,5\n" and len(calls) == 2


@pytest.mark.parametrize(
    "result,code",
    [
        (
            DownloadResponse(200, {"content-type": "text/html"}, b"login"),
            "DOWNLOAD_PAGE_REQUIRES_REVIEW",
        ),
        (DownloadResponse(403, {}, b"login"), "DOWNLOAD_LOGIN_REQUIRED"),
        (DownloadResponse(200, {}, b"x" * 101), "FILE_OR_MESSAGE_TOO_LARGE"),
        (DownloadResponse(503, {}, b"outage"), "DOWNLOAD_TEMPORARILY_UNAVAILABLE"),
    ],
)
def test_failed_downloads_are_coded_and_do_not_disclose_urls(
    result: DownloadResponse, code: str
) -> None:
    with pytest.raises(IntakeError) as caught:
        download_file(
            "https://files.example.com/stock?sig=private",
            hosts=["files.example.com"],
            max_bytes=100,
            resolve=lambda _: "1.1.1.1",
            fetch=lambda *args: result,
        )
    assert caught.value.code == code
    assert "private" not in str(caught.value)


def test_graph_delta_and_raw_attachment_requests_are_read_only_and_origin_scoped() -> None:
    calls: list[httpx.Request] = []
    settings = configured()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            assert request.url.host == "login.microsoftonline.com"
            return httpx.Response(
                200, json={"access_token": "private-access-token", "expires_in": 3600}
            )
        assert request.method == "GET" and request.url.host == "graph.microsoft.com"
        assert request.headers["Authorization"] == "Bearer private-access-token"
        if request.url.path.endswith("/$value"):
            return httpx.Response(200, content=b"original\x00bytes")
        assert request.url.params["changeType"] == "created"
        assert "ImmutableId" in request.headers["Prefer"]
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "message",
                        "from": {"emailAddress": {"address": "stock@vendor.example"}},
                        "receivedDateTime": NOW.isoformat(),
                        "subject": "Inventory",
                        "hasAttachments": True,
                        "body": {"content": ""},
                    }
                ],
                "@odata.deltaLink": str(request.url).split("?")[0] + "?$deltatoken=opaque",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GraphMailReader(settings, client=client)
        page = reader.page(None, NOW)
        assert page.messages[0].sender == "stock@vendor.example" and not page.more
        assert reader.attachment("message", "attachment", 100) == b"original\x00bytes"
        with pytest.raises(IntakeError):
            reader.page("https://evil.test/messages/delta?private", NOW)
    assert len(calls) == 3


def test_graph_retries_throttling_and_uses_only_safe_errors() -> None:
    attempts = 0
    delays: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "private", "expires_in": 3600})
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, text="sensitive provider response")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reader = GraphMailReader(configured(), client=client, sleep=delays.append)
        with pytest.raises(IntakeError, match="GRAPH_TEMPORARILY_UNAVAILABLE"):
            reader.page(None, NOW)
    assert attempts == 3 and delays == [2, 2]


def test_sparse_delta_entries_are_resolved_and_deleted_messages_are_skipped() -> None:
    looked_up: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "private", "expires_in": 3600})
        if request.url.path.endswith("/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "sparse"},
                        {"id": "gone"},
                        {"id": "removed", "@removed": {"reason": "deleted"}},
                    ],
                    "@odata.deltaLink": str(request.url).split("?")[0] + "?$deltatoken=opaque",
                },
            )
        looked_up.append(request.url.path.rsplit("/", 1)[-1])
        if looked_up[-1] == "gone":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": "sparse",
                "receivedDateTime": NOW.isoformat(),
                "from": {"emailAddress": {"address": "stock@vendor.example"}},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        page = GraphMailReader(configured(), client=client).page(None, NOW)
    assert [message.id for message in page.messages] == ["sparse"]
    assert looked_up == ["sparse", "gone"]
