"""Immutable purchasing-policy drafts and reviewed decisions.

Revision ID: a61117202604
Revises: a61117202603
"""

from alembic import op

revision = "a61117202604"
down_revision = "a61117202603"
branch_labels = None
depends_on = None

# Frozen PostgreSQL DDL, generated from the reviewed model at implementation time.
# No application imports are used when this migration runs.
TABLE_SQL = r"""
CREATE TABLE purchasing_policy_revisions (
    policy_id UUID NOT NULL,
    product_id UUID NOT NULL,
    vendor_product_id UUID NOT NULL,
    marketplace_id VARCHAR(32) NOT NULL,
    revision_number INTEGER NOT NULL,
    supersedes_revision_id UUID,
    reviewed_revision_id UUID,
    kind VARCHAR(16) NOT NULL,
    definition_snapshot JSONB NOT NULL,
    definition_sha256 VARCHAR(64) NOT NULL,
    review_context JSONB,
    context_sha256 VARCHAR(64),
    recorded_by_user_id UUID NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    purchasing_eligible BOOLEAN DEFAULT false NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    organization_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_purchasing_policy_revisions PRIMARY KEY (id),
    CONSTRAINT uq_purchasing_policy_revisions_policy_id_revision_number UNIQUE (policy_id, revision_number),
    CONSTRAINT uq_purchasing_policy_revisions_supersedes_revision_id UNIQUE (supersedes_revision_id),
    CONSTRAINT ck_purchasing_policy_revisions_positive_revision CHECK (revision_number > 0),
    CONSTRAINT ck_purchasing_policy_revisions_us_marketplace CHECK (marketplace_id = 'ATVPDKIKX0DER'),
    CONSTRAINT ck_purchasing_policy_revisions_valid_kind CHECK (kind in ('DRAFT', 'APPROVED', 'REVOKED')),
    CONSTRAINT ck_purchasing_policy_revisions_reason_required CHECK (length(btrim(reason)) between 10 and 1000),
    CONSTRAINT ck_purchasing_policy_revisions_valid_digest CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_purchasing_policy_revisions_object_definition CHECK (jsonb_typeof(definition_snapshot) = 'object'),
    CONSTRAINT ck_purchasing_policy_revisions_root_or_successor CHECK ((revision_number = 1 and policy_id = id and supersedes_revision_id is null and kind = 'DRAFT') or (revision_number > 1 and policy_id <> id and supersedes_revision_id is not null)),
    CONSTRAINT ck_purchasing_policy_revisions_review_evidence CHECK ((kind = 'DRAFT' and reviewed_revision_id is null and review_context is null and context_sha256 is null) or (kind <> 'DRAFT' and reviewed_revision_id is not null and review_context is not null and jsonb_typeof(review_context) = 'object' and context_sha256 is not null and context_sha256 ~ '^[0-9a-f]{64}$')),
    CONSTRAINT ck_purchasing_policy_revisions_not_purchase_authorization CHECK (not purchasing_eligible),
    CONSTRAINT fk_purchasing_policy_revisions_policy_id_purchasing_pol_18a2 FOREIGN KEY(policy_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_product_id_products FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_vendor_product_id_vendor_e347 FOREIGN KEY(vendor_product_id) REFERENCES vendor_products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_supersedes_revision_id_p_cbc9 FOREIGN KEY(supersedes_revision_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_reviewed_revision_id_pur_8f78 FOREIGN KEY(reviewed_revision_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_recorded_by_user_id_users FOREIGN KEY(recorded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchasing_policy_revisions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
)

"""
INDEX_SQL = (
    "CREATE INDEX ix_purchasing_policy_revisions_organization_id ON purchasing_policy_revisions (organization_id)",
    "CREATE UNIQUE INDEX uq_purchasing_policy_root ON purchasing_policy_revisions (organization_id, product_id, vendor_product_id, marketplace_id) WHERE revision_number = 1",
)


def upgrade() -> None:
    op.execute(TABLE_SQL)
    for statement in INDEX_SQL:
        op.execute(statement)
    op.execute(GUARD_SQL)
    op.execute(
        "CREATE TRIGGER purchasing_policy_insert BEFORE INSERT ON purchasing_policy_revisions FOR EACH ROW EXECUTE FUNCTION purchasing_policy_guard()"
    )
    op.execute(
        "CREATE FUNCTION purchasing_policy_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Purchasing policy history is immutable'; END $$"
    )
    op.execute(
        "CREATE TRIGGER purchasing_policy_no_mutation BEFORE UPDATE OR DELETE ON purchasing_policy_revisions FOR EACH ROW EXECUTE FUNCTION purchasing_policy_immutable()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM purchasing_policy_revisions) THEN RAISE EXCEPTION 'Cannot discard recorded purchasing policy history'; END IF; END $$"
    )
    op.drop_table("purchasing_policy_revisions")
    op.execute("DROP FUNCTION purchasing_policy_guard()")
    op.execute("DROP FUNCTION purchasing_policy_immutable()")


