"""Append-only reviewed inbound positions and v5 evaluation references.

Revision ID: a61118202601
Revises: a61117202608
"""

from alembic import op

revision = "a61118202601"
down_revision = "a61117202608"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(TABLE_SQL)
    for statement in INDEX_SQL:
        op.execute(statement)
    op.execute(INBOUND_GUARD_SQL)
    op.execute(
        "CREATE TRIGGER inbound_evidence_insert BEFORE INSERT ON inbound_evidence_revisions FOR EACH ROW EXECUTE FUNCTION inbound_evidence_guard()"
    )
    op.execute(
        "CREATE FUNCTION inbound_evidence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Reviewed inbound evidence is immutable'; END $$"
    )
    op.execute(
        "CREATE TRIGGER inbound_evidence_no_mutation BEFORE UPDATE OR DELETE ON inbound_evidence_revisions FOR EACH ROW EXECUTE FUNCTION inbound_evidence_immutable()"
    )
    op.execute(GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM inbound_evidence_revisions) OR EXISTS (SELECT 1 FROM replenishment_recommendations WHERE snapshot->>'rule_version'='replenishment-evaluation-v5') THEN RAISE EXCEPTION 'Cannot discard reviewed inbound evidence'; END IF; END $$"
    )
    op.execute(LEGACY_GUARD_SQL)
    op.drop_table("inbound_evidence_revisions")
    op.execute("DROP FUNCTION inbound_evidence_guard()")
    op.execute("DROP FUNCTION inbound_evidence_immutable()")


TABLE_SQL = r"""

CREATE TABLE inbound_evidence_revisions (
	policy_id UUID NOT NULL,
	approved_revision_id UUID NOT NULL,
	product_id UUID NOT NULL,
	marketplace_id VARCHAR(32) NOT NULL,
	pool_id VARCHAR(64) NOT NULL,
	channel VARCHAR(3) NOT NULL,
	revision_number INTEGER NOT NULL,
	previous_revision_id UUID,
	kind VARCHAR(16) NOT NULL,
	inventory_run_id UUID NOT NULL,
	stock_snapshot_id UUID NOT NULL,
	stock_source_listing_id UUID NOT NULL,
	source_context JSONB NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	position JSONB NOT NULL,
	declared_total_base_units BIGINT NOT NULL,
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	request_id UUID NOT NULL,
	request_sha256 VARCHAR(64) NOT NULL,
	recorded_by_user_id UUID NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	reason VARCHAR(1000) NOT NULL,
	purchasing_eligible BOOLEAN DEFAULT false NOT NULL,
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	organization_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_inbound_evidence_revisions PRIMARY KEY (id),
	CONSTRAINT ck_inbound_evidence_revisions_valid_kind_channel CHECK (kind in ('CONFIRMED','REVOKED') and channel in ('FBA','FBM')),
	CONSTRAINT ck_inbound_evidence_revisions_valid_counts CHECK (revision_number > 0 and declared_total_base_units >= 0),
	CONSTRAINT ck_inbound_evidence_revisions_us_marketplace CHECK (marketplace_id = 'ATVPDKIKX0DER'),
	CONSTRAINT ck_inbound_evidence_revisions_valid_digests CHECK (source_sha256 ~ '^[0-9a-f]{64}$' and request_sha256 ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_inbound_evidence_revisions_object_evidence CHECK (jsonb_typeof(position) = 'object' and jsonb_typeof(source_context) = 'object'),
	CONSTRAINT ck_inbound_evidence_revisions_reason_required CHECK (length(btrim(reason)) between 10 and 1000),
	CONSTRAINT ck_inbound_evidence_revisions_not_purchase_authorization CHECK (not purchasing_eligible),
	CONSTRAINT fk_inbound_evidence_revisions_policy_id_purchasing_poli_c425 FOREIGN KEY(policy_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_approved_revision_id_purc_cdc1 FOREIGN KEY(approved_revision_id) REFERENCES purchasing_policy_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_product_id_products FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_previous_revision_id_inbo_2a13 FOREIGN KEY(previous_revision_id) REFERENCES inbound_evidence_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_inventory_run_id_amazon_sync_runs FOREIGN KEY(inventory_run_id) REFERENCES amazon_sync_runs (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_stock_snapshot_id_amazon__3ab0 FOREIGN KEY(stock_snapshot_id) REFERENCES amazon_inventory_snapshots (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_stock_source_listing_id_m_3c31 FOREIGN KEY(stock_source_listing_id) REFERENCES marketplace_listings (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_recorded_by_user_id_users FOREIGN KEY(recorded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT fk_inbound_evidence_revisions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
)


"""

