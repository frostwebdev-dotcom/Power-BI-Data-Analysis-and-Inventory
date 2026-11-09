"""No real Google consent: exercise callback binding, refresh and private output."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from app.cli import vendor_mail_authorize as cli
from app.integrations.vendor_mail.gmail import GMAIL, READ_SCOPE, TOKEN_URL
from app.integrations.vendor_mail.gmail_oauth import GmailAuthorization
from app.integrations.vendor_mail.types import IntakeError
from tests.unit.test_vendor_mail_gmail import MAILBOX, encoded, settings

REDIRECT = "http://127.0.0.1:8765/oauth/callback"


def callback(flow: GmailAuthorization, **extra: str) -> str:
    return "/oauth/callback?" + urlencode({"state": flow.state, "code": "private-code", **extra})


def test_requests_only_readonly_offline_access_and_uses_fresh_pkce_and_state() -> None:
    first, second = (
        GmailAuthorization(settings(), REDIRECT),
        GmailAuthorization(settings(), REDIRECT),
    )
    query = parse_qs(urlsplit(first.authorization_url()).query)
    assert query["scope"] == [READ_SCOPE]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [encoded(hashlib.sha256(first.verifier.encode()).digest())]
    assert query["login_hint"] == [MAILBOX]
    assert first.state != second.state and first.verifier != second.verifier
    assert "private-test-client-secret" not in first.authorization_url()
    assert first.state not in repr(first) and first.verifier not in repr(first)


@pytest.mark.parametrize(
    "redirect",
    [
        "https://outside.example/oauth/callback",
        "http://0.0.0.0:8765/oauth/callback",
        "http://localhost:8765/oauth/callback",
        "http://127.0.0.1:8765/wrong",
        REDIRECT + "?url=outside",
        REDIRECT + "#fragment",
        "http://[bad",
        "http://127.0.0.1:99999/oauth/callback",
    ],
)
def test_callback_listener_cannot_be_bound_to_an_external_origin(redirect: str) -> None:
    with pytest.raises(IntakeError, match="GMAIL_CALLBACK_INVALID"):
        GmailAuthorization(settings(), redirect)


def test_invalid_duplicate_or_replayed_callback_never_consumes_a_valid_code() -> None:
    flow = GmailAuthorization(settings(), REDIRECT)
    for target, host in [
        (callback(flow, state="wrong"), flow.host),
        (callback(flow), "outside.example"),
        (callback(flow) + "&state=duplicate", flow.host),
        (callback(flow) + "&code=duplicate", flow.host),
        ("https://outside.example" + callback(flow), flow.host),
    ]:
        with pytest.raises(IntakeError, match="GMAIL_CALLBACK_INVALID"):
            flow.accept_callback(target, host)
        assert not flow.callback_used
    assert flow.accept_callback(callback(flow), flow.host) == "private-code"
    with pytest.raises(IntakeError, match="GMAIL_CALLBACK_INVALID"):
        flow.accept_callback(callback(flow), flow.host)


class Google:
    def __init__(self, flow: GmailAuthorization, **overrides: Any) -> None:
        self.flow = flow
        self.overrides = overrides
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if str(request.url) == TOKEN_URL:
            assert request.method == "POST"
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == ["private-test-client-secret"]
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"] == [self.flow.verifier]
                assert form["code"] == ["private-code"]
                assert form["redirect_uri"] == [REDIRECT]
                return httpx.Response(
                    200,
                    json={
                        "access_token": "private-access",
                        "refresh_token": "private-refresh",
                        "scope": READ_SCOPE,
                        **self.overrides,
                    },
                )
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == ["private-refresh"]
            return httpx.Response(
                200,
                json={
                    "access_token": "private-refreshed-access",
                    "scope": READ_SCOPE,
                    "expires_in": 3600,
                },
            )
        assert str(request.url) == GMAIL + "/profile" and request.method == "GET"
        return httpx.Response(
            200, json={"emailAddress": self.overrides.get("email", MAILBOX), "historyId": "100"}
        )


def test_code_exchange_verifies_exact_mailbox_and_refresh_without_loading_mail() -> None:
    flow = GmailAuthorization(settings(vendor_mail_enabled=True), REDIRECT)
    code = flow.accept_callback(callback(flow), flow.host)
    google = Google(flow)
    values = flow.exchange(code, client=httpx.Client(transport=httpx.MockTransport(google.handle)))
    assert values["VENDOR_MAIL_GMAIL_REFRESH_TOKEN"] == "private-refresh"
    assert values["VENDOR_MAIL_ENABLED"] == "false" and values["VENDOR_MAIL_PROVIDER"] == "gmail"
    assert len(google.calls) == 4  # exchange/profile, then a real refresh/profile check
    assert all(str(r.url) in (TOKEN_URL, GMAIL + "/profile") for r in google.calls)
    with pytest.raises(IntakeError, match="GMAIL_CALLBACK_INVALID"):
        flow.exchange(code)


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"email": "other@example.test"}, "GMAIL_MAILBOX_MISMATCH"),
        (
            {"scope": "https://www.googleapis.com/auth/gmail.modify"},
            "GMAIL_READONLY_SCOPE_REQUIRED",
        ),
        ({"refresh_token": None}, "GMAIL_REFRESH_TOKEN_MISSING"),
    ],
)
def test_wrong_identity_scope_or_absent_refresh_cannot_produce_runtime_settings(
    overrides: dict[str, Any],
    code: str,
) -> None:
    flow = GmailAuthorization(settings(), REDIRECT)
    authorization_code = flow.accept_callback(callback(flow), flow.host)
    google = Google(flow, **overrides)
    with pytest.raises(IntakeError, match=code) as error:
        flow.exchange(
            authorization_code, client=httpx.Client(transport=httpx.MockTransport(google.handle))
        )
    assert "private" not in str(error.value)


def test_denied_consent_and_provider_errors_are_safe_and_do_not_exchange() -> None:
    flow = GmailAuthorization(settings(), REDIRECT)
    with pytest.raises(IntakeError, match="GMAIL_AUTHORIZATION_CANCELLED"):
        flow.accept_callback(
            "/oauth/callback?" + urlencode({"state": flow.state, "error": "access_denied"}),
            flow.host,
        )
    with pytest.raises(IntakeError, match="GMAIL_CALLBACK_INVALID"):
        GmailAuthorization(settings(), REDIRECT).exchange("private-code")
    flow = GmailAuthorization(settings(), REDIRECT)
    code = flow.accept_callback(callback(flow), flow.host)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, text="private failure response")
        )
    )
    with pytest.raises(IntakeError, match="GMAIL_AUTHORIZATION_REFUSED") as error:
        flow.exchange(code, client=client)
    assert "private" not in str(error.value)


def test_private_output_is_new_owner_only_and_preserves_opaque_values(tmp_path: Path) -> None:
    path = tmp_path / "private" / "runtime.json"
    values = {"VENDOR_MAIL_GMAIL_REFRESH_TOKEN": "private-${UNEXPANDED}-'quoted'\"\\value"}
    with cli.private_output(path) as output:
        json.dump(values, output)
    assert json.loads(path.read_text()) == values
    public = tmp_path / "public"
    public.mkdir()
    public.chmod(0o755)
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(IntakeError, match="GMAIL_OUTPUT_ALREADY_EXISTS"), cli.private_output(path):
        pytest.fail("Must not overwrite a saved grant")
    assert json.loads(path.read_text()) == values
    with (
        pytest.raises(IntakeError, match="GMAIL_OUTPUT_DIRECTORY_NOT_PRIVATE"),
        cli.private_output(tmp_path / "public" / "runtime.json"),
    ):
        pytest.fail("Public directory must be rejected")


def test_no_output_in_git_and_no_incomplete_file_after_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with (
        pytest.raises(IntakeError, match="GMAIL_OUTPUT_MUST_BE_OUTSIDE_GIT"),
        cli.private_output(repo / "runtime.json"),
    ):
        pytest.fail("A grant must never be written into a repository")
    path = tmp_path / "private" / "runtime.json"
    with pytest.raises(IntakeError), cli.private_output(path) as output:
        output.write("private-incomplete")
        raise IntakeError("GMAIL_AUTHORIZATION_REFUSED")
    assert not path.exists()


def test_windows_acl_is_applied_to_empty_file_before_any_secret_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> Mock:
        assert path.read_bytes() == b""
        calls.append(arguments)
        return Mock(stdout="LOCAL\\operator\n")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "name", "nt")
            patch.setattr(subprocess, "run", run)
            cli._protect_output(path, descriptor)
    finally:
        os.close(descriptor)
    assert calls[0] == ["whoami"]
    assert calls[1][2:] == ["/inheritance:r", "/grant:r", "LOCAL\\operator:(F)"]


def test_real_loopback_handler_rejects_wrong_state_then_accepts_without_logging_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results: list[httpx.Response] = []
    workers: list[threading.Thread] = []

    def open_browser(url: str) -> bool:
        query = parse_qs(urlsplit(url).query)
        redirect = query["redirect_uri"][0]

        def requests() -> None:
            with httpx.Client(trust_env=False, timeout=5) as client:
                results.append(
                    client.get(redirect, params={"state": "wrong", "code": "private-code"})
                )
                results.append(
                    client.get(
                        redirect, params={"state": query["state"][0], "code": "private-code"}
                    )
                )

        worker = threading.Thread(target=requests)
        workers.append(worker)
        worker.start()
        return True

    flow, code = cli.receive_authorization(
        settings(), port=0, seconds=10, open_browser=open_browser
    )
    for worker in workers:
        worker.join(timeout=5)
    assert code == "private-code" and flow.callback_used
    assert [response.status_code for response in results] == [400, 200]
    assert all("private-code" not in response.text for response in results)
    assert results[-1].headers["cache-control"] == "no-store"
    assert "private-code" not in str(capsys.readouterr())


def test_cli_saves_only_after_verification_and_never_prints_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    flow = Mock()
    flow.exchange.return_value = {
        "VENDOR_MAIL_GMAIL_REFRESH_TOKEN": "private-refresh",
        "VENDOR_MAIL_ENABLED": "false",
    }
    receive = Mock(return_value=(flow, "private-code"))
    monkeypatch.setattr(cli, "get_settings", settings)
    monkeypatch.setattr(cli, "receive_authorization", receive)
    path = tmp_path / "private" / "runtime.json"
    assert cli.main(["--output", str(path)]) == 0
    assert json.loads(path.read_text())["VENDOR_MAIL_ENABLED"] == "false"
    captured = capsys.readouterr()
    assert "private-refresh" not in captured.out and "private-code" not in captured.out
    assert "private-refresh" not in captured.err
    assert cli.main(["--output", str(path)]) == 2
    assert receive.call_count == 1
    receive.side_effect = IntakeError("GMAIL_MAILBOX_MISMATCH")
    failed = path.with_name("failed.json")
    assert cli.main(["--output", str(failed)]) == 2 and not failed.exists()