GUARD_SQL = r"""
CREATE FUNCTION purchasing_policy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous purchasing_policy_revisions%ROWTYPE;
reviewed purchasing_policy_revisions%ROWTYPE;
decision purchasing_policy_revisions%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('purchasing-policy:' || NEW.organization_id::text || ':' || NEW.product_id::text || ':' || NEW.vendor_product_id::text || ':' || NEW.marketplace_id, 0));
    IF NOT EXISTS (SELECT 1 FROM users u JOIN organizations o ON o.id = u.organization_id WHERE u.id = NEW.recorded_by_user_id AND u.organization_id = NEW.organization_id AND u.is_active AND o.is_active) THEN
        RAISE EXCEPTION 'Policy actor must belong to an active organization';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM products WHERE id = NEW.product_id AND organization_id = NEW.organization_id)
        OR NOT EXISTS (SELECT 1 FROM vendor_products vp JOIN vendors v ON v.id = vp.vendor_id WHERE vp.id = NEW.vendor_product_id AND vp.organization_id = NEW.organization_id AND v.organization_id = NEW.organization_id) THEN
        RAISE EXCEPTION 'Policy identities must belong to the organization';
    END IF;
    IF NEW.definition_snapshot->>'rule_version' IS DISTINCT FROM 'purchasing-policy-v1'
        OR NEW.definition_snapshot->'amazon'->>'product_id' IS DISTINCT FROM NEW.product_id::text
        OR NEW.definition_snapshot->'amazon'->>'marketplace_id' IS DISTINCT FROM NEW.marketplace_id
        OR NEW.recorded_at > clock_timestamp() THEN
        RAISE EXCEPTION 'Policy definition identity and recording time must agree';
    END IF;
    IF NEW.supersedes_revision_id IS NOT NULL THEN
        SELECT * INTO previous FROM purchasing_policy_revisions WHERE id = NEW.supersedes_revision_id FOR UPDATE;
        IF NOT FOUND OR previous.organization_id <> NEW.organization_id OR previous.policy_id <> NEW.policy_id
            OR previous.product_id <> NEW.product_id OR previous.vendor_product_id <> NEW.vendor_product_id
            OR previous.marketplace_id <> NEW.marketplace_id OR previous.revision_number + 1 <> NEW.revision_number
            OR previous.recorded_at > NEW.recorded_at THEN
            RAISE EXCEPTION 'Policy revision must extend its own current stream';
        END IF;
        IF EXISTS (SELECT 1 FROM purchasing_policy_revisions WHERE supersedes_revision_id = previous.id) THEN
            RAISE EXCEPTION 'Policy stream cannot fork';
        END IF;
        IF NEW.kind <> 'DRAFT' THEN
            SELECT * INTO reviewed FROM purchasing_policy_revisions WHERE id = NEW.reviewed_revision_id;
            IF NOT FOUND OR reviewed.policy_id <> NEW.policy_id OR reviewed.organization_id <> NEW.organization_id
                OR reviewed.revision_number > previous.revision_number
                OR reviewed.definition_snapshot <> NEW.definition_snapshot
                OR reviewed.definition_sha256 <> NEW.definition_sha256 THEN
                RAISE EXCEPTION 'Policy review must copy its own reviewed definition';
            END IF;
            IF NEW.review_context->>'head_id' IS DISTINCT FROM previous.id::text
                OR NEW.review_context->>'policy_id' IS DISTINCT FROM NEW.policy_id::text
                OR NEW.review_context->>'definition_sha256' IS DISTINCT FROM NEW.definition_sha256
                OR NEW.review_context->>'reviewed_revision_id' IS DISTINCT FROM reviewed.id::text
                OR NEW.review_context->>'action' IS DISTINCT FROM CASE NEW.kind WHEN 'APPROVED' THEN 'APPROVE' ELSE 'REVOKE' END THEN
                RAISE EXCEPTION 'Policy review context must agree with the reviewed transition';
            END IF;
            IF NEW.kind = 'APPROVED' THEN
                IF previous.kind <> 'DRAFT' OR reviewed.id <> previous.id THEN
                    RAISE EXCEPTION 'Only the current draft can be approved';
                END IF;
            ELSE
                SELECT * INTO decision FROM purchasing_policy_revisions WHERE policy_id = NEW.policy_id AND kind <> 'DRAFT' ORDER BY revision_number DESC LIMIT 1;
                IF NOT FOUND OR decision.kind <> 'APPROVED' OR reviewed.id <> decision.id THEN
                    RAISE EXCEPTION 'Only the current approval can be revoked';
                END IF;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""
