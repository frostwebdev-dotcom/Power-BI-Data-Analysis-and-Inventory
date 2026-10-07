"""Users, roles, and role assignment.

These exist now because audit attribution and mapping approval both need a real
actor: an approved mapping is permanent and someone has to be accountable for it
(CLAUDE.md §5.2, §6). Microsoft Entra authenticates explicitly enrolled users
(ADR 0016). ``password_hash`` remains nullable and unused; Microsoft owns credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class User(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A person who can act in the system and be named in the audit trail."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(nullable=False)
    display_name: Mapped[str] = mapped_column(nullable=False)
    # Nullable until B2 settles how people authenticate. A null hash means the
    # account cannot log in locally, not that it has an empty password.
    password_hash: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    entra_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    entra_object_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")
    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        foreign_keys="UserRole.user_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Email is unique per tenant, not globally: the same person may hold an
        # account in more than one organization.
        Index("uq_users_organization_id_email", "organization_id", "email", unique=True),
        Index("uq_users_entra_identity", "entra_tenant_id", "entra_object_id", unique=True),
        CheckConstraint(
            "(entra_tenant_id IS NULL) = (entra_object_id IS NULL)",
            name="entra_identity_complete",
        ),
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint("position('@' in email) > 1", name="email_looks_like_an_address"),
    )


class Role(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """A named set of permissions within one organization.

    Roles are tenant-scoped rather than global so an organization can add its
    own without affecting anyone else. ``is_system`` marks the seeded roles that
    the application relies on and must not be deleted.
    """

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    organization: Mapped[Organization] = relationship(back_populates="roles")
    user_roles: Mapped[list[UserRole]] = relationship(back_populates="role")

    __table_args__ = (
        Index("uq_roles_organization_id_code", "organization_id", "code", unique=True),
        CheckConstraint("code = upper(code)", name="code_is_uppercase"),
    )


class UserRole(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Assignment of a role to a user, with provenance.

    ``granted_by_user_id`` records who conferred the privilege, which is part of
    the audit story for approval rights.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    user: Mapped[User] = relationship(back_populates="user_roles", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="user_roles")
    granted_by: Mapped[User | None] = relationship(foreign_keys=[granted_by_user_id])

    __table_args__ = (
        Index("uq_user_roles_user_id_role_id", "user_id", "role_id", unique=True),
        Index("ix_user_roles_role_id", "role_id"),
    )
