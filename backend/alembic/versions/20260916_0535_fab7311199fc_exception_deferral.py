"""exception deferral

Revision ID: fab7311199fc
Revises: fadb756b1b85
Create Date: 2026-09-16 05:35:33.436187+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fab7311199fc"
down_revision: str | None = "fadb756b1b85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Hand-checked: a nullable TIMESTAMPTZ and a partial index; nothing autogenerate
# gets wrong. No enum, no check constraint.


def upgrade() -> None:
    op.add_column(
        "product_mapping_exceptions",
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_product_mapping_exceptions_deferred_until",
        "product_mapping_exceptions",
        ["organization_id", "deferred_until"],
        unique=False,
        postgresql_where=sa.text("deferred_until is not null"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_mapping_exceptions_deferred_until",
        table_name="product_mapping_exceptions",
        postgresql_where=sa.text("deferred_until is not null"),
    )
    op.drop_column("product_mapping_exceptions", "deferred_until")
