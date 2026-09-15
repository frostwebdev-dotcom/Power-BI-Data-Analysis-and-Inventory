"""import job current stage

Revision ID: fadb756b1b85
Revises: 44c932e601b0
Create Date: 2026-09-15 23:15:31.152010+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fadb756b1b85"
down_revision: str | None = "44c932e601b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Hand-checked. Autogenerate rendered the column with a bare sa.Enum, which
# does not create the PostgreSQL type on add_column (only create_table does),
# so the type is created and dropped explicitly. The enum is new in this
# revision and is the only thing its downgrade drops.
# ---------------------------------------------------------------------------

IMPORT_JOB_STAGE = postgresql.ENUM(
    "PARSING", "MATCHING", "SNAPSHOTTING", name="import_job_stage", create_type=False
)


def upgrade() -> None:
    IMPORT_JOB_STAGE.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "import_jobs",
        sa.Column("current_stage", IMPORT_JOB_STAGE, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "current_stage")
    IMPORT_JOB_STAGE.drop(op.get_bind(), checkfirst=True)
