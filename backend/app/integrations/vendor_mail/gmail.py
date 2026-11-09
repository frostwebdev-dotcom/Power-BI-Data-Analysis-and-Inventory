"""Owner-authorized, read-only Gmail intake. No mailbox writes or vendor requests."""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.message import Message
from email.utils import getaddresses
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.integrations.vendor_mail.types import IntakeError, MailAttachment, MailMessage, MailPage

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 512000
MAX_CURSOR_BYTES = 16384
EXCLUDED_LABELS = {"SENT", "DRAFT", "SPAM", "TRASH"}


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    content = bytearray()
    deadline = time.monotonic() + 120
    for chunk in response.iter_bytes():
        if time.monotonic() > deadline:
            raise IntakeError("GMAIL_CONNECTION_FAILED", retryable=True)
        content.extend(chunk)
        if len(content) > limit:
            raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
    return bytes(content)


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, UnicodeError):
        raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True) from None


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", value):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    return quote(value, safe="")


def _history_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}", value):
        raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
    return value


def _envelope(value: dict[str, Any], message_id: str) -> MailMessage:
    if value.get("id") != message_id or not isinstance(value.get("payload"), dict):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    try:
        received = datetime.fromtimestamp(int(value["internalDate"]) / 1000, tz=UTC)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True) from None
    headers = _headers(value["payload"])
    senders = getaddresses(headers.get("from", []))
    sender = senders[0][1].lower() if len(senders) == 1 else ""
    # Metadata has no attachment/body payload. Only routed vendors load it later.
    return MailMessage(
        message_id,
        sender,
        headers.get("subject", [""])[0][:1000],
        received,
        has_attachments=True,
        body_loaded=False,
    )


def _decode(data: Any, max_bytes: int) -> bytes:
    if not isinstance(data, str):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    if len(data) > ((max_bytes + 2) // 3) * 4:
        raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True) from None
    if len(raw) > max_bytes:
        raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
    return raw


