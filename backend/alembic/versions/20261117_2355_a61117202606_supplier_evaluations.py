"""Versioned supplier-aware evaluation snapshots; preserve v1 history.

Revision ID: a61117202606
Revises: a61117202605
"""

from alembic import op

revision = "a61117202606"
down_revision = "a61117202605"
branch_labels = None
depends_on = None


def _status_check(values: str) -> None:
    op.execute(
        "ALTER TABLE replenishment_recommendations DROP CONSTRAINT ck_replenishment_recommendations_valid_status"
    )
    op.execute(
        "ALTER TABLE replenishment_recommendations ADD CONSTRAINT ck_replenishment_recommendations_valid_status CHECK (status in ("
        + values
        + "))"
    )


def upgrade() -> None:
    _status_check("'BLOCKED', 'REVIEW_REQUIRED', 'NO_REORDER'")
    op.execute(GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM replenishment_recommendations WHERE snapshot->>'rule_version' = 'replenishment-evaluation-v2') OR EXISTS (SELECT 1 FROM purchasing_policy_revisions WHERE definition_snapshot ? 'supplier') THEN RAISE EXCEPTION 'Cannot discard supplier evaluation history or policy settings'; END IF; END $$"
    )
    _status_check("'BLOCKED', 'NO_REORDER'")
    op.execute(LEGACY_GUARD_SQL)


GUARD_SQL = r"""

CREATE OR REPLACE FUNCTION replenishment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
    IF (NEW.snapshot->>'rule_version' IS NULL OR NEW.snapshot->>'rule_version' NOT IN ('replenishment-evaluation-v1', 'replenishment-evaluation-v2'))
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
    IF (NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v2' AND
        (jsonb_typeof(approval.definition_snapshot->'supplier') IS DISTINCT FROM 'object' OR jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object'))
        OR (NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v1' AND (approval.definition_snapshot ? 'supplier' OR NEW.status = 'REVIEW_REQUIRED')) THEN
        RAISE EXCEPTION 'Supplier evaluation version must agree with approved settings';
    END IF;
    RETURN NEW;
END $$;

"""

LEGACY_GUARD_SQL = r"""

CREATE OR REPLACE FUNCTION replenishment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
