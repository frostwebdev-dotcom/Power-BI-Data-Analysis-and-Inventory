from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class IntakeError(Exception):
    """Safe codes only: never include a message body, token or signed URL."""

    def __init__(self, code: str, *, retryable: bool = False, review: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        self.review = review
        super().__init__(code)


@dataclass(frozen=True)
class MailMessage:
    id: str
    sender: str
    subject: str
    received_at: datetime
    has_attachments: bool = False
    body: str = ""


@dataclass(frozen=True)
class MailAttachment:
    id: str
    name: str
    size: int
    inline: bool = False
    is_file: bool = True


@dataclass(frozen=True)
class MailPage:
    messages: tuple[MailMessage, ...]
    checkpoint: str
    more: bool


class MailReader(Protocol):
    def page(self, checkpoint: str | None, since: datetime) -> MailPage: ...
    def message(self, message_id: str) -> MailMessage: ...
    def attachments(self, message_id: str) -> list[MailAttachment]: ...
    def attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> bytes: ...