def _parts(payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    pending = [("part:0", payload, 0)]
    count = 0
    while pending:
        path, part, depth = pending.pop()
        count += 1
        if depth > 20 or count > 500 or not isinstance(part, dict):
            raise IntakeError("GMAIL_MIME_TOO_COMPLEX", review=True)
        yield path, part
        # Do not treat files embedded inside attached emails/containers as top-level stock.
        if part.get("filename") or str(part.get("mimeType", "")).startswith("message/"):
            continue
        children = part.get("parts", [])
        if not isinstance(children, list) or len(children) > 500:
            raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
        pending.extend((f"{path}.{i}", child, depth + 1) for i, child in enumerate(children))


def _headers(part: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    headers = part.get("headers", [])
    if not isinstance(headers, list) or len(headers) > 1000:
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    for header in headers:
        if not isinstance(header, dict):
            raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
        name, value = header.get("name"), header.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
        result.setdefault(name.lower(), []).append(value)
    return result


def _attachment(path: str, part: dict[str, Any]) -> MailAttachment | None:
    name = part.get("filename", "")
    if not isinstance(name, str) or len(name) > 1000:
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    if not name:
        return None
    body = part.get("body", {})
    if not isinstance(body, dict):
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    size = body.get("size", 0)
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
    inline = any(
        value.lower().split(";", 1)[0].strip() == "inline"
        for value in _headers(part).get("content-disposition", [])
    )
    mime = str(part.get("mimeType", ""))
    return MailAttachment(path, name, size, inline, not mime.startswith(("multipart/", "message/")))


class GmailMailReader:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.vendor_mail_provider != "gmail" or not settings.vendor_mail_configured:
            raise IntakeError("MAILBOX_NOT_CONFIGURED")
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.owns_client = client is None
        self.sleep = sleep
        self.token = ""
        self.expires_at = 0.0
        self.profile_history: str | None = None
        self.cache: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
        self.cache_bytes = 0

    def __enter__(self) -> GmailMailReader:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.owns_client:
            self.client.close()

    def _request(
        self, url: str, *, limit: int, token_request: bool = False, **kwargs: Any
    ) -> bytes:
        for attempt in range(3):
            try:
                with self.client.stream(
                    "POST" if token_request else "GET", url, **kwargs
                ) as response:
                    status = response.status_code
                    # Never retain token errors, mail bodies or private response URLs in errors.
                    if token_request and status in (400, 401, 403):
                        raise IntakeError("GMAIL_AUTHORIZATION_REQUIRED")
                    if status == 403:
                        # Gmail reports per-user quota throttling as 403, not only 429.
                        value = _json(_read_limited(response, 65536)).get("error")
                        errors = value.get("errors", []) if isinstance(value, dict) else []
                        reasons = (
                            {
                                e["reason"]
                                for e in errors
                                if isinstance(e, dict) and isinstance(e.get("reason"), str)
                            }
                            if isinstance(errors, list)
                            else set()
                        )
                        if reasons.intersection({"rateLimitExceeded", "userRateLimitExceeded"}):
                            status = 429
                    if status in (401, 403):
                        raise IntakeError("MAILBOX_ACCESS_DENIED")
                    if status == 400 and kwargs.get("params", {}).get("pageToken"):
                        raise IntakeError("GMAIL_CURSOR_EXPIRED")
                    if status == 404:
                        if url == GMAIL + "/history":
                            raise IntakeError("GMAIL_CURSOR_EXPIRED")
                        raise IntakeError("EMAIL_OR_ATTACHMENT_MISSING", review=True)
                    if status == 429 or status >= 500:
                        if attempt == 2:
                            raise IntakeError("GMAIL_TEMPORARILY_UNAVAILABLE", retryable=True)
                        try:
                            delay = min(
                                30.0,
                                max(1.0, float(response.headers.get("Retry-After", 2**attempt))),
                            )
                        except ValueError:
                            delay = float(2**attempt)
                        self.sleep(delay)
                        continue
                    if status != 200:
                        raise IntakeError("GMAIL_REQUEST_REFUSED")
                    return _read_limited(response, limit)
            except httpx.TransportError:
                if attempt == 2:
                    raise IntakeError("GMAIL_CONNECTION_FAILED", retryable=True) from None
                self.sleep(float(2**attempt))
        raise IntakeError("GMAIL_TEMPORARILY_UNAVAILABLE", retryable=True)

    def _access_token(self) -> str:
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        secret = self.settings.vendor_mail_gmail_client_secret
        refresh = self.settings.vendor_mail_gmail_refresh_token
        assert secret is not None and refresh is not None
        value = _json(
            self._request(
                TOKEN_URL,
                limit=65536,
                token_request=True,
                data={
                    "client_id": self.settings.vendor_mail_gmail_client_id,
                    "client_secret": secret.get_secret_value(),
                    "refresh_token": refresh.get_secret_value(),
                    "grant_type": "refresh_token",
                },
            )
        )
        token = value.get("access_token")
        if not isinstance(token, str) or not token:
            raise IntakeError("MAILBOX_TOKEN_INVALID")
        # If Google reports scopes, refuse a different or broader grant.
        if "scope" in value and set(str(value["scope"]).split()) != {READ_SCOPE}:
            raise IntakeError("GMAIL_READONLY_SCOPE_REQUIRED")
        self.token = token
        try:
            lifetime = max(0, int(value.get("expires_in", 300)) - 60)
        except (ValueError, TypeError):
            lifetime = 0
        self.expires_at = time.monotonic() + lifetime
        return token

    def _get(self, path: str, *, limit: int = 2 * 1024 * 1024, **params: Any) -> dict[str, Any]:
        return _json(
            self._request(
                GMAIL + path,
                limit=limit,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
        )

    def _verify_mailbox(self) -> str:
        if self.profile_history is None:
            value = self._get("/profile")
            email = value.get("emailAddress")
            if (
                not isinstance(email, str)
                or email.lower() != (self.settings.vendor_mail_mailbox or "").lower()
            ):
                raise IntakeError("GMAIL_MAILBOX_MISMATCH")
            self.profile_history = _history_id(value.get("historyId"))
        return self.profile_history

    def _payload(self, message_id: str) -> dict[str, Any]:
        self._verify_mailbox()
        identifier = _identifier(message_id)
        if message_id in self.cache:
            self.cache.move_to_end(message_id)
            return self.cache[message_id][0]
        raw = self._request(
            f"{GMAIL}/messages/{identifier}",
            limit=MAX_MESSAGE_BYTES,
            params={"format": "full"},
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        value = _json(raw)
        if value.get("id") != message_id or not isinstance(value.get("payload"), dict):
            raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
        self.cache[message_id] = value, len(raw)
        self.cache_bytes += len(raw)
        while len(self.cache) > 50 or self.cache_bytes > MAX_MESSAGE_BYTES:
            _, (_, size) = self.cache.popitem(last=False)
            self.cache_bytes -= size
        return value

    def message(self, message_id: str) -> MailMessage:
        value = self._payload(message_id)
        envelope = _envelope(value, message_id)
        body: list[str] = []
        body_bytes = 0
        has_attachments = False
        for path, part in _parts(value["payload"]):
            attachment = _attachment(path, part)
            if attachment:
                has_attachments = has_attachments or (attachment.is_file and not attachment.inline)
                continue
            if part.get("mimeType") not in ("text/plain", "text/html"):
                continue
            part_body = part.get("body", {})
            if not isinstance(part_body, dict):
                raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
            raw = self._body_data(message_id, part_body, MAX_BODY_BYTES - body_bytes)
            body_bytes += len(raw)
            content_type = Message()
            content_type["content-type"] = _headers(part).get("content-type", [""])[0]
            charset = content_type.get_content_charset() or "utf-8"
            try:
                body.append(raw.decode(charset, errors="replace"))
            except LookupError:
                body.append(raw.decode("utf-8", errors="replace"))
        return MailMessage(
            message_id,
            envelope.sender,
            envelope.subject,
            envelope.received_at,
            has_attachments,
            "\n".join(body),
        )

    def _messages(self, items: Any, since: datetime) -> tuple[MailMessage, ...]:
        if not isinstance(items, list) or len(items) > 500:
            raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
        messages: list[MailMessage] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
            identifier = _identifier(item.get("id"))
            if identifier in seen:
                continue
            seen.add(identifier)
            try:
                value = self._get(
                    f"/messages/{identifier}",
                    format="metadata",
                    metadataHeaders=["From", "Subject"],
                )
                labels = value.get("labelIds", [])
                if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
                    raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
                label = self.settings.vendor_mail_gmail_label_id
                if EXCLUDED_LABELS.intersection(labels) or (label and label not in labels):
                    continue
                message = _envelope(value, identifier)
                if message.received_at >= since:
                    messages.append(message)
            except IntakeError as error:
                if error.code != "EMAIL_OR_ATTACHMENT_MISSING":
                    raise
        return tuple(messages)

    def page(self, checkpoint: str | None, since: datetime) -> MailPage:
        anchor = self._verify_mailbox()  # validate owner before listing any messages
        if checkpoint is None:
            cursor = {"mode": "initial", "history": anchor}
        else:
            if len(checkpoint) > MAX_CURSOR_BYTES or not checkpoint.startswith("gmail:v1:"):
                raise IntakeError("GMAIL_CURSOR_INVALID")
            try:
                cursor = _json(checkpoint[len("gmail:v1:") :].encode())
            except IntakeError:
                raise IntakeError("GMAIL_CURSOR_INVALID") from None
        mode = cursor.get("mode")
        if mode not in ("initial", "history"):
            raise IntakeError("GMAIL_CURSOR_INVALID")
        history = _history_id(cursor.get("history"))
        page_token = cursor.get("page", "")
        if not isinstance(page_token, str) or len(page_token) > 4096:
            raise IntakeError("GMAIL_CURSOR_INVALID")
        params: dict[str, Any] = {"maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        label = self.settings.vendor_mail_gmail_label_id
        if mode == "initial":
            params["q"] = f"after:{int(since.timestamp()) - 1} -in:sent -in:drafts"
            if label:
                params["labelIds"] = label
            value = self._get("/messages", **params)
            items = value.get("messages", [])
        else:
            params.update(startHistoryId=history, historyTypes="messageAdded")
            if label:
                params["labelId"] = label
            value = self._get("/history", **params)
            records = value.get("history", [])
            if not isinstance(records, list) or len(records) > 500:
                raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
            items = []
            for record in records:
                if not isinstance(record, dict) or not isinstance(
                    record.get("messagesAdded", []), list
                ):
                    raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
                for entry in record.get("messagesAdded", []):
                    if not isinstance(entry, dict):
                        raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
                    items.append(entry.get("message"))
        next_page = value.get("nextPageToken")
        if next_page is not None and (
            not isinstance(next_page, str) or not next_page or len(next_page) > 4096
        ):
            raise IntakeError("GMAIL_RESPONSE_INVALID", retryable=True)
        if next_page:
            next_cursor = {"mode": mode, "history": history, "page": next_page}
        else:
            next_cursor = {
                "mode": "history",
                "history": history if mode == "initial" else _history_id(value.get("historyId")),
            }
        return MailPage(
            self._messages(items, since),
            "gmail:v1:" + json.dumps(next_cursor, separators=(",", ":")),
            bool(next_page),
        )

    def attachments(self, message_id: str) -> list[MailAttachment]:
        payload = self._payload(message_id)["payload"]
        return [
            attachment
            for path, part in _parts(payload)
            if (attachment := _attachment(path, part)) is not None
        ]

    def _body_data(self, message_id: str, body: dict[str, Any], max_bytes: int) -> bytes:
        size = body.get("size", 0)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise IntakeError("GMAIL_MESSAGE_INVALID", review=True)
        if size > max_bytes:
            raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
        external = body.get("attachmentId")
        if external:
            body = self._get(
                f"/messages/{_identifier(message_id)}/attachments/{_identifier(external)}",
                limit=((max_bytes + 2) // 3) * 4 + 65536,
            )
        return _decode(body.get("data", ""), max_bytes)

    def attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> bytes:
        for path, part in _parts(self._payload(message_id)["payload"]):
            if path != attachment_id:
                continue
            metadata = _attachment(path, part)
            if metadata is None or metadata.inline or not metadata.is_file:
                raise IntakeError("EMAIL_OR_ATTACHMENT_MISSING", review=True)
            if metadata.size > max_bytes:
                raise IntakeError("FILE_OR_MESSAGE_TOO_LARGE", review=True)
            return self._body_data(message_id, part["body"], max_bytes)
        raise IntakeError("EMAIL_OR_ATTACHMENT_MISSING", review=True)
