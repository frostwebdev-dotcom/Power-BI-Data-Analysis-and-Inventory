"""Vendor request and response schemas (AC-4).

Validation here mirrors the database constraints so a bad payload is
rejected with a field-level 422 rather than a 500 from a check constraint:
codes are upper-case identifiers, currency is an ISO-4217 three-letter code,
emails look like addresses, quantities are never negative. The frontend
copies these rules field for field.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import VendorStatus

#: Upper-case letters, digits, hyphen and underscore; 2 to 32 characters.
CODE_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")
#: Deliberately simple: something@something.something. RFC-exact validation
#: adds a dependency and rejects nothing that matters here.
EMAIL_PATTERN: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")


def _normalize_code(value: str) -> str:
    code = value.strip().upper()
    if not CODE_PATTERN.match(code):
        raise ValueError("code must be 2-32 characters of letters, digits, hyphen or underscore")
    return code


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise ValueError("email must look like an address (name@domain.tld)")
    return email


def _normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if not CURRENCY_PATTERN.match(currency):
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return currency


def _not_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


# --- vendors --------------------------------------------------------------------------


class VendorBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: VendorStatus = VendorStatus.ACTIVE
    currency: str = "USD"
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    default_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    minimum_order_quantity: int | None = Field(default=None, ge=0)
    minimum_order_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    purchasing_terms: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return _normalize_currency(value)

    @field_validator("contact_email")
    @classmethod
    def _contact_email(cls, value: str | None) -> str | None:
        return _normalize_email(value) if value else None


class VendorCreate(VendorBase):
    code: str

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _normalize_code(value)


class VendorUpdate(BaseModel):
    """Partial update: only the fields present are changed.

    ``code`` is not here on purpose — it is the vendor's business key,
    referenced by import profiles and audit rows; changing it is a new vendor.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: VendorStatus | None = None
    currency: str | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    default_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    minimum_order_quantity: int | None = Field(default=None, ge=0)
    minimum_order_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    purchasing_terms: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _not_blank(value) if value is not None else None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return _normalize_currency(value) if value is not None else None

    @field_validator("contact_email")
    @classmethod
    def _contact_email(cls, value: str | None) -> str | None:
        return _normalize_email(value) if value else None


class VendorContactBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    role: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=64)
    is_primary: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)


class VendorContactCreate(VendorContactBase):
    pass


class VendorContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    role: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=64)
    is_primary: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _not_blank(value) if value is not None else None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _normalize_email(value) if value is not None else None


class VendorContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    name: str
    email: str
    role: str | None
    phone: str | None
    is_primary: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VendorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    code: str
    name: str
    status: VendorStatus
    currency: str
    timezone: str
    default_lead_time_days: int | None
    contact_email: str | None
    contact_phone: str | None
    notes: str | None
    minimum_order_quantity: int | None
    minimum_order_value: Decimal | None
    purchasing_terms: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VendorDetailResponse(VendorResponse):
    contacts: list[VendorContactResponse]


class VendorListResponse(BaseModel):
    items: list[VendorResponse]
    page: int
    page_size: int
    total: int