INDEX_SQL = (
    "CREATE INDEX ix_inbound_evidence_revisions_organization_id ON inbound_evidence_revisions (organization_id)",
    "CREATE UNIQUE INDEX uq_inbound_previous ON inbound_evidence_revisions (previous_revision_id) WHERE previous_revision_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_inbound_request ON inbound_evidence_revisions (organization_id, request_id)",
    "CREATE UNIQUE INDEX uq_inbound_stream_revision ON inbound_evidence_revisions (organization_id, policy_id, pool_id, revision_number)",
)

INBOUND_GUARD_SQL = r"""
CREATE FUNCTION inbound_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approval purchasing_policy_revisions%ROWTYPE; decision purchasing_policy_revisions%ROWTYPE;
previous inbound_evidence_revisions%ROWTYPE; snap amazon_inventory_snapshots%ROWTYPE;
listing marketplace_listings%ROWTYPE; run amazon_sync_runs%ROWTYPE;
pool jsonb; factor numeric; native numeric; latest_id uuid;
BEGIN
    SELECT * INTO approval FROM purchasing_policy_revisions WHERE id=NEW.approved_revision_id;
    IF NOT FOUND OR approval.organization_id<>NEW.organization_id OR approval.kind<>'APPROVED'
        OR approval.policy_id<>NEW.policy_id OR approval.product_id<>NEW.product_id OR approval.marketplace_id<>NEW.marketplace_id THEN
        RAISE EXCEPTION 'Inbound evidence must reference its own scoped policy approval';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('purchasing-policy:'||NEW.organization_id::text||':'||NEW.product_id::text||':'||approval.vendor_product_id::text||':'||NEW.marketplace_id,0));
    IF NOT EXISTS (SELECT 1 FROM users u JOIN organizations o ON o.id=u.organization_id WHERE u.id=NEW.recorded_by_user_id AND u.organization_id=NEW.organization_id AND u.is_active AND o.is_active) THEN
        RAISE EXCEPTION 'Inbound reviewer and organization must be active';
    END IF;
    SELECT * INTO previous FROM inbound_evidence_revisions WHERE organization_id=NEW.organization_id AND policy_id=NEW.policy_id AND pool_id=NEW.pool_id ORDER BY revision_number DESC LIMIT 1;
    IF FOUND THEN
        IF NEW.previous_revision_id IS DISTINCT FROM previous.id OR NEW.revision_number<>previous.revision_number+1 OR NEW.recorded_at<previous.recorded_at THEN RAISE EXCEPTION 'Inbound evidence head changed'; END IF;
    ELSIF NEW.previous_revision_id IS NOT NULL OR NEW.revision_number<>1 OR NEW.kind<>'CONFIRMED' THEN
        RAISE EXCEPTION 'Inbound evidence must start with a confirmation';
    END IF;
    IF NEW.recorded_at>clock_timestamp() OR NEW.recorded_at<approval.recorded_at THEN RAISE EXCEPTION 'Invalid inbound recording time'; END IF;
    IF NEW.kind='REVOKED' THEN
        IF previous.id IS NULL OR previous.kind<>'CONFIRMED' OR
            (NEW.approved_revision_id,NEW.product_id,NEW.marketplace_id,NEW.channel,NEW.inventory_run_id,NEW.stock_snapshot_id,NEW.stock_source_listing_id,NEW.source_context,NEW.source_sha256,NEW.position,NEW.declared_total_base_units,NEW.observed_at,NEW.expires_at)
            IS DISTINCT FROM
            (previous.approved_revision_id,previous.product_id,previous.marketplace_id,previous.channel,previous.inventory_run_id,previous.stock_snapshot_id,previous.stock_source_listing_id,previous.source_context,previous.source_sha256,previous.position,previous.declared_total_base_units,previous.observed_at,previous.expires_at) THEN
            RAISE EXCEPTION 'Revocation must copy the currently confirmed position';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO decision FROM purchasing_policy_revisions WHERE organization_id=NEW.organization_id AND policy_id=NEW.policy_id AND kind<>'DRAFT' ORDER BY revision_number DESC LIMIT 1;
    IF NOT FOUND OR decision.id<>approval.id THEN RAISE EXCEPTION 'Inbound confirmation requires current approval'; END IF;
    SELECT value INTO pool FROM jsonb_array_elements(approval.definition_snapshot->'amazon'->'stock_pools') WHERE value->>'pool_id'=NEW.pool_id;
    IF pool IS NULL OR pool->>'channel'<>NEW.channel OR pool->>'stock_source_listing_id'<>NEW.stock_source_listing_id::text THEN RAISE EXCEPTION 'Inbound pool differs from approved stock source'; END IF;
    SELECT * INTO listing FROM marketplace_listings WHERE id=NEW.stock_source_listing_id AND organization_id=NEW.organization_id AND product_id=NEW.product_id AND marketplace_id=NEW.marketplace_id AND mapping_status='APPROVED' AND is_active AND listing_status='ACTIVE';
    IF NOT FOUND THEN RAISE EXCEPTION 'Inbound stock listing must be scoped and approved'; END IF;
    SELECT * INTO snap FROM amazon_inventory_snapshots WHERE id=NEW.stock_snapshot_id AND organization_id=NEW.organization_id AND sync_run_id=NEW.inventory_run_id AND seller_sku=listing.seller_sku AND asin=listing.asin;
    IF NOT FOUND THEN RAISE EXCEPTION 'Inbound snapshot ownership or identity differs'; END IF;
    SELECT * INTO run FROM amazon_sync_runs WHERE id=NEW.inventory_run_id AND organization_id=NEW.organization_id AND marketplace_id=NEW.marketplace_id AND job_type='FBA_INVENTORY' AND status='COMPLETED' AND rows_failed=0;
    IF NOT FOUND OR run.started_at IS NULL OR run.completed_at IS NULL OR snap.captured_at<run.started_at OR snap.captured_at>run.completed_at OR run.completed_at>NEW.recorded_at THEN RAISE EXCEPTION 'Inbound inventory source is incomplete or invalid'; END IF;
    SELECT id INTO latest_id FROM amazon_sync_runs WHERE organization_id=NEW.organization_id AND marketplace_id=NEW.marketplace_id AND job_type='FBA_INVENTORY' ORDER BY started_at DESC NULLS LAST,id LIMIT 1;
    IF latest_id IS DISTINCT FROM run.id OR (SELECT count(*) FROM amazon_sync_runs WHERE organization_id=NEW.organization_id AND marketplace_id=NEW.marketplace_id AND job_type='FBA_INVENTORY' AND started_at=run.started_at)>1 THEN RAISE EXCEPTION 'Inbound confirmation requires an unambiguous latest inventory source'; END IF;
    IF NEW.observed_at<snap.captured_at OR NEW.observed_at>NEW.recorded_at OR NEW.expires_at<=NEW.recorded_at OR NEW.expires_at<=NEW.observed_at OR NEW.expires_at>NEW.observed_at+interval '31 days' THEN RAISE EXCEPTION 'Inbound observation or expiry is invalid'; END IF;
    IF NEW.source_context->>'approved_revision_id' IS DISTINCT FROM approval.id::text OR NEW.source_context->>'policy_id' IS DISTINCT FROM NEW.policy_id::text OR NEW.source_context->>'product_id' IS DISTINCT FROM NEW.product_id::text OR NEW.source_context->>'stock_snapshot_id' IS DISTINCT FROM snap.id::text OR NEW.source_context->'inventory_run'->>'id' IS DISTINCT FROM run.id::text OR NEW.source_context->'pool' IS DISTINCT FROM pool THEN RAISE EXCEPTION 'Inbound source context disagrees with its references'; END IF;
    IF NEW.position->'complete_position_confirmed' IS DISTINCT FROM 'true'::jsonb OR NEW.position->'no_on_hand_overlap_confirmed' IS DISTINCT FROM 'true'::jsonb OR NEW.position->'declared_total_base_units' IS DISTINCT FROM to_jsonb(NEW.declared_total_base_units) OR (NEW.position->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at OR (NEW.position->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at OR coalesce(length(btrim(NEW.position->>'evidence_reference')),0) NOT BETWEEN 10 AND 500 OR jsonb_typeof(NEW.position->'arrivals') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'A complete non-overlapping referenced inbound position is required'; END IF;
    IF jsonb_array_length(NEW.position->'arrivals')>100 OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.position->'arrivals') a WHERE jsonb_typeof(a) IS DISTINCT FROM 'object' OR a->>'expected_at' IS NULL OR a->>'expected_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' OR jsonb_typeof(a->'base_units') IS DISTINCT FROM 'number' OR (a->>'base_units')!~'^[1-9][0-9]*$' OR (a->>'base_units')::numeric>9223372036854775807 OR coalesce(length(btrim(a->>'reference')),0) NOT BETWEEN 1 AND 100 OR (a->>'expected_at')::timestamptz<=NEW.recorded_at) OR (SELECT coalesce(sum((a->>'base_units')::numeric),0) FROM jsonb_array_elements(NEW.position->'arrivals') a)<>NEW.declared_total_base_units OR (SELECT count(*)<>count(DISTINCT lower(btrim(a->>'reference'))) FROM jsonb_array_elements(NEW.position->'arrivals') a) THEN RAISE EXCEPTION 'Inbound arrivals must be distinct future whole quantities that reconcile exactly'; END IF;
    IF NEW.channel='FBA' THEN
        SELECT (value->>'base_units_per_listing_unit')::numeric INTO factor FROM jsonb_array_elements(approval.definition_snapshot->'amazon'->'listing_units') WHERE value->>'listing_id'=listing.id::text;
        IF factor IS NULL OR factor<>trunc(factor) OR factor NOT BETWEEN 1 AND 1000000 OR snap.raw->>'source' IS DISTINCT FROM 'fba_inventory' OR snap.raw->'inbound_working' IS DISTINCT FROM to_jsonb(snap.inbound_working) OR snap.raw->'inbound_shipped' IS DISTINCT FROM to_jsonb(snap.inbound_shipped) OR snap.raw->'inbound_receiving' IS DISTINCT FROM to_jsonb(snap.inbound_receiving) OR snap.raw->>'inbound_working'!~'^[0-9]+$' OR snap.raw->>'inbound_shipped'!~'^[0-9]+$' OR snap.raw->>'inbound_receiving'!~'^[0-9]+$' THEN RAISE EXCEPTION 'Native FBA inbound must be known and consistent'; END IF;
        native=(snap.inbound_working::numeric+snap.inbound_shipped::numeric+snap.inbound_receiving::numeric)*factor;
        IF native IS DISTINCT FROM NEW.declared_total_base_units::numeric THEN RAISE EXCEPTION 'FBA inbound confirmation must reconcile to the observed total'; END IF;
    ELSIF snap.raw->'listing'->>'fulfillment-channel' IS DISTINCT FROM 'DEFAULT' THEN
        RAISE EXCEPTION 'Manual FBM evidence requires a merchant stock source';
    END IF;
    IF EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (pool_id) * FROM inbound_evidence_revisions WHERE organization_id=NEW.organization_id AND policy_id=NEW.policy_id ORDER BY pool_id,revision_number DESC) h JOIN LATERAL jsonb_array_elements(h.position->'arrivals') old_arrival ON true JOIN LATERAL jsonb_array_elements(NEW.position->'arrivals') new_arrival ON lower(btrim(old_arrival->>'reference'))=lower(btrim(new_arrival->>'reference')) WHERE h.pool_id<>NEW.pool_id AND h.kind='CONFIRMED' AND h.approved_revision_id=NEW.approved_revision_id AND h.inventory_run_id=NEW.inventory_run_id AND h.expires_at>NEW.recorded_at) THEN RAISE EXCEPTION 'An inbound shipment cannot be credited to two pools'; END IF;
    RETURN NEW;
END $$;

"""

