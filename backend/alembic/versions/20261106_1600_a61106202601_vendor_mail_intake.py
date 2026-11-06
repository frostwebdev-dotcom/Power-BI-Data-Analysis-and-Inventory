"""Durable, organization-scoped vendor email inventory intake.

Revision ID: a61106202601
Revises: a61105202601
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a61106202601"
down_revision = "a61105202601"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "vendor_mail_sources",
        *_common(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "vendor_id", sa.UUID(), sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "profile_id",
            sa.UUID(),
            sa.ForeignKey("vendor_import_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sender_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("subject_contains", sa.String(200)),
        sa.Column("filename_pattern", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("download_hosts", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("download_path_prefix", sa.String(500)),
        sa.Column("download_filename", sa.String(200)),
        sa.Column("stale_after_hours", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_received_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("mode in ('ATTACHMENT', 'LINK')", name="valid_mode"),
        sa.CheckConstraint(
            "stale_after_hours is null or stale_after_hours > 0", name="positive_cadence"
        ),
    )
    op.create_index(
        "uq_vendor_mail_sources_org_name",
        "vendor_mail_sources",
        ["organization_id", "name"],
        unique=True,
    )
    op.create_table(
        "vendor_mail_states",
        *_common(),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("checkpoint_url", sa.Text()),
        sa.Column("initial_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
    )
    op.create_index(
        "uq_vendor_mail_states_org_account",
        "vendor_mail_states",
        ["organization_id", "account_key"],
        unique=True,
    )
    op.create_table(
        "vendor_mail_deliveries",
        *_common(),
        sa.Column(
            "state_id",
            sa.UUID(),
            sa.ForeignKey("vendor_mail_states.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.UUID(),
            sa.ForeignKey("vendor_mail_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.UUID(),
            sa.ForeignKey("vendor_import_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("attachment_id", sa.Text()),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("import_job_id", sa.UUID(), sa.ForeignKey("import_jobs.id", ondelete="RESTRICT")),
        sa.CheckConstraint(
            "status in ('PENDING', 'RUNNING', 'COMPLETED', 'DUPLICATE', 'FAILED', 'NEEDS_REVIEW', 'SUPERSEDED')",
            name="valid_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )
    op.create_index(
        "uq_vendor_mail_deliveries_identity",
        "vendor_mail_deliveries",
        ["state_id", "source_id", "message_id", "item_key"],
        unique=True,
    )
    op.create_index(
        "ix_vendor_mail_deliveries_queue",
        "vendor_mail_deliveries",
        ["organization_id", "status", "received_at"],
    )
    for table in ("vendor_mail_sources", "vendor_mail_states", "vendor_mail_deliveries"):
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])


def downgrade() -> None:
    # Avoid dropping a new intake trail accidentally during a production rollback.
    op.execute("""do $$ begin
        if exists(select 1 from vendor_mail_deliveries) then
            raise exception 'Cannot downgrade: vendor email delivery history exists';
        end if;
    end $$;""")
    op.drop_table("vendor_mail_deliveries")
    op.drop_table("vendor_mail_states")
    op.drop_table("vendor_mail_sources")
