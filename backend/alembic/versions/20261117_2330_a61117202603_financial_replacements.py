"""Preserve financial source-report replacements as immutable lineage.

Revision ID: a61117202603
Revises: a61117202602
"""

import sqlalchemy as sa
from alembic import op

revision = "a61117202603"
down_revision = "a61117202602"
branch_labels = None
depends_on = None

ORIGINAL_GUARD = """
CREATE OR REPLACE FUNCTION financial_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
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
"""


def upgrade() -> None:
    op.add_column("financial_imports", sa.Column("supersedes_import_id", sa.UUID()))
    op.add_column("financial_imports", sa.Column("replacement_reason", sa.String(1000)))
    op.add_column("financial_imports", sa.Column("replacement_preview_sha256", sa.String(64)))
    op.create_unique_constraint(
        "uq_financial_imports_supersedes_import_id", "financial_imports", ["supersedes_import_id"]
    )
    op.create_foreign_key(
        op.f("fk_financial_imports_supersedes_import_id_organization_id_financial_imports"),
        "financial_imports",
        "financial_imports",
        ["supersedes_import_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "valid_replacement",
        "financial_imports",
        "(supersedes_import_id is null and replacement_reason is null and replacement_preview_sha256 is null) or (supersedes_import_id is not null and replacement_reason is not null and length(btrim(replacement_reason)) between 10 and 1000 and replacement_preview_sha256 is not null and replacement_preview_sha256 ~ '^[0-9a-f]{64}$')",
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION financial_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source financial_imports%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || NEW.marketplace_id, 0));
            IF NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.uploaded_by_user_id AND organization_id = NEW.organization_id AND is_active) THEN
                RAISE EXCEPTION 'Financial uploader must belong to the organization and be active';
            END IF;
            IF NEW.supersedes_import_id IS NOT NULL THEN
                SELECT * INTO source FROM financial_imports WHERE id = NEW.supersedes_import_id AND organization_id = NEW.organization_id;
                IF NOT FOUND THEN RAISE EXCEPTION 'Financial replacement source must belong to the organization'; END IF;
                IF EXISTS (SELECT 1 FROM financial_imports WHERE supersedes_import_id = source.id) THEN
                    RAISE EXCEPTION 'Financial replacement must extend the current source head';
                END IF;
                IF NEW.period_start <> source.period_start OR NEW.period_end <> source.period_end
                    OR NEW.marketplace_id <> source.marketplace_id OR NEW.currency <> source.currency
                    OR NEW.store_name <> source.store_name OR NEW.profile_sha256 <> source.profile_sha256
                    OR NEW.profile_snapshot <> source.profile_snapshot OR NEW.raw_sha256 = source.raw_sha256 THEN
                    RAISE EXCEPTION 'Financial replacement must preserve confirmed context and use distinct bytes';
                END IF;
            END IF;
            IF EXISTS (
                WITH RECURSIVE ancestry AS (
                    SELECT id, supersedes_import_id FROM financial_imports WHERE id = NEW.supersedes_import_id AND organization_id = NEW.organization_id
                    UNION ALL
                    SELECT p.id, p.supersedes_import_id FROM financial_imports p JOIN ancestry a ON a.supersedes_import_id = p.id WHERE p.organization_id = NEW.organization_id
                )
                SELECT 1 FROM financial_imports f WHERE f.organization_id = NEW.organization_id AND f.marketplace_id = NEW.marketplace_id
                    AND f.period_start < NEW.period_end AND f.period_end > NEW.period_start
                    AND NOT EXISTS (SELECT 1 FROM ancestry a WHERE a.id = f.id)
            ) THEN RAISE EXCEPTION 'Financial reporting periods must not overlap outside replacement ancestry'; END IF;
            NEW.recording_transaction_id := txid_current();
            RETURN NEW;
        END $$;
        CREATE FUNCTION financial_binding_source_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE market text;
        BEGIN
            SELECT marketplace_id INTO market FROM financial_imports WHERE id = NEW.import_id AND organization_id = NEW.organization_id;
            PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || market, 0));
            IF EXISTS (SELECT 1 FROM financial_imports WHERE supersedes_import_id = NEW.import_id AND organization_id = NEW.organization_id) THEN
                RAISE EXCEPTION 'Financial binding revisions require a current source report';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER financial_binding_source_current BEFORE INSERT ON financial_binding_revisions FOR EACH ROW EXECUTE FUNCTION financial_binding_source_head_guard();
    """)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM financial_imports WHERE supersedes_import_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Cannot discard existing financial replacement history';
        END IF;
    END $$;""")
    op.execute("DROP TRIGGER financial_binding_source_current ON financial_binding_revisions")
    op.execute("DROP FUNCTION financial_binding_source_head_guard()")
    op.execute(ORIGINAL_GUARD)
    op.drop_constraint(
        op.f("ck_financial_imports_valid_replacement"), "financial_imports", type_="check"
    )
    op.drop_constraint(
        op.f("fk_financial_imports_supersedes_import_id_organization_id_financial_imports"),
        "financial_imports",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_financial_imports_supersedes_import_id", "financial_imports", type_="unique"
    )
    for column in ("replacement_preview_sha256", "replacement_reason", "supersedes_import_id"):
        op.drop_column("financial_imports", column)
