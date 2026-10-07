"""Explicit Microsoft Entra identity enrollment.

Revision ID: e71a20261007
Revises: 52e787f49770
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e71a20261007"
down_revision: str | None = "52e787f49770"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("entra_tenant_id", sa.Uuid(), nullable=True))
    op.add_column("users", sa.Column("entra_object_id", sa.Uuid(), nullable=True))
    op.create_index(
        "uq_users_entra_identity", "users", ["entra_tenant_id", "entra_object_id"], unique=True
    )
    op.create_check_constraint(
        "entra_identity_complete",
        "users",
        "(entra_tenant_id IS NULL) = (entra_object_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("entra_identity_complete", "users", type_="check")
    op.drop_index("uq_users_entra_identity", table_name="users")
    op.drop_column("users", "entra_object_id")
    op.drop_column("users", "entra_tenant_id")
