"""Append-only reviewed revisions of financial SKU links.

Revision ID: a61117202602
Revises: a61117202601
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a61117202602"
down_revision = "a61117202601"
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
    op.create_unique_constraint(
        "uq_financial_import_rows_id_import_id_organization_id",
        "financial_import_rows",
        ["id", "import_id", "organization_id"],
    )
    op.create_table(
        "financial_binding_revisions",
        *_common(),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_revision_id", sa.UUID()),
        sa.Column(
            "recorded_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("bindings_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in ("row_count", "linked_rows", "review_rows", "changed_rows")
        ),
        sa.Column(
            "purchasing_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "recording_transaction_id",
            sa.BigInteger(),
            server_default=sa.text("txid_current()"),
            nullable=False,
        ),
        sa.UniqueConstraint("id", "import_id", "organization_id"),
        sa.UniqueConstraint("import_id", "revision_number"),
        sa.UniqueConstraint("supersedes_revision_id"),
        sa.ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["financial_imports.id", "financial_imports.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_revision_id", "import_id", "organization_id"],
            [
                "financial_binding_revisions.id",
                "financial_binding_revisions.import_id",
                "financial_binding_revisions.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision_number > 0", name="positive_revision"),
        sa.CheckConstraint("length(btrim(reason)) between 10 and 1000", name="reason_required"),
        sa.CheckConstraint("bindings_sha256 ~ '^[0-9a-f]{64}$'", name="valid_digest"),
        sa.CheckConstraint("status in ('RECORDED', 'REVIEW_REQUIRED')", name="valid_status"),
        sa.CheckConstraint(
            "row_count > 0 and linked_rows between 0 and row_count and review_rows between 0 and row_count and changed_rows between 1 and row_count",
            name="valid_counters",
        ),
        sa.CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )
    op.create_table(
        "financial_binding_rows",
        *_common(),
        sa.Column("revision_id", sa.UUID(), nullable=False),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("source_row_id", sa.UUID(), nullable=False),
        sa.Column("mapping_status", sa.String(32), nullable=False),
        sa.Column(
            "listing_id", sa.UUID(), sa.ForeignKey("marketplace_listings.id", ondelete="RESTRICT")
        ),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="RESTRICT")),
        sa.Column("match_priority", sa.Integer()),
        sa.Column("mapping_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("review_reasons", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column(
            "purchasing_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "import_id", "organization_id"],
            [
                "financial_binding_revisions.id",
                "financial_binding_revisions.import_id",
                "financial_binding_revisions.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_row_id", "import_id", "organization_id"],
            [
                "financial_import_rows.id",
                "financial_import_rows.import_id",
                "financial_import_rows.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("revision_id", "source_row_id"),
        sa.CheckConstraint(
            "mapping_status in ('LINKED', 'UNRESOLVED', 'UNALLOCATED', 'INVALID_SOURCE', 'ASIN_CONFLICT')",
            name="valid_mapping_status",
        ),
        sa.CheckConstraint(
            "(mapping_status = 'LINKED' and product_id is not null and listing_id is not null and match_priority is not null and match_priority = 4) or (mapping_status <> 'LINKED' and product_id is null and listing_id is null and match_priority is null)",
            name="approved_links_only",
        ),
        sa.CheckConstraint("not purchasing_eligible", name="not_purchasing_input"),
    )
    for table in ("financial_binding_revisions", "financial_binding_rows"):
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    op.create_index(
        "ix_financial_binding_rows_review",
        "financial_binding_rows",
        ["organization_id", "revision_id", "review_required"],
    )
    op.execute("""
        CREATE FUNCTION financial_binding_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source financial_imports%ROWTYPE; head financial_binding_revisions%ROWTYPE;
        BEGIN
            SELECT * INTO source FROM financial_imports WHERE id = NEW.import_id AND organization_id = NEW.organization_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'Financial revision source must belong to the organization'; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || source.marketplace_id, 0));
            IF NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.recorded_by_user_id AND organization_id = NEW.organization_id AND is_active) THEN
                RAISE EXCEPTION 'Financial revision actor must be active in the organization';
            END IF;
            SELECT * INTO head FROM financial_binding_revisions WHERE import_id = NEW.import_id AND organization_id = NEW.organization_id ORDER BY revision_number DESC LIMIT 1;
            IF NEW.supersedes_revision_id IS DISTINCT FROM head.id OR NEW.revision_number <> coalesce(head.revision_number, 0) + 1 THEN
                RAISE EXCEPTION 'Financial revision must extend the current head';
            END IF;
            IF NEW.row_count <> source.row_count OR NEW.status <> CASE WHEN NEW.review_rows > 0 OR source.report_snapshot->>'status' = 'REVIEW_REQUIRED' THEN 'REVIEW_REQUIRED' ELSE 'RECORDED' END THEN
                RAISE EXCEPTION 'Financial revision must retain source coverage and batch warnings';
            END IF;
            NEW.recording_transaction_id := txid_current();
            RETURN NEW;
        END $$;
        CREATE FUNCTION financial_binding_row_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner financial_binding_revisions%ROWTYPE; source financial_import_rows%ROWTYPE; market text;
        BEGIN
            SELECT * INTO owner FROM financial_binding_revisions WHERE id = NEW.revision_id AND import_id = NEW.import_id AND organization_id = NEW.organization_id;
            IF NOT FOUND OR owner.recording_transaction_id <> txid_current() THEN
                RAISE EXCEPTION 'Financial binding rows require their own revision transaction';
            END IF;
            SELECT * INTO source FROM financial_import_rows WHERE id = NEW.source_row_id AND import_id = NEW.import_id AND organization_id = NEW.organization_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'Financial binding source must belong to its import'; END IF;
            IF (source.row_kind = 'UNALLOCATED' AND NEW.mapping_status <> 'UNALLOCATED')
                OR (source.row_kind <> 'UNALLOCATED' AND NEW.mapping_status = 'UNALLOCATED')
                OR (source.row_kind <> 'UNALLOCATED' AND (source.source_snapshot->>'status' = 'INVALID') <> (NEW.mapping_status = 'INVALID_SOURCE'))
                OR NEW.review_required <> (source.source_snapshot->>'status' <> 'RECONCILED' OR NEW.mapping_status <> 'LINKED')
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(source.source_snapshot->'issues') issue WHERE NOT (issue->>'code' = ANY(NEW.review_reasons))) THEN
                RAISE EXCEPTION 'Financial binding must preserve source warnings and row kind';
            END IF;
            SELECT marketplace_id INTO market FROM financial_imports WHERE id = NEW.import_id;
            IF NEW.mapping_status = 'LINKED' AND NOT EXISTS (
                SELECT 1 FROM marketplace_listings l JOIN products p ON p.id = l.product_id
                WHERE l.id = NEW.listing_id AND p.id = NEW.product_id
                  AND l.organization_id = NEW.organization_id AND p.organization_id = NEW.organization_id
                  AND l.marketplace = 'AMAZON' AND l.marketplace_id = market
                  AND l.seller_sku = source.seller_sku AND l.mapping_status = 'APPROVED'
                  AND l.is_active AND p.is_active AND p.status = 'ACTIVE'
                  AND (source.source_snapshot->>'asin' IS NULL OR l.asin IS NULL OR source.source_snapshot->>'asin' = l.asin)
            ) THEN RAISE EXCEPTION 'Financial revised links require an approved mapping'; END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION financial_binding_check_counts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE all_rows integer; linked integer; review integer; changed integer;
        BEGIN
            SELECT count(*), count(*) FILTER (WHERE b.mapping_status = 'LINKED'), count(*) FILTER (WHERE b.review_required),
                count(*) FILTER (WHERE
                  CASE WHEN NEW.supersedes_revision_id IS NULL THEN
                    ROW(b.mapping_status,b.listing_id,b.product_id,b.match_priority,b.mapping_snapshot,b.review_reasons,b.review_required)
                    IS DISTINCT FROM ROW(s.mapping_status,s.listing_id,s.product_id,s.match_priority,s.mapping_snapshot,s.review_reasons,s.review_required)
                  ELSE
                    ROW(b.mapping_status,b.listing_id,b.product_id,b.match_priority,b.mapping_snapshot,b.review_reasons,b.review_required)
                    IS DISTINCT FROM ROW(prev.mapping_status,prev.listing_id,prev.product_id,prev.match_priority,prev.mapping_snapshot,prev.review_reasons,prev.review_required)
                  END)
              INTO all_rows, linked, review, changed FROM financial_binding_rows b
              JOIN financial_import_rows s ON s.id = b.source_row_id
              LEFT JOIN financial_binding_rows prev ON prev.revision_id = NEW.supersedes_revision_id AND prev.source_row_id = b.source_row_id
              WHERE b.revision_id = NEW.id AND b.organization_id = NEW.organization_id;
            IF all_rows <> NEW.row_count OR linked <> NEW.linked_rows OR review <> NEW.review_rows OR changed <> NEW.changed_rows THEN
                RAISE EXCEPTION 'Financial revision counters must match complete bindings';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER financial_binding_revision_insert_guard BEFORE INSERT ON financial_binding_revisions FOR EACH ROW EXECUTE FUNCTION financial_binding_revision_guard();
        CREATE TRIGGER financial_binding_row_insert_guard BEFORE INSERT ON financial_binding_rows FOR EACH ROW EXECUTE FUNCTION financial_binding_row_guard();
        CREATE CONSTRAINT TRIGGER financial_binding_complete AFTER INSERT ON financial_binding_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION financial_binding_check_counts();
    """)
    for table in ("financial_binding_revisions", "financial_binding_rows"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION financial_snapshot_reject_mutation()"
        )


def downgrade() -> None:
    op.drop_table("financial_binding_rows")
    op.drop_table("financial_binding_revisions")
    for function in (
        "financial_binding_check_counts",
        "financial_binding_row_guard",
        "financial_binding_revision_guard",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint(
        "uq_financial_import_rows_id_import_id_organization_id",
        "financial_import_rows",
        type_="unique",
    )
