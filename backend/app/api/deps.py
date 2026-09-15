"""Shared route dependencies.

``get_current_principal`` is the single gate every protected route passes
through, and ``require_roles`` is how a route states which roles it needs. Both
depend only on :class:`~app.core.security.Principal`, so replacing the
authentication backend with Microsoft Entra ID changes nothing here.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.security import Principal, RoleCode, build_authentication_backend
from app.db.session import get_db
from app.imports.storage import StorageBackend

# auto_error=False so a missing header reaches our own handler and produces the
# standard error envelope, rather than FastAPI's bare {"detail": ...}.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="Bearer")


def get_app_settings(request: Request) -> Settings:
    """The settings this application was built with.

    Routes must not call ``get_settings()`` directly. That reads the
    process-wide cache, which can differ from what ``create_app`` was handed —
    so an app built for one configuration would answer according to another.
    Reading them off ``app.state`` makes the two impossible to disagree.
    """
    settings: Settings = request.app.state.settings
    return settings


def get_storage(request: Request) -> StorageBackend:
    """The storage backend this application was built with (ADR 0004)."""
    storage: StorageBackend = request.app.state.storage
    return storage


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Principal:
    """Resolve the authenticated actor, or reject the request.

    The credential-shape checks happen before any database access, so an
    unauthenticated request costs nothing and cannot be used to probe the
    database.
    """
    if credentials is None or not credentials.credentials.strip():
        raise AuthenticationError("Authentication is required.")

    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication is required.")

    backend = build_authentication_backend(settings)
    return backend.authenticate(credentials.credentials, session)


def require_roles(*roles: RoleCode) -> Callable[[Principal], Principal]:
    """Dependency factory gating a route on one of ``roles``.

        @router.post("/vendors", dependencies=[Depends(require_roles(RoleCode.DATA_OPERATOR))])

    ADMIN passes every check. Called with no roles it still requires
    authentication, which is the right default for a mutation endpoint.
    """

    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        principal.require_roles(roles)
        return principal

    return dependency


def _parse_ip(value: str | None) -> str | None:
    """Return ``value`` only if it is a real IP address.

    ``audit_events.ip_address`` is an ``INET`` column, so an unparseable value
    fails the INSERT and turns an ordinary request into a 500. Since
    ``X-Forwarded-For`` is caller-supplied text, that would be trivially
    triggerable from outside. An address we cannot parse is simply not recorded.
    """
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str | None:
    """Best-effort client address for audit rows.

    ``X-Forwarded-For`` is honoured because this service is expected to sit
    behind a proxy that sets it; exposed directly, the header is caller-supplied
    and worth nothing. Either way the value is validated before it is stored.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return _parse_ip(forwarded.split(",")[0])
    return _parse_ip(request.client.host) if request.client else None
