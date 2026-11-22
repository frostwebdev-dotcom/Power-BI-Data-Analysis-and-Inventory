"""Immutable saved replenishment evaluations.

Revision ID: a61117202605
Revises: a61117202604
"""

from alembic import op

revision = "a61117202605"
down_revision = "a61117202604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(TABLE_SQL)
    for statement in INDEX_SQL:
        op.execute(statement)
    op.execute(GUARD_SQL)
    op.execute(
        "CREATE TRIGGER replenishment_insert BEFORE INSERT ON replenishment_recommendations "
        "FOR EACH ROW EXECUTE FUNCTION replenishment_guard()"
    )
    op.execute(
        "CREATE FUNCTION replenishment_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Saved replenishment evaluations are immutable'; END $$"
    )
    op.execute(
        "CREATE TRIGGER replenishment_no_mutation BEFORE UPDATE OR DELETE ON "
        "replenishment_recommendations FOR EACH ROW EXECUTE FUNCTION replenishment_immutable()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM replenishment_recommendations) THEN "
        "RAISE EXCEPTION 'Cannot discard saved replenishment evaluations'; END IF; END $$"
    )
    op.drop_table("replenishment_recommendations")
    op.execute("DROP FUNCTION replenishment_guard()")
    op.execute("DROP FUNCTION replenishment_immutable()")


GUARD_SQL = r"""
CREATE FUNCTION replenishment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approval purchasing_policy_revisions%ROWTYPE;
decision purchasing_policy_revisions%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('purchasing-policy:' || NEW.organization_id::text || ':' || NEW.product_id::text || ':' || NEW.vendor_product_id::text || ':' || NEW.marketplace_id, 0));
    SELECT * INTO approval FROM purchasing_policy_revisions WHERE id = NEW.approved_revision_id;
    IF NOT FOUND OR approval.kind <> 'APPROVED'
        OR approval.policy_id <> NEW.policy_id OR approval.organization_id <> NEW.organization_id
        OR approval.product_id <> NEW.product_id OR approval.vendor_product_id <> NEW.vendor_product_id
        OR approval.marketplace_id <> NEW.marketplace_id THEN
        RAISE EXCEPTION 'Evaluation must reference its own approved policy';
    END IF;
    SELECT * INTO decision FROM purchasing_policy_revisions
        WHERE policy_id = NEW.policy_id AND organization_id = NEW.organization_id AND kind <> 'DRAFT'
        ORDER BY revision_number DESC LIMIT 1;
    IF NOT FOUND OR decision.id <> approval.id THEN
        RAISE EXCEPTION 'Evaluation must use the current policy approval';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM users u JOIN organizations o ON o.id = u.organization_id
        WHERE u.id = NEW.recorded_by_user_id AND u.organization_id = NEW.organization_id AND u.is_active AND o.is_active) THEN
        RAISE EXCEPTION 'Evaluation actor must belong to an active organization';
    END IF;
    IF NEW.snapshot->>'rule_version' IS DISTINCT FROM 'replenishment-evaluation-v1'
        OR NEW.snapshot->>'policy_id' IS DISTINCT FROM NEW.policy_id::text
        OR NEW.snapshot->>'product_id' IS DISTINCT FROM NEW.product_id::text
        OR NEW.snapshot->>'vendor_product_id' IS DISTINCT FROM NEW.vendor_product_id::text
        OR NEW.snapshot->>'marketplace_id' IS DISTINCT FROM NEW.marketplace_id
        OR NEW.snapshot->>'status' IS DISTINCT FROM NEW.status
        OR NEW.snapshot->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
        OR NEW.snapshot->'approved_policy'->>'id' IS DISTINCT FROM approval.id::text
        OR NEW.snapshot->'approved_policy'->'definition' IS DISTINCT FROM approval.definition_snapshot
        OR NEW.snapshot->'approved_policy'->>'definition_sha256' IS DISTINCT FROM approval.definition_sha256
        OR NEW.snapshot->'approved_policy'->'review_context' IS DISTINCT FROM approval.review_context
        OR NEW.snapshot->'approved_policy'->>'context_sha256' IS DISTINCT FROM approval.context_sha256
        OR NEW.snapshot->>'evaluated_at' IS NULL THEN
        RAISE EXCEPTION 'Evaluation snapshot must agree with its policy and identity';
    END IF;
    IF (NEW.snapshot->>'evaluated_at')::timestamptz > NEW.recorded_at
        OR (NEW.snapshot->>'evaluated_at')::timestamptz < approval.recorded_at
        OR NEW.recorded_at > clock_timestamp() THEN
        RAISE EXCEPTION 'Evaluation times must follow approval and cannot be in the future';
    END IF;
    RETURN NEW;
END $$;
"""

# Frozen PostgreSQL DDL; migration does not import application models.
TABLE_SQL = r"""

CREATE TABLE replenishment_recommendations (
	request_id UUID NOT NULL,
	request_sha256 VARCHAR(64) NOT NULL,
	policy_id UUID NOT NULL,
	approved_revision_id UUID NOT NULL,
	product_id UUID NOT NULL,
	vendor_product_id UUID NOT NULL,
	marketplace_id VARCHAR(32) NOT NULL,
	status VARCHAR(16) NOT NULL,
	snapshot JSONB NOT NULL,
	evidence_sha256 VARCHAR(64) NOT NULL,
	snapshot_sha256 VARCHAR(64) NOT NULL,
	recorded_by_user_id UUID NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	reason VARCHAR(1000) NOT NULL,
	purchasing_eligible BOOLEAN DEFAULT false NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	organization_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_replenishment_recommendations PRIMARY KEY (id),
	CONSTRAINT ck_replenishment_recommendations_valid_status CHECK (status in ('BLOCKED', 'NO_REORDER')),
	CONSTRAINT ck_replenishment_recommendations_us_marketplace CHECK (marketplace_id = 'ATVPDKIKX0DER'),
	CONSTRAINT ck_replenishment_recommendations_reason_required CHECK (length(btrim(reason)) between 10 and 1000),
	CONSTRAINT ck_replenishment_recommendations_object_snapshot CHECK (jsonb_typeof(snapshot) = 'object'),
	CONSTRAINT ck_replenishment_recommendations_not_purchase_authorization CHECK (not purchasing_eligible),
	CONSTRAINT ck_replenishment_recommendations_valid_digests CHECK (request_sha256 ~ '^[0-9a-f]{64}$' and evidence_sha256 ~ '^[0-9a-f]{64}$' and snapshot_sha256 ~ '^[0-9a-f]{64}$'),
	CONSTRAINT fk_replenishment_recommendations_policy_id_purchasing_p_51a6 FOREIGN KEY(policy_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_replenishment_recommendations_approved_revision_id_p_7bb8 FOREIGN KEY(approved_revision_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_replenishment_recommendations_product_id_products FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE RESTRICT,
	CONSTRAINT fk_replenishment_recommendations_vendor_product_id_vend_f52e FOREIGN KEY(vendor_product_id) REFERENCES vendor_products (id) ON DELETE RESTRICT,
	CONSTRAINT fk_replenishment_recommendations_recorded_by_user_id_users FOREIGN KEY(recorded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT fk_replenishment_recommendations_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
)


"""
INDEX_SQL = (
    "CREATE INDEX ix_replenishment_policy_recorded ON replenishment_recommendations (organization_id, policy_id, recorded_at)",
    "CREATE INDEX ix_replenishment_recommendations_organization_id ON replenishment_recommendations (organization_id)",
    "CREATE UNIQUE INDEX uq_replenishment_request ON replenishment_recommendations (organization_id, request_id)",
)
