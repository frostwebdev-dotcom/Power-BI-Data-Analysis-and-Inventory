"""Global-cloud Microsoft Graph mail reads; no send/write/delete operations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

from app.core.config import Settings
from app.integrations.vendor_mail.types import (
    IntakeError,
    MailAttachment,
    MailMessage,
    MailPage,
)

GRAPH = "https://graph.microsoft.com/v1.0"
MESSAGE_FIELDS = "id,from,subject,receivedDateTime,hasAttachments,body"


def parse_message(value: dict[str, Any]) -> MailMessage:
    try:
        received = datetime.fromisoformat(str(value["receivedDateTime"]).replace("Z", "+00:00"))
        if received.tzinfo is None:
            raise ValueError("timezone required")
        identifier = str(value["id"])
        if not identifier or len(identifier) > 2048:
            raise ValueError("invalid id")
        return MailMessage(
            id=identifier,
            sender=str(value.get("from", {}).get("emailAddress", {}).get("address", "")).lower(),
            subject=str(value.get("subject", ""))[:1000],
            received_at=received,
            has_attachments=bool(value.get("hasAttachments")),
            body=str(value.get("body", {}).get("content", ""))[:512000],
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise IntakeError("GRAPH_MESSAGE_INVALID", retryable=True) from None


class GraphMailReader:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.vendor_mail_provider != "microsoft365" or not settings.vendor_mail_configured:
            raise IntakeError("MAILBOX_NOT_CONFIGURED")
        self.settings = settings
        self.root = f"{GRAPH}/users/{quote(settings.vendor_mail_mailbox or '', safe='')}"
        folder = quote(settings.vendor_mail_folder_id, safe="")
        self.delta_path = f"{self.root}/mailFolders/{folder}/messages/delta"
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.sleep = sleep
        self.token = ""
        self.expires_at = 0.0

    def __enter__(self) -> GraphMailReader:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.owns_client:
            self.client.close()

    def _check_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "graph.microsoft.com"
            or not unquote(parsed.path).startswith(unquote(urlsplit(self.root).path) + "/")
            or parsed.fragment
        ):
            raise IntakeError("GRAPH_PAGINATION_ORIGIN_INVALID")

    def _request(self, method: str, url: str, *, max_bytes: int, **kwargs: Any) -> bytes:
        for attempt in range(3):
            try:
                with self.client.stream(method, url, **kwargs) as response:
                    code = response.status_code
                    if code == 410:
                        raise IntakeError("GRAPH_CURSOR_EXPIRED")
                    if code in (401, 403):
                        raise IntakeError("MAILBOX_ACCESS_DENIED")
                    if code == 404:
                        raise IntakeError("EMAIL_OR_ATTACHMENT_MISSING", review=True)
                    if code == 429 or code >= 500:
                        if attempt == 2:
                            raise IntakeError("GRAPH_TEMPORARILY_UNAVAILABLE", retryable=True)
                        try:
                            delay = min(
                                30.0,
                                max(1.0, float(response.headers.get("Retry-After", 2**attempt))),
                            )
                        except ValueError:
                            delay = float(2**attempt)
                        self.sleep(delay)
                        continue
                    if code != 200:
                        raise IntakeError("GRAPH_REQUEST_REFUSED")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
                    return bytes(content)
            except httpx.TransportError:
                if attempt == 2:
                    raise IntakeError("GRAPH_CONNECTION_FAILED", retryable=True) from None
                self.sleep(float(2**attempt))
        raise IntakeError("GRAPH_TEMPORARILY_UNAVAILABLE", retryable=True)

    def _access_token(self) -> str:
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        tenant = self.settings.vendor_mail_tenant_id
        secret = self.settings.vendor_mail_client_secret
        assert secret is not None
        raw = self._request(
            "POST",
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            max_bytes=65536,
            data={
                "client_id": str(self.settings.vendor_mail_client_id),
                "client_secret": secret.get_secret_value(),
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        value = self._json(raw)
        token = value.get("access_token")
        if not isinstance(token, str) or not token:
            raise IntakeError("MAILBOX_TOKEN_INVALID")
        self.token = token
        try:
            lifetime = max(0, int(value.get("expires_in", 300)) - 60)
        except (ValueError, TypeError):
            lifetime = 0
        self.expires_at = time.monotonic() + lifetime
        return token

    @staticmethod
    def _json(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (ValueError, UnicodeError):
            raise IntakeError("GRAPH_RESPONSE_INVALID", retryable=True) from None

    def _get(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> bytes:
        self._check_url(url)
        return self._request(
            "GET",
            url,
            max_bytes=max_bytes,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Prefer": 'IdType="ImmutableId", odata.maxpagesize=50',
            },
        )

    def page(self, checkpoint: str | None, since: datetime) -> MailPage:
        url = checkpoint or self.delta_path + "?" + urlencode(
            {
                "changeType": "created",
                "$select": MESSAGE_FIELDS,
                "$filter": "receivedDateTime ge " + since.isoformat(),
                "$orderby": "receivedDateTime desc",
            }
        )
        # Never send a bearer token to a URL supplied by an email or another origin.
        if unquote(urlsplit(url).path) != unquote(urlsplit(self.delta_path).path):
            raise IntakeError("GRAPH_PAGINATION_PATH_INVALID")
        value = self._json(self._get(url))
        next_url = value.get("@odata.nextLink") or value.get("@odata.deltaLink")
        if not isinstance(next_url, str) or not next_url:
            raise IntakeError("GRAPH_CHECKPOINT_MISSING", retryable=True)
        self._check_url(next_url)
        values = value.get("value")
        if not isinstance(values, list):
            raise IntakeError("GRAPH_RESPONSE_INVALID", retryable=True)
        messages = []
        for item in values:
            if not isinstance(item, dict) or "@removed" in item:
                continue
            # Delta can include sparse read-state changes, even with created
            # filtering. Resolve them before committing the page checkpoint.
            if not all(field in item for field in MESSAGE_FIELDS.split(",")):
                identifier = item.get("id")
                if not isinstance(identifier, str) or not identifier or len(identifier) > 2048:
                    raise IntakeError("GRAPH_MESSAGE_INVALID", retryable=True)
                try:
                    messages.append(self.message(identifier))
                except IntakeError as error:
                    if error.code != "EMAIL_OR_ATTACHMENT_MISSING":
                        raise
            else:
                messages.append(parse_message(item))
        return MailPage(tuple(messages), next_url, "@odata.nextLink" in value)

    def message(self, message_id: str) -> MailMessage:
        url = f"{self.root}/messages/{quote(message_id, safe='')}?" + urlencode(
            {"$select": MESSAGE_FIELDS}
        )
        return parse_message(self._json(self._get(url)))

    def attachments(self, message_id: str) -> list[MailAttachment]:
        url: str | None = (
            f"{self.root}/messages/{quote(message_id, safe='')}/attachments?"
            + urlencode(
                {
                    "$select": "id,name,size,isInline,contentType",
                }
            )
        )
        result: list[MailAttachment] = []
        for _ in range(20):
            if url is None:
                return result
            value = self._json(self._get(url))
            try:
                for item in value["value"]:
                    result.append(
                        MailAttachment(
                            str(item["id"]),
                            str(item.get("name", "")),
                            int(item.get("size", 0)),
                            bool(item.get("isInline")),
                            item.get("@odata.type") == "#microsoft.graph.fileAttachment",
                        )
                    )
                next_url = value.get("@odata.nextLink")
                url = next_url if isinstance(next_url, str) else None
            except (KeyError, TypeError, ValueError):
                raise IntakeError("GRAPH_ATTACHMENT_INVALID", retryable=True) from None
        raise IntakeError("TOO_MANY_ATTACHMENTS", review=True)

    def attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> bytes:
        url = (
            f"{self.root}/messages/{quote(message_id, safe='')}/attachments/"
            f"{quote(attachment_id, safe='')}/$value"
        )
        return self._get(url, max_bytes)
