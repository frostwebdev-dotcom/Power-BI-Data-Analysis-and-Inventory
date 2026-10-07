"""Explicitly enroll an Entra identity; never authenticate by email claims."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import RoleCode
from app.db.transaction import session_scope
from app.models import Organization, User
from app.models.enums import ActorType
from app.services import audit
from app.services.roles import assign_role, ensure_system_roles


def enroll(
    session: Session,
    *,
    org_slug: str,
    tenant_id: uuid.UUID,
    object_id: uuid.UUID,
    email: str,
    display_name: str,
    role: RoleCode,
    operator_label: str,
) -> User:
    organization = session.execute(
        select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
    ).scalar_one_or_none()
    if organization is None:
        raise ValueError("An active organization with that slug is required.")
    email = email.strip().lower()
    if (
        len(email) > 320
        or email.find("@") < 1
        or not display_name.strip()
        or not operator_label.strip()
    ):
        raise ValueError("Valid email, display name and operator label are required.")
    bound = session.execute(
        select(User).where(User.entra_tenant_id == tenant_id, User.entra_object_id == object_id)
    ).scalar_one_or_none()
    user = session.execute(
        select(User).where(User.organization_id == organization.id, User.email == email)
    ).scalar_one_or_none()
    if bound is not None and (user is None or bound.id != user.id):
        raise ValueError("That Entra identity already belongs to another account.")
    before = audit.snapshot(user) if user is not None else None
    if user is None:
        user = User(organization_id=organization.id, email=email, display_name=display_name.strip())
        session.add(user)
    elif not user.is_active:
        raise ValueError("The existing account is inactive; enrollment does not reactivate it.")
    elif user.entra_object_id is not None and (
        user.entra_tenant_id != tenant_id or user.entra_object_id != object_id
    ):
        raise ValueError("The existing account is bound to another Entra identity.")
    user.entra_tenant_id = tenant_id
    user.entra_object_id = object_id
    session.flush()
    roles = ensure_system_roles(session, organization.id)
    assignment = assign_role(session, user=user, role=roles[role])
    audit.record(
        session,
        organization_id=organization.id,
        action="auth.entra_user_enrolled",
        entity_type="users",
        entity_id=user.id,
        actor_type=ActorType.SYSTEM,
        actor_label=operator_label.strip(),
        before=before,
        after={
            **audit.snapshot(user),
            "granted_role": role.value,
            "assignment_id": str(assignment.id),
        },
        summary="Operator enrolled an Entra identity and ensured the requested role.",
    )
    return user


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--object-id", required=True, type=uuid.UUID)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", required=True, choices=[role.value for role in RoleCode])
    parser.add_argument(
        "--operator-label", required=True, help="Person performing this approved grant."
    )
    arguments = parser.parse_args(argv)
    settings = get_settings()
    if settings.entra_tenant_id is None or arguments.tenant_id != settings.entra_tenant_id:
        parser.error("--tenant-id must match configured ENTRA_TENANT_ID")
    try:
        with session_scope() as session:
            user = enroll(
                session,
                org_slug=arguments.org_slug,
                tenant_id=arguments.tenant_id,
                object_id=arguments.object_id,
                email=arguments.email,
                display_name=arguments.display_name,
                role=RoleCode(arguments.role),
                operator_label=arguments.operator_label,
            )
            user_id = user.id
    except ValueError as exc:
        print(f"Enrollment refused: {exc}", file=sys.stderr)
        return 1
    print(f"Entra account enrolled: {user_id}; role: {arguments.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
