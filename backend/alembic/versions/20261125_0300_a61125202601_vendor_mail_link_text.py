"""Optional inventory-download link text and button-image selectors.

Revision ID: a61125202601
Revises: a61118202601
"""

import sqlalchemy as sa
from alembic import op

revision = "a61125202601"
down_revision = "a61118202601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendor_mail_sources", sa.Column("download_link_text", sa.String(200)))
    op.add_column("vendor_mail_sources", sa.Column("download_link_image", sa.String(2000)))


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM vendor_mail_sources "
        "WHERE download_link_text IS NOT NULL OR download_link_image IS NOT NULL) THEN "
        "RAISE EXCEPTION 'Cannot discard configured download link selectors'; "
        "END IF; END $$"
    )
    op.drop_column("vendor_mail_sources", "download_link_text")
    op.drop_column("vendor_mail_sources", "download_link_image")
