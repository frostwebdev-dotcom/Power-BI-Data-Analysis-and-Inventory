"""Entra enrollment, tenant isolation and account revocation against PostgreSQL."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.cli.enroll_entra_user import enroll
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.security import RoleCode, load_principal
from app.db.session import get_db
from app.main import create_app
from app.models import AuditEvent, User, UserRole
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories
from tests.unit.test_entra_auth import API, OBJECT, SPA, TENANT, token

pytestmark = pytest.mark.integration


def enroll_viewer(session: Session, slug: str, *, object_id: uuid.UUID = OBJECT) -> User:
    return enroll(
        session,
        org_slug=slug,
        tenant_id=TENANT,
        object_id=object_id,
        email="buyer@example.test",
        display_name="Buyer",
        role=RoleCode.VIEWER,
        operator_label="approved-test-operator",
    )


def test_enrollment_is_idempotent_and_audited(db_session: Session) -> None:
    org = factories.make_organization(db_session)
    first = enroll_viewer(db_session, org.slug)
    second = enroll_viewer(db_session, org.slug)
    assert first.id == second.id
    assert (
        db_session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.user_id == first.id)
        )
        == 1
    )
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == first.id)))
    assert len(events) == 2
    assert all(event.actor_label == "approved-test-operator" for event in events)
    assert first.password_hash is None


def test_identity_cannot_be_bound_to_another_organization(db_session: Session) -> None:
    first_org = factories.make_organization(db_session)
    second_org = factories.make_organization(db_session)
    enroll_viewer(db_session, first_org.slug)
    with pytest.raises(ValueError, match="already belongs"):
        enroll_viewer(db_session, second_org.slug)


def test_existing_identity_is_not_overwritten(db_session: Session) -> None:
    org = factories.make_organization(db_session)
    enroll_viewer(db_session, org.slug)
    with pytest.raises(ValueError, match="another Entra identity"):
        enroll_viewer(db_session, org.slug, object_id=uuid.uuid4())


def test_enrollment_does_not_reactivate_user(db_session: Session) -> None:
    org = factories.make_organization(db_session)
    user = enroll_viewer(db_session, org.slug)
    user.is_active = False
    db_session.flush()
    with pytest.raises(ValueError, match="inactive"):
        enroll_viewer(db_session, org.slug)


def test_principal_rejects_inactive_organization(db_session: Session) -> None:
    org = factories.make_organization(db_session)
    user = enroll_viewer(db_session, org.slug)
    org.is_active = False
    db_session.flush()
    with pytest.raises(AuthenticationError):
        load_principal(db_session, user.id)


def test_role_from_another_organization_is_never_effective(db_session: Session) -> None:
    first_org = factories.make_organization(db_session)
    second_org = factories.make_organization(db_session)
    user = enroll_viewer(db_session, first_org.slug)
    roles = ensure_system_roles(db_session, second_org.id)
    assign_role(db_session, user=user, role=roles[RoleCode.ADMIN])
    # Even a malformed legacy assignment must not confer another tenant's role.
    assert load_principal(db_session, user.id).roles == frozenset({RoleCode.VIEWER})


def test_signed_token_works_then_role_and_user_revocations_take_effect(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import security

    org = factories.make_organization(db_session)
    user = enroll_viewer(db_session, org.slug)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = MagicMock()
    keys.get_signing_key_from_jwt.return_value.key = key.public_key()
    monkeypatch.setattr(security, "_entra_jwks_client", lambda tenant: keys)
    settings = Settings(
        _env_file=None,
        app_env="staging",
        auth_backend="entra",
        dev_auth_enabled=False,
        entra_tenant_id=TENANT,
        entra_api_client_id=API,
        entra_spa_client_id=SPA,
    )
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: db_session
    headers = {"Authorization": f"Bearer {token(key)}"}
    with TestClient(app) as client:
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["roles"] == ["VIEWER"]  # The token's ADMIN claim is ignored.
        assert me.json()["organization_id"] == str(org.id)
        db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        db_session.flush()
        assert client.get("/api/v1/auth/me", headers=headers).json()["roles"] == []
        user.is_active = False
        db_session.flush()
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