GUARD_SQL = r"""


CREATE OR REPLACE FUNCTION replenishment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approval purchasing_policy_revisions%ROWTYPE;
decision purchasing_policy_revisions%ROWTYPE;
financial financial_imports%ROWTYPE;
binding_id uuid;
assessed jsonb; ev inbound_evidence_revisions%ROWTYPE; stock_pool jsonb;
arrival jsonb; total_credit numeric; credit numeric; sales numeric; on_hand numeric;
window_days integer; coverage_days integer; captured timestamptz; evaluated timestamptz;
expected_disposition text; expected_arrivals jsonb;
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
    IF (NEW.snapshot->>'rule_version' IS NULL OR NEW.snapshot->>'rule_version' NOT IN ('replenishment-evaluation-v1', 'replenishment-evaluation-v2', 'replenishment-evaluation-v3', 'replenishment-evaluation-v4', 'replenishment-evaluation-v5'))
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
    IF NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v3' OR (NEW.snapshot->>'rule_version' IN ('replenishment-evaluation-v4','replenishment-evaluation-v5') AND NEW.financial_import_id IS NOT NULL) THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || NEW.marketplace_id, 0));
        SELECT * INTO financial FROM financial_imports WHERE id = NEW.financial_import_id
            AND organization_id = NEW.organization_id AND marketplace_id = NEW.marketplace_id;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM financial_imports WHERE supersedes_import_id = financial.id) THEN
            RAISE EXCEPTION 'Financial evaluation must reference its own current source';
        END IF;
        SELECT id INTO binding_id FROM financial_binding_revisions WHERE import_id = financial.id
            AND organization_id = NEW.organization_id ORDER BY revision_number DESC LIMIT 1;
        IF binding_id IS DISTINCT FROM NEW.financial_binding_revision_id
            OR jsonb_typeof(NEW.snapshot->'financial') IS DISTINCT FROM 'object'
            OR NEW.snapshot->'financial'->>'basis' IS DISTINCT FROM 'HISTORICAL_REPORT_ONLY'
            OR NEW.snapshot->>'profitability' IS DISTINCT FROM 'HISTORICAL_CONTEXT'
            OR NEW.snapshot->'financial'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'financial'->>'import_id' IS DISTINCT FROM financial.id::text
            OR NEW.snapshot->'financial'->>'binding_revision_id' IS DISTINCT FROM binding_id::text
            OR NEW.snapshot->'financial'->>'marketplace_id' IS DISTINCT FROM financial.marketplace_id
            OR NEW.snapshot->'financial'->>'raw_sha256' IS DISTINCT FROM financial.raw_sha256
            OR NEW.snapshot->'financial'->>'profile_sha256' IS DISTINCT FROM financial.profile_sha256
            OR NEW.snapshot->'financial'->'profile_snapshot' IS DISTINCT FROM financial.profile_snapshot
            OR NEW.snapshot->'financial'->>'store_name' IS DISTINCT FROM financial.store_name
            OR NEW.snapshot->'financial'->>'currency' IS DISTINCT FROM financial.currency
            OR (NEW.snapshot->'financial'->>'period_start')::timestamptz IS DISTINCT FROM financial.period_start
            OR (NEW.snapshot->'financial'->>'period_end')::timestamptz IS DISTINCT FROM financial.period_end
            OR ((approval.definition_snapshot ? 'supplier') AND jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object')
            OR (NOT (approval.definition_snapshot ? 'supplier') AND NEW.snapshot->'supplier' IS DISTINCT FROM 'null'::jsonb) THEN
            RAISE EXCEPTION 'Financial evaluation must agree with scoped source, binding and supplier settings';
        END IF;
    ELSIF NEW.financial_import_id IS NOT NULL OR NEW.financial_binding_revision_id IS NOT NULL THEN
        RAISE EXCEPTION 'Legacy evaluations cannot silently select financial evidence';
    END IF;
    IF NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v4' OR (NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v5' AND approval.definition_snapshot ? 'commercial') THEN
        IF jsonb_typeof(approval.definition_snapshot->'commercial') IS DISTINCT FROM 'object'
            OR jsonb_typeof(approval.definition_snapshot->'supplier') IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.snapshot->'commercial') IS DISTINCT FROM 'object'
            OR NEW.snapshot->'commercial'->'policy' IS DISTINCT FROM approval.definition_snapshot->'commercial'
            OR NEW.snapshot->'commercial'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'commercial'->'landed_cost_verified' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'allocation'->>'rule_version' IS DISTINCT FROM 'approved-pool-priority-v1'
            OR NEW.snapshot->'allocation'->>'scope' IS DISTINCT FROM 'THIS_PRODUCT_SUPPLIER_SKU_EVALUATION'
            OR NEW.snapshot->'allocation'->'stock_reserved' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'allocation'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR (NEW.financial_import_id IS NULL AND (NEW.snapshot->'financial' IS DISTINCT FROM 'null'::jsonb OR NEW.snapshot->>'profitability' IS DISTINCT FROM 'NOT_INTEGRATED')) THEN
            RAISE EXCEPTION 'Commercial evaluation must agree with approved settings and non-purchasing scope';
        END IF;
    ELSIF approval.definition_snapshot ? 'commercial' THEN
        RAISE EXCEPTION 'Legacy evaluation cannot discard approved commercial settings';
    END IF;
    IF NEW.snapshot->>'rule_version'='replenishment-evaluation-v5' THEN
        IF NEW.snapshot->'readiness'->>'rule_version' IS DISTINCT FROM 'amazon-readiness-v2'
            OR NEW.snapshot->'readiness'->'confirmations_persisted' IS DISTINCT FROM 'true'::jsonb
            OR jsonb_typeof(NEW.snapshot->'readiness'->'inbound_revision_ids') IS DISTINCT FROM 'array'
            OR jsonb_typeof(NEW.snapshot->'inbound_assessments') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.snapshot->'readiness'->'inbound_revision_ids') NOT BETWEEN 1 AND 200
            OR jsonb_array_length(NEW.snapshot->'readiness'->'inbound_revision_ids')<>jsonb_array_length(NEW.snapshot->'inbound_assessments')
            OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(NEW.snapshot->'readiness'->'inbound_revision_ids')) THEN
            RAISE EXCEPTION 'Reviewed inbound evaluation requires explicit distinct evidence';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.snapshot->'inbound_assessments') a
            LEFT JOIN inbound_evidence_revisions e ON e.id=(a->'revision'->>'id')::uuid
            WHERE e.id IS NULL OR e.organization_id<>NEW.organization_id OR e.policy_id<>NEW.policy_id OR e.approved_revision_id<>approval.id OR e.kind<>'CONFIRMED'
            OR NOT (NEW.snapshot->'readiness'->'inbound_revision_ids' @> jsonb_build_array(e.id::text))
            OR e.id IS DISTINCT FROM (SELECT id FROM inbound_evidence_revisions h WHERE h.organization_id=NEW.organization_id AND h.policy_id=NEW.policy_id AND h.pool_id=e.pool_id ORDER BY revision_number DESC LIMIT 1)
            OR a->'revision'->'position' IS DISTINCT FROM e.position
            OR a->'revision'->>'source_sha256' IS DISTINCT FROM e.source_sha256
            OR a->'revision'->'source_context' IS DISTINCT FROM e.source_context
            OR a->'revision'->>'pool_id' IS DISTINCT FROM e.pool_id
            OR a->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR (a->>'usable_base_units')::numeric<0 OR (a->>'usable_base_units')::numeric>e.declared_total_base_units
        ) OR (SELECT count(*)<>count(DISTINCT a->'revision'->>'pool_id') FROM jsonb_array_elements(NEW.snapshot->'inbound_assessments') a) THEN
            RAISE EXCEPTION 'Inbound evaluation must retain its own current immutable evidence';
        END IF;
        IF jsonb_typeof(NEW.snapshot->'readiness'->'original_inbound_issues') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Original inbound issues must be retained'; END IF;
        FOR assessed IN SELECT value FROM jsonb_array_elements(NEW.snapshot->'inbound_assessments') LOOP
            SELECT * INTO ev FROM inbound_evidence_revisions WHERE id=(assessed->'revision'->>'id')::uuid;
            SELECT value INTO stock_pool FROM jsonb_array_elements(NEW.snapshot->'readiness'->'pools') WHERE value->>'pool_id'=ev.pool_id;
            IF stock_pool IS NULL OR stock_pool->'usable_inbound_base_units' IS DISTINCT FROM assessed->'usable_base_units'
                OR assessed->>'basis' IS DISTINCT FROM 'MANAGER_REVIEWED_COMPLETE_POSITION'
                OR assessed->>'status' NOT IN ('APPLIED','BLOCKED') OR assessed->>'status' IS NULL THEN
                RAISE EXCEPTION 'Reviewed inbound must agree with its readiness pool';
            END IF;
            IF assessed->>'status'='BLOCKED' THEN
                IF assessed->'usable_base_units' IS DISTINCT FROM 'null'::jsonb OR NEW.snapshot->'readiness'->>'status' IS DISTINCT FROM 'BLOCKED'
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(assessed->'arrivals') v WHERE v->'credited_units' IS DISTINCT FROM 'null'::jsonb OR v->>'disposition' IS DISTINCT FROM 'UNCONFIRMED') THEN
                    RAISE EXCEPTION 'Blocked inbound must retain unknown quantities';
                END IF;
            ELSE
                evaluated=(NEW.snapshot->>'evaluated_at')::timestamptz;
                captured=(ev.source_context->>'source_capture_at')::timestamptz;
                sales=(stock_pool->>'sales_base_units')::numeric; on_hand=(stock_pool->>'on_hand_base_units')::numeric;
                window_days=(approval.definition_snapshot->'quantity'->>'velocity_window_days')::integer;
                coverage_days=(approval.definition_snapshot->'quantity'->>'target_days')::integer+(approval.definition_snapshot->'quantity'->>'lead_time_days')::integer+(approval.definition_snapshot->'quantity'->>'safety_days')::integer;
                IF captured IS NULL OR (assessed->>'source_capture_at')::timestamptz IS DISTINCT FROM captured
                    OR sales IS NULL OR sales<0 OR on_hand IS NULL OR on_hand<0
                    OR ev.observed_at>evaluated OR ev.expires_at<=evaluated
                    OR NEW.snapshot->'readiness'->'inventory_run'->>'id' IS DISTINCT FROM ev.inventory_run_id::text
                    OR ev.source_context->'on_hand_base_units' IS DISTINCT FROM stock_pool->'on_hand_base_units'
                    OR (ev.channel='FBA' AND ev.declared_total_base_units::numeric IS DISTINCT FROM (stock_pool->>'observed_inbound_base_units')::numeric)
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(ev.position->'arrivals') v WHERE (v->>'expected_at')::timestamptz<=evaluated)
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(assessed->'reasons')) THEN
                    RAISE EXCEPTION 'Applied inbound requires current complete source evidence';
                END IF;
                total_credit=0; expected_arrivals='[]'::jsonb;
                FOR arrival IN SELECT value FROM jsonb_array_elements(ev.position->'arrivals') ORDER BY (value->>'expected_at')::timestamptz,value->>'reference' LOOP
                    credit=0;
                    IF (arrival->>'expected_at')::timestamptz>captured+make_interval(days=>coverage_days) THEN expected_disposition='AFTER_COVERAGE';
                    ELSIF sales=0 OR (on_hand+total_credit)*window_days*86400000000 > sales*extract(epoch FROM ((arrival->>'expected_at')::timestamptz-captured))*1000000 THEN
                        expected_disposition='BEFORE_STOCKOUT'; credit=(arrival->>'base_units')::numeric;
                    ELSE expected_disposition='AFTER_STOCKOUT'; END IF;
                    expected_arrivals=expected_arrivals||jsonb_build_array(arrival||jsonb_build_object('disposition',expected_disposition,'credited_units',credit));
                    total_credit=total_credit+credit;
                END LOOP;
                IF (assessed->>'usable_base_units')::numeric IS DISTINCT FROM total_credit OR assessed->'arrivals' IS DISTINCT FROM expected_arrivals THEN
                    RAISE EXCEPTION 'Inbound arrival credit must follow exact before-stockout qualification';
                END IF;
            END IF;
        END LOOP;
        IF approval.definition_snapshot ? 'supplier' AND (jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object' OR NEW.snapshot->>'supplier_inputs' NOT IN ('VERIFIED','BLOCKED')) THEN RAISE EXCEPTION 'Inbound evaluation requires approved supplier evidence'; END IF;
        IF NOT (approval.definition_snapshot ? 'commercial') AND (NEW.snapshot->'commercial' IS DISTINCT FROM 'null'::jsonb OR NEW.snapshot->'allocation' IS DISTINCT FROM 'null'::jsonb) THEN RAISE EXCEPTION 'Inbound evaluation cannot invent commercial settings'; END IF;
        IF NOT (approval.definition_snapshot ? 'supplier') AND NEW.snapshot->'supplier' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'Inbound evaluation cannot invent supplier settings'; END IF;
        IF NEW.financial_import_id IS NULL AND (NEW.snapshot->'financial' IS DISTINCT FROM 'null'::jsonb OR NEW.snapshot->>'profitability' IS DISTINCT FROM 'NOT_INTEGRATED') THEN RAISE EXCEPTION 'Inbound evaluation cannot invent financial context'; END IF;
    END IF;
    RETURN NEW;
END $$;


"""

