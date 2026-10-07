"""Authentication request and response schemas."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AuthConfigurationResponse(BaseModel):
    """Public browser configuration. Never includes secrets or user data."""

    mode: Literal["dev", "entra", "disabled"]
    tenant_id: str | None = None
    client_id: str | None = None
    scope: str | None = None


class DevTokenRequest(BaseModel):
    """Request a development access token for an existing user.

    No password: the development backend is not a credential system. See
    docs/security.md for why, and for what replaces it.
    """

    # A plain string, not EmailStr: this is a lookup key against a row that
    # already exists, so RFC-correct validation would add a dependency and
    # reject nothing that matters.
    email: str = Field(
        min_length=3, max_length=320, description="Email of an existing, active user."
    )

    @field_validator("email")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    """An issued access token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")


class PrincipalResponse(BaseModel):
    """The authenticated actor, as the API sees them."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    display_name: str
    roles: list[str]
