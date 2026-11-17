"""Immutable tenant-scoped financial imports and approval snapshots.

Revision ID: a61117202601
Revises: a61106202601
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a61117202601"
down_revision = "a61106202601"
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
        "financial_imports",
        *_common(),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("profile_sha256", sa.String(64), nullable=False),
        sa.Column("profile_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("report_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("marketplace_id", sa.String(64), nullable=False),
        sa.Column("store_name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "uploaded_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("linked_rows", sa.Integer(), nullable=False),
        sa.Column("review_rows", sa.Integer(), nullable=False),
        sa.Column(
            "purchasing_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "recording_transaction_id",
            sa.BigInteger(),
            server_default=sa.text("txid_current()"),
            nullable=False,
        ),
        sa.UniqueConstraint("id", "organization_id"),
        sa.UniqueConstraint("organization_id", "raw_sha256"),
        sa.CheckConstraint("period_end > period_start", name="positive_period"),
        sa.CheckConstraint("currency = 'USD'", name="supported_currency"),
        sa.CheckConstraint(
            "raw_sha256 ~ '^[0-9a-f]{64}$' and profile_sha256 ~ '^[0-9a-f]{64}$'",
            name="valid_digests",
        ),
        sa.CheckConstraint("status in ('RECORDED', 'REVIEW_REQUIRED')", name="valid_status"),
        sa.CheckConstraint(
            "row_count > 0 and linked_rows between 0 and row_count and review_rows between 0 and row_count",
            name="valid_counters",
        ),
        sa.CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )
    op.create_table(
        "financial_import_rows",
        *_common(),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("seller_sku", sa.Text()),
        sa.Column("row_kind", sa.String(16), nullable=False),
        sa.Column("mapping_status", sa.String(32), nullable=False),
        sa.Column(
            "listing_id", sa.UUID(), sa.ForeignKey("marketplace_listings.id", ondelete="RESTRICT")
        ),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="RESTRICT")),
        sa.Column("match_priority", sa.Integer()),
        sa.Column("mapping_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("source_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("review_reasons", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column(
            "purchasing_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        *(
            sa.Column(name, sa.Numeric(38, 16))
            for name in (
                "net_sales",
                "fees",
                "cost",
                "ad_spend",
                "reported_profit",
                "computed_profit",
            )
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["financial_imports.id", "financial_imports.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("import_id", "source_row_number"),
        sa.CheckConstraint("source_row_number > 1", name="positive_source_row"),
        sa.CheckConstraint(
            "mapping_status in ('LINKED', 'UNRESOLVED', 'UNALLOCATED', 'INVALID_SOURCE', 'ASIN_CONFLICT')",
            name="valid_mapping_status",
        ),
        sa.CheckConstraint(
            "(row_kind = 'UNALLOCATED' and seller_sku is null and mapping_status = 'UNALLOCATED') or (row_kind = 'SKU' and mapping_status <> 'UNALLOCATED')",
            name="valid_row_kind",
        ),
        sa.CheckConstraint(
            "(mapping_status = 'LINKED' and product_id is not null and listing_id is not null and match_priority is not null and match_priority = 4) or (mapping_status <> 'LINKED' and product_id is null and listing_id is null and match_priority is null)",
            name="approved_links_only",
        ),
        sa.CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )
    for table in ("financial_imports", "financial_import_rows"):
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    op.create_index(
        "ix_financial_imports_period",
        "financial_imports",
        ["organization_id", "marketplace_id", "period_start", "period_end"],
    )
    op.create_index(
        "ix_financial_import_rows_review",
        "financial_import_rows",
        ["organization_id", "import_id", "review_required", "source_row_number"],
    )
    for key in ("product_id", "listing_id"):
        op.create_index(f"ix_financial_import_rows_{key}", "financial_import_rows", [key])
    op.execute("""
        CREATE FUNCTION financial_snapshot_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Financial snapshots are append-only';
        END $$;
        CREATE FUNCTION financial_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || NEW.marketplace_id, 0));
            IF NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.uploaded_by_user_id AND organization_id = NEW.organization_id) THEN
                RAISE EXCEPTION 'Financial uploader must belong to the organization';
            END IF;
            IF EXISTS (SELECT 1 FROM financial_imports WHERE organization_id = NEW.organization_id AND marketplace_id = NEW.marketplace_id AND period_start < NEW.period_end AND period_end > NEW.period_start) THEN
                RAISE EXCEPTION 'Financial reporting periods must not overlap';
            END IF;
            NEW.recording_transaction_id := txid_current();
            RETURN NEW;
        END $$;
        CREATE FUNCTION financial_import_row_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner financial_imports%ROWTYPE;
        BEGIN
            SELECT * INTO owner FROM financial_imports WHERE id = NEW.import_id AND organization_id = NEW.organization_id;
            IF NOT FOUND OR owner.recording_transaction_id <> txid_current() THEN
                RAISE EXCEPTION 'Financial rows must be recorded with their own import transaction';
            END IF;
            IF NEW.mapping_status = 'LINKED' AND NOT EXISTS (
                SELECT 1 FROM marketplace_listings l JOIN products p ON p.id = l.product_id
                WHERE l.id = NEW.listing_id AND p.id = NEW.product_id
                  AND l.organization_id = NEW.organization_id AND p.organization_id = NEW.organization_id
                  AND l.marketplace = 'AMAZON' AND l.marketplace_id = owner.marketplace_id
                  AND l.seller_sku = NEW.seller_sku AND l.mapping_status = 'APPROVED'
                  AND l.is_active AND p.is_active AND p.status = 'ACTIVE'
            ) THEN RAISE EXCEPTION 'Financial links require an approved mapping in the organization';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION financial_import_check_counts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE all_rows integer; linked integer; review integer;
        BEGIN
            SELECT count(*), count(*) FILTER (WHERE mapping_status = 'LINKED'), count(*) FILTER (WHERE review_required)
              INTO all_rows, linked, review FROM financial_import_rows
              WHERE import_id = NEW.id AND organization_id = NEW.organization_id;
            IF all_rows <> NEW.row_count OR linked <> NEW.linked_rows OR review <> NEW.review_rows THEN
                RAISE EXCEPTION 'Financial import counters must match the recorded rows';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER financial_import_insert_guard BEFORE INSERT ON financial_imports FOR EACH ROW EXECUTE FUNCTION financial_import_guard();
        CREATE TRIGGER financial_row_insert_guard BEFORE INSERT ON financial_import_rows FOR EACH ROW EXECUTE FUNCTION financial_import_row_guard();
        CREATE CONSTRAINT TRIGGER financial_import_complete AFTER INSERT ON financial_imports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION financial_import_check_counts();
    """)
    for table in ("financial_imports", "financial_import_rows"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION financial_snapshot_reject_mutation()"
        )


def downgrade() -> None:
    for table, trigger in (
        ("financial_import_rows", "financial_row_insert_guard"),
        ("financial_import_rows", "financial_import_rows_immutable"),
        ("financial_imports", "financial_import_insert_guard"),
        ("financial_imports", "financial_import_complete"),
        ("financial_imports", "financial_imports_immutable"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for function in (
        "financial_import_check_counts",
        "financial_import_row_guard",
        "financial_import_guard",
        "financial_snapshot_reject_mutation",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_table("financial_import_rows")
    op.drop_table("financial_imports")