LEGACY_GUARD_SQL = r"""


CREATE OR REPLACE FUNCTION replenishment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approval purchasing_policy_revisions%ROWTYPE;
decision purchasing_policy_revisions%ROWTYPE;
financial financial_imports%ROWTYPE;
binding_id uuid;
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
    IF (NEW.snapshot->>'rule_version' IS NULL OR NEW.snapshot->>'rule_version' NOT IN ('replenishment-evaluation-v1', 'replenishment-evaluation-v2', 'replenishment-evaluation-v3', 'replenishment-evaluation-v4'))
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
    IF NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v3' OR (NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v4' AND NEW.financial_import_id IS NOT NULL) THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('financial:' || NEW.organization_id::text || ':' || NEW.marketplace_id, 0));
        SELECT * INTO financial FROM financial_imports WHERE id = NEW.financial_import_id
            AND organization_id = NEW.organization_id AND marketplace_id = NEW.marketplace_id;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM financial_imports WHERE supersedes_import_id = financial.id) THEN
            RAISE EXCEPTION 'Financial evaluation must reference its own current source';
        END IF;
        SELECT id INTO binding_id FROM financial_binding_revisions WHERE import_id = financial.id
            AND organization_id = NEW.organization_id ORDER BY revision_number DESC LIMIT 1;
        IF binding_id IS DISTINCT FROM NEW.financial_binding_revision_id
            OR jsonb_typeof(NEW.snapshot->'financial') IS DISTINCT FROM 'object'
            OR NEW.snapshot->'financial'->>'basis' IS DISTINCT FROM 'HISTORICAL_REPORT_ONLY'
            OR NEW.snapshot->>'profitability' IS DISTINCT FROM 'HISTORICAL_CONTEXT'
            OR NEW.snapshot->'financial'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'financial'->>'import_id' IS DISTINCT FROM financial.id::text
            OR NEW.snapshot->'financial'->>'binding_revision_id' IS DISTINCT FROM binding_id::text
            OR NEW.snapshot->'financial'->>'marketplace_id' IS DISTINCT FROM financial.marketplace_id
            OR NEW.snapshot->'financial'->>'raw_sha256' IS DISTINCT FROM financial.raw_sha256
            OR NEW.snapshot->'financial'->>'profile_sha256' IS DISTINCT FROM financial.profile_sha256
            OR NEW.snapshot->'financial'->'profile_snapshot' IS DISTINCT FROM financial.profile_snapshot
            OR NEW.snapshot->'financial'->>'store_name' IS DISTINCT FROM financial.store_name
            OR NEW.snapshot->'financial'->>'currency' IS DISTINCT FROM financial.currency
            OR (NEW.snapshot->'financial'->>'period_start')::timestamptz IS DISTINCT FROM financial.period_start
            OR (NEW.snapshot->'financial'->>'period_end')::timestamptz IS DISTINCT FROM financial.period_end
            OR ((approval.definition_snapshot ? 'supplier') AND jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object')
            OR (NOT (approval.definition_snapshot ? 'supplier') AND NEW.snapshot->'supplier' IS DISTINCT FROM 'null'::jsonb) THEN
            RAISE EXCEPTION 'Financial evaluation must agree with scoped source, binding and supplier settings';
        END IF;
    ELSIF NEW.financial_import_id IS NOT NULL OR NEW.financial_binding_revision_id IS NOT NULL THEN
        RAISE EXCEPTION 'Legacy evaluations cannot silently select financial evidence';
    END IF;
    IF NEW.snapshot->>'rule_version' = 'replenishment-evaluation-v4' THEN
        IF jsonb_typeof(approval.definition_snapshot->'commercial') IS DISTINCT FROM 'object'
            OR jsonb_typeof(approval.definition_snapshot->'supplier') IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.snapshot->'supplier') IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.snapshot->'commercial') IS DISTINCT FROM 'object'
            OR NEW.snapshot->'commercial'->'policy' IS DISTINCT FROM approval.definition_snapshot->'commercial'
            OR NEW.snapshot->'commercial'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'commercial'->'landed_cost_verified' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'allocation'->>'rule_version' IS DISTINCT FROM 'approved-pool-priority-v1'
            OR NEW.snapshot->'allocation'->>'scope' IS DISTINCT FROM 'THIS_PRODUCT_SUPPLIER_SKU_EVALUATION'
            OR NEW.snapshot->'allocation'->'stock_reserved' IS DISTINCT FROM 'false'::jsonb
            OR NEW.snapshot->'allocation'->'purchasing_eligible' IS DISTINCT FROM 'false'::jsonb
            OR (NEW.financial_import_id IS NULL AND (NEW.snapshot->'financial' IS DISTINCT FROM 'null'::jsonb OR NEW.snapshot->>'profitability' IS DISTINCT FROM 'NOT_INTEGRATED')) THEN
            RAISE EXCEPTION 'Commercial evaluation must agree with approved settings and non-purchasing scope';
        END IF;
    ELSIF approval.definition_snapshot ? 'commercial' THEN
        RAISE EXCEPTION 'Legacy evaluation cannot discard approved commercial settings';
    END IF;
    RETURN NEW;
END $$;


"""
