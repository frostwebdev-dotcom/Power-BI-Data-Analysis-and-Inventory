"""One-time confidential-client Gmail authorization; no mail content is read."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.vendor_mail.gmail import (
    GMAIL,
    READ_SCOPE,
    TOKEN_URL,
    GmailMailReader,
    _json,
    _read_limited,
)
from app.integrations.vendor_mail.types import IntakeError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALLBACK_PATH = "/oauth/callback"


class GmailAuthorization:
    """Private state is absent from reprs, URLs printed by the CLI and logs."""

    def __init__(self, settings: Settings, redirect_uri: str) -> None:
        if settings.vendor_mail_provider != "gmail" or any(
            not getattr(settings, name)
            for name in (
                "vendor_mail_gmail_client_id",
                "vendor_mail_gmail_client_secret",
                "vendor_mail_mailbox",
                "vendor_mail_organization_slug",
            )
        ):
            raise IntakeError("GMAIL_SETUP_NOT_CONFIGURED")
        try:
            parsed = urlsplit(redirect_uri)
            port = parsed.port
        except ValueError:
            raise IntakeError("GMAIL_CALLBACK_INVALID") from None
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not port
            or parsed.netloc != f"127.0.0.1:{port}"
            or parsed.path != CALLBACK_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        self.settings = settings
        self.redirect_uri = redirect_uri
        self.host = parsed.netloc
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(48)
        self.callback_used = False
        self.received_code: str | None = None
        self.exchange_used = False

    def authorization_url(self) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
        return (
            AUTH_URL
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.vendor_mail_gmail_client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": READ_SCOPE,
                    "state": self.state,
                    "code_challenge": challenge.decode().rstrip("="),
                    "code_challenge_method": "S256",
                    "access_type": "offline",
                    "prompt": "consent",
                    "include_granted_scopes": "false",
                    "login_hint": self.settings.vendor_mail_mailbox,
                }
            )
        )

    def accept_callback(self, target: str, host: str) -> str:
        if self.callback_used or host != self.host or len(target) > 16384:
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        try:
            parsed = urlsplit(target)
        except ValueError:
            raise IntakeError("GMAIL_CALLBACK_INVALID") from None
        if parsed.scheme or parsed.netloc or parsed.path != CALLBACK_PATH or parsed.fragment:
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        try:
            values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
        except ValueError:
            raise IntakeError("GMAIL_CALLBACK_INVALID") from None
        states = values.get("state", [])
        if (
            len(states) != 1
            or len(states[0]) > 200
            or not secrets.compare_digest(states[0].encode(), self.state.encode())
        ):
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        codes, errors = values.get("code", []), values.get("error", [])
        if len(errors) == 1 and errors[0] and not codes:
            self.callback_used = True
            raise IntakeError("GMAIL_AUTHORIZATION_CANCELLED")
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192 or errors:
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        self.callback_used = True
        self.received_code = codes[0]
        return codes[0]

    @staticmethod
    def _request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with client.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    raise IntakeError("GMAIL_AUTHORIZATION_REFUSED")
                return _json(_read_limited(response, 65536))
        except httpx.HTTPError:
            raise IntakeError("GMAIL_AUTHORIZATION_CONNECTION_FAILED") from None

    def exchange(self, code: str, *, client: httpx.Client | None = None) -> dict[str, str]:
        if (
            not self.callback_used
            or self.exchange_used
            or self.received_code is None
            or not secrets.compare_digest(code.encode(), self.received_code.encode())
        ):
            raise IntakeError("GMAIL_CALLBACK_INVALID")
        self.exchange_used = True
        self.received_code = None
        own_client = client is None
        client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        secret = self.settings.vendor_mail_gmail_client_secret
        assert secret is not None
        try:
            token = self._request(
                client,
                "POST",
                TOKEN_URL,
                data={
                    "client_id": self.settings.vendor_mail_gmail_client_id,
                    "client_secret": secret.get_secret_value(),
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": self.verifier,
                    "redirect_uri": self.redirect_uri,
                },
            )
            if set(str(token.get("scope", "")).split()) != {READ_SCOPE}:
                raise IntakeError("GMAIL_READONLY_SCOPE_REQUIRED")
            access, refresh = token.get("access_token"), token.get("refresh_token")
            if any(
                not isinstance(value, str)
                or not value
                or len(value) > 16384
                or any(ord(char) < 32 for char in value)
                for value in (access, refresh)
            ):
                raise IntakeError("GMAIL_REFRESH_TOKEN_MISSING")
            profile = self._request(
                client,
                "GET",
                GMAIL + "/profile",
                headers={
                    "Authorization": f"Bearer {access}",
                },
            )
            email = profile.get("emailAddress")
            if (
                not isinstance(email, str)
                or email.lower() != (self.settings.vendor_mail_mailbox or "").lower()
            ):
                raise IntakeError("GMAIL_MAILBOX_MISMATCH")
            assert isinstance(refresh, str)
            configured = self.settings.model_copy(
                update={
                    "vendor_mail_gmail_refresh_token": SecretStr(refresh),
                    "vendor_mail_enabled": False,
                }
            )
            # Exercise the same refresh/identity path the unattended importer uses.
            # This reads the profile only, never message bodies or attachments.
            with GmailMailReader(configured, client=client) as reader:
                reader._verify_mailbox()
            return {
                "VENDOR_MAIL_PROVIDER": "gmail",
                "VENDOR_MAIL_ENABLED": "false",
                "VENDOR_MAIL_GMAIL_CLIENT_ID": self.settings.vendor_mail_gmail_client_id or "",
                "VENDOR_MAIL_GMAIL_CLIENT_SECRET": secret.get_secret_value(),
                "VENDOR_MAIL_GMAIL_REFRESH_TOKEN": refresh,
                "VENDOR_MAIL_MAILBOX": self.settings.vendor_mail_mailbox or "",
                "VENDOR_MAIL_ORGANIZATION_SLUG": self.settings.vendor_mail_organization_slug or "",
                "VENDOR_MAIL_GMAIL_LABEL_ID": self.settings.vendor_mail_gmail_label_id,
            }
        finally:
            if own_client:
                client.close()
