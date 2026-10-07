"""Entra validation with real RSA signatures, without Microsoft network access."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.security import EntraIdAuthenticationBackend, Principal, RoleCode
from app.main import create_app

TENANT = uuid.UUID("17bbcdfa-a0c5-4dfe-a851-0c7459906680")
API = uuid.UUID("11111111-1111-4111-8111-111111111111")
SPA = uuid.UUID("22222222-2222-4222-8222-222222222222")
OBJECT = uuid.UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture
def entra_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="staging",
        auth_backend="entra",
        dev_auth_enabled=False,
        entra_tenant_id=TENANT,
        entra_api_client_id=API,
        entra_spa_client_id=SPA,
    )


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key: rsa.RSAPrivateKey, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": str(API),
        "sub": "opaque-subject",
        "tid": str(TENANT),
        "oid": str(OBJECT),
        "azp": str(SPA),
        "ver": "2.0",
        "scp": "access_as_user",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "email": "untrusted@example.test",
        "roles": ["ADMIN"],
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def mock_keys(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> MagicMock:
    keys = MagicMock()
    keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=signing_key.public_key())
    monkeypatch.setattr(security, "_entra_jwks_client", lambda tenant: keys)
    return keys


def test_valid_token_uses_identity_and_database_roles(
    monkeypatch: pytest.MonkeyPatch,
    entra_settings: Settings,
    signing_key: rsa.RSAPrivateKey,
    mock_keys: MagicMock,
) -> None:
    session = MagicMock(spec=Session)
    user_id = uuid.uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id=user_id)
    principal = Principal(
        user_id, uuid.uuid4(), "local@example.test", "User", frozenset({RoleCode.VIEWER})
    )
    load = MagicMock(return_value=principal)
    monkeypatch.setattr(security, "load_principal", load)
    assert (
        EntraIdAuthenticationBackend(entra_settings).authenticate(token(signing_key), session)
        == principal
    )
    parameters = session.execute.call_args.args[0].compile().params
    assert TENANT in parameters.values() and OBJECT in parameters.values()
    assert "untrusted@example.test" not in parameters.values()
    load.assert_called_once_with(session, user_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": str(SPA)},
        {"aud": [str(API)]},
        {"iss": "https://attacker.test"},
        {"tid": str(uuid.uuid4())},
        {"azp": str(uuid.uuid4())},
        {"ver": "1.0"},
        {"scp": "other_scope"},
        {"scp": ["access_as_user"]},
        {"idtyp": "app"},
        {"oid": "not-a-uuid"},
        {"oid": None},
        {"scp": None},
        {"exp": datetime.now(UTC) - timedelta(minutes=5)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=5)},
        {"iat": datetime.now(UTC) + timedelta(minutes=5)},
    ],
)
def test_invalid_tokens_never_touch_database(
    entra_settings: Settings,
    signing_key: rsa.RSAPrivateKey,
    mock_keys: MagicMock,
    overrides: dict[str, object],
) -> None:
    session = MagicMock(spec=Session)
    with pytest.raises(AuthenticationError):
        EntraIdAuthenticationBackend(entra_settings).authenticate(
            token(signing_key, **overrides), session
        )
    session.execute.assert_not_called()


def test_wrong_signature_is_rejected(entra_settings: Settings, mock_keys: MagicMock) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session = MagicMock(spec=Session)
    with pytest.raises(AuthenticationError):
        EntraIdAuthenticationBackend(entra_settings).authenticate(token(other), session)
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "credential", ["malformed", jwt.encode({"sub": "user"}, "a" * 32, algorithm="HS256")]
)
def test_wrong_algorithm_does_not_fetch_keys(
    entra_settings: Settings,
    mock_keys: MagicMock,
    credential: str,
) -> None:
    with pytest.raises(AuthenticationError):
        EntraIdAuthenticationBackend(entra_settings).authenticate(
            credential, MagicMock(spec=Session)
        )
    mock_keys.get_signing_key_from_jwt.assert_not_called()


def test_key_failure_fails_closed(
    entra_settings: Settings,
    signing_key: rsa.RSAPrivateKey,
    mock_keys: MagicMock,
) -> None:
    mock_keys.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientConnectionError("unavailable")
    with pytest.raises(AuthenticationError):
        EntraIdAuthenticationBackend(entra_settings).authenticate(
            token(signing_key), MagicMock(spec=Session)
        )


def test_unenrolled_identity_is_refused(
    entra_settings: Settings,
    signing_key: rsa.RSAPrivateKey,
    mock_keys: MagicMock,
) -> None:
    session = MagicMock(spec=Session)
    session.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(AuthenticationError, match="not been granted"):
        EntraIdAuthenticationBackend(entra_settings).authenticate(token(signing_key), session)


def test_public_configuration_and_dev_lock(entra_settings: Settings) -> None:
    with TestClient(create_app(entra_settings)) as client:
        assert client.get("/api/v1/auth/config").json() == {
            "mode": "entra",
            "tenant_id": str(TENANT),
            "client_id": str(SPA),
            "scope": f"api://{API}/access_as_user",
        }
        assert (
            client.post("/api/v1/auth/dev-token", json={"email": "admin@example.test"}).status_code
            == 404
        )


def test_staging_refuses_development_login() -> None:
    with pytest.raises(ValidationError, match="DEV_AUTH_ENABLED"):
        Settings(_env_file=None, app_env="staging", dev_auth_enabled=True)
    settings = Settings(_env_file=None, app_env="staging", dev_auth_enabled=False)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/auth/config").json()["mode"] == "disabled"
    with pytest.raises(AuthenticationError):
        security.DevJwtAuthenticationBackend(settings).authenticate(
            "any-token", MagicMock(spec=Session)
        )


def test_incomplete_configuration_is_refused() -> None:
    with pytest.raises(ValidationError, match="Entra requires"):
        Settings(_env_file=None, auth_backend="entra")


def test_shared_api_browser_registration_is_refused() -> None:
    with pytest.raises(ValidationError, match="separate"):
        Settings(
            _env_file=None,
            auth_backend="entra",
            entra_tenant_id=TENANT,
            entra_api_client_id=API,
            entra_spa_client_id=API,
        )
