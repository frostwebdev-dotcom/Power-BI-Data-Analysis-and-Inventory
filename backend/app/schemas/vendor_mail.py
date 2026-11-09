"""Public inventory-intake configuration. Never includes mailbox credentials."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MailSourceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    vendor_id: uuid.UUID
    profile_id: uuid.UUID
    sender_addresses: list[str] = Field(min_length=1, max_length=20)
    subject_contains: str | None = Field(default=None, max_length=200)
    filename_pattern: str = Field(default="*.xlsx", min_length=1, max_length=200)
    mode: Literal["ATTACHMENT", "LINK"] = "ATTACHMENT"
    download_hosts: list[str] = Field(default_factory=list, max_length=10)
    download_path_prefix: str | None = Field(default=None, max_length=500)
    download_filename: str | None = Field(default=None, max_length=200)
    stale_after_hours: int | None = Field(default=None, ge=1, le=87600)
    is_active: bool = False

    @field_validator("name", "filename_pattern")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("must not be blank or contain control characters")
        return value.strip()

    @field_validator("sender_addresses")
    @classmethod
    def exact_addresses(cls, values: list[str]) -> list[str]:
        addresses = sorted({x.strip().lower() for x in values})
        if any(
            len(x) > 254 or "*" in x or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", x)
            for x in addresses
        ):
            raise ValueError("use exact email addresses, not sender domains or wildcards")
        return addresses

    @field_validator("download_hosts")
    @classmethod
    def exact_hosts(cls, values: list[str]) -> list[str]:
        hosts = sorted({x.strip().lower() for x in values})
        if any(
            len(x) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", x) for x in hosts
        ):
            raise ValueError("use exact hostnames without URLs, ports or wildcards")
        return hosts

    @model_validator(mode="after")
    def link_rules(self) -> MailSourceWrite:
        if self.mode == "LINK":
            if not self.download_hosts or not self.download_filename:
                raise ValueError("a link source needs approved hosts and a download filename")
            prefix = self.download_path_prefix
            if not prefix or not prefix.startswith("/") or "?" in prefix or "#" in prefix:
                raise ValueError("a link source needs an explicit URL path prefix")
            name = self.download_filename
            if "/" in name or "\\" in name or not name.lower().endswith((".csv", ".xls", ".xlsx")):
                raise ValueError("download filename must be a plain .csv, .xls or .xlsx filename")
        return self


class MailSourceResponse(MailSourceWrite):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vendor_code: str
    profile_name: str
    profile_version: int
    starts_at: datetime
    last_received_at: datetime | None
    last_success_at: datetime | None
    freshness: Literal[
        "paused", "never_received", "awaiting_import", "current", "stale", "irregular"
    ]


class MailDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source_id: uuid.UUID
    profile_id: uuid.UUID
    filename: str
    received_at: datetime
    status: str
    attempts: int
    next_attempt_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    import_job_id: uuid.UUID | None


class MailRetry(BaseModel):
    use_current_profile: bool = False


class MailConnectionStatus(BaseModel):
    provider: Literal["microsoft365", "gmail"] = "microsoft365"
    enabled: bool
    configured: bool
    mailbox: str | None
    poll_minutes: int
    missing_settings: list[str]
    last_poll_at: datetime | None = None
    last_success_at: datetime | None = None
    error_code: str | None = None
