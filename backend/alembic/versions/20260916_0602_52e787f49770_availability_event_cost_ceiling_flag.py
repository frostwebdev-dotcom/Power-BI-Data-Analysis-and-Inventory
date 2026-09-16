"""availability event cost ceiling flag

Revision ID: 52e787f49770
Revises: fab7311199fc
Create Date: 2026-09-16 06:02:04.976842+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "52e787f49770"
down_revision: str | None = "fab7311199fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Hand-checked: one nullable boolean; nothing autogenerate gets wrong.


def upgrade() -> None:
    op.add_column(
        "availability_events", sa.Column("over_max_unit_cost", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("availability_events", "over_max_unit_cost")
