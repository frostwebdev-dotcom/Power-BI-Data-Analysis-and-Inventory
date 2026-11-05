"""Scope new Excel jobs by resolved worksheet, retaining one original file.

Revision ID: a61105202601
Revises: 9c1520261015
"""

import sqlalchemy as sa
from alembic import op

revision = "a61105202601"
down_revision = "9c1520261015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("source_sheet", sa.String(255), nullable=True))
    op.drop_index("uq_import_jobs_active_import_file", table_name="import_jobs")
    op.create_index(
        "uq_import_jobs_active_import_file",
        "import_jobs",
        ["organization_id", "import_file_id", sa.text("coalesce(source_sheet, '')")],
        unique=True,
        postgresql_where=sa.text("status not in ('FAILED', 'CANCELLED')"),
    )


def downgrade() -> None:
    # Refuse a lossy rollback once several sheets of a workbook are imported.
    # Never delete successful history to make the old unique index fit.
    op.execute("""
        do $$ begin
            if exists (
                select 1 from import_jobs
                where status not in ('FAILED', 'CANCELLED')
                group by organization_id, import_file_id having count(*) > 1
            ) then
                raise exception 'Cannot downgrade: multiple worksheet jobs retain one file';
            end if;
        end $$;
    """)
    op.drop_index("uq_import_jobs_active_import_file", table_name="import_jobs")
    op.create_index(
        "uq_import_jobs_active_import_file",
        "import_jobs",
        ["organization_id", "import_file_id"],
        unique=True,
        postgresql_where=sa.text("status not in ('FAILED', 'CANCELLED')"),
    )
    op.drop_column("import_jobs", "source_sheet")
