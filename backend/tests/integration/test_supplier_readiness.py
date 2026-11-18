"""Supplier provenance, receipt-age policy and versioned saved calculation evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.imports.profile_rules import ProfileRules
from app.models import (
    ImportFile,
    ImportJob,
    ImportJobRow,
    VendorImportProfile,
    VendorInventorySnapshot,
)
from app.models.enums import (
    AvailabilityStatus,
    ImportJobStatus,
    ImportRowStatus,
    MatchMethod,
    MatchResult,
)
from app.schemas.purchasing_policies import PolicyDraftRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import SupplierRecommendationSnapshot
from app.schemas.supplier_readiness import SupplierReadinessResponse
from app.services import purchasing_policies as policies
from app.services import recommendations as service
from app.services.supplier_readiness import check_supplier_readiness
from tests.integration import factories
from tests.integration import test_purchasing_policies as policy_tests
from tests.integration import test_recommendations as evaluation
from tests.unit.test_supplier_readiness import supplier_settings

pytestmark = pytest.mark.integration


@dataclass
class Context:
    calculation: evaluation.Context
    profile: VendorImportProfile
    job: ImportJob
    file: ImportFile
    row: ImportJobRow
    stock: VendorInventorySnapshot


def setup(session: Session) -> Context:
    ctx = evaluation.setup(session)
    identity = ctx.policy
    at = datetime.now(UTC)
    rules = ProfileRules.model_validate(
        {
            "column_map": {
                "columns": [
                    {"target": "vendor_sku", "source": "SKU"},
                    {"target": "quantity_available", "source": "Qty"},
                ]
            },
            "availability_rules": {"available_when": "listed"},
            "quantity_semantics": {"unit": "case", "case_pack_from": "fixed", "fixed_pack_size": 6},
        }
    )
    profile = factories.make_import_profile(
        session, identity.org, identity.vendor, **rules.as_columns()
    )
    file = factories.make_import_file(
        session, identity.org, identity.vendor, uploaded_at=at - timedelta(minutes=2)
    )
    job = factories.make_import_job(
        session,
        identity.org,
        identity.vendor,
        file,
        vendor_import_profile_id=profile.id,
        profile_version=profile.version,
        status=ImportJobStatus.COMPLETED,
        total_rows=1,
        processed_rows=1,
        matched_rows=1,
        created_at=at - timedelta(seconds=110),
        started_at=at - timedelta(seconds=100),
        completed_at=at - timedelta(seconds=10),
    )
    row = factories.make_import_job_row(
        session,
        identity.org,
        job,
        vendor_sku=identity.vendor_product.vendor_sku,
        normalized_vendor_sku=identity.vendor_product.normalized_vendor_sku,
        vendor_product_id=identity.vendor_product.id,
        product_id=identity.product.id,
        quantity=Decimal("10"),
        availability_status=AvailabilityStatus.AVAILABLE,
        status=ImportRowStatus.OK,
        match_result=MatchResult.MATCHED,
        matched_by=MatchMethod.MANUAL_APPROVAL,
        normalized_data={
            "quantity": 10,
            "availability": "AVAILABLE",
            "pack_size": 6,
            "unit_of_measure": "case",
            "issues": [],
        },
    )
    stock = factories.make_snapshot(
        session,
        identity.org,
        identity.vendor,
        identity.vendor_product,
        job,
        product_id=identity.product.id,
        quantity_available=Decimal("10"),
        effective_at=file.uploaded_at,
        captured_at=at - timedelta(seconds=20),
    )
    data = identity.payload.definition.model_dump(mode="json")
    data["supplier"] = {**supplier_settings(), "profile_id": str(profile.id)}
    current = policies.get_policy(session, identity.operator, ctx.policy_id)
    new = policies.record_draft(
        session,
        identity.operator,
        ctx.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(data),
            reason="Synthetic supplier receipt-age and unit policy proposed",
        ),
    )
    reviewed = policy_tests.approve(session, identity, new)
    ctx.approval = reviewed.head.id
    ctx.at = datetime.now(UTC)
    session.commit()
    return Context(ctx, profile, job, file, row, stock)


@pytest.fixture
def context(db_session: Session) -> Context:
    return setup(db_session)


def check(session: Session, context: Context) -> SupplierReadinessResponse:
    ctx = context.calculation
    policy = policies.get_policy(session, ctx.policy.operator, ctx.policy_id).approved
    assert policy is not None and policy.definition.supplier is not None
    return check_supplier_readiness(
        session,
        ctx.policy.org.id,
        ctx.policy.product.id,
        ctx.policy.vendor_product.id,
        policy.definition.supplier,
        at=ctx.at,
    )


def test_complete_supplier_source_is_recorded_and_replayed(
    db_session: Session, context: Context
) -> None:
    ctx = context.calculation
    result = evaluation.preview(db_session, ctx)
    assert isinstance(result.snapshot, SupplierRecommendationSnapshot)
    assert result.snapshot.supplier.status == "VERIFIED"
    assert result.snapshot.supplier.stock_base_units == 60
    assert not result.snapshot.supplier.supplier_effective_time_verified
    [pool] = result.snapshot.pools
    assert pool.result.status == "READY" and pool.result.suggested_units == 42
    assert result.snapshot.status == "REVIEW_REQUIRED"
    assert result.snapshot.review_reasons == ("PROFITABILITY_NOT_INTEGRATED",)
    payload = evaluation.request(result)
    saved = evaluation.record(db_session, ctx, payload)
    assert saved.status == "REVIEW_REQUIRED" and not saved.purchasing_eligible
    assert saved.snapshot.rule_version == "replenishment-evaluation-v2"
    assert saved.snapshot_sha256 == policies.digest(saved.snapshot.model_dump(mode="json"))
    assert evaluation.record(db_session, ctx, payload) == saved
    assert (
        service.recommendation_page(
            db_session, ctx.policy.operator, 1, 10, status="REVIEW_REQUIRED"
        ).total
        == 1
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "SUPPLIER_IMPORT_MISSING"),
        ("running", "SUPPLIER_IMPORT_NOT_CLEAN_COMPLETE"),
        ("failed", "SUPPLIER_IMPORT_NOT_CLEAN_COMPLETE"),
        ("partial", "SUPPLIER_IMPORT_NOT_CLEAN_COMPLETE"),
        ("counts", "SUPPLIER_IMPORT_NOT_CLEAN_COMPLETE"),
        ("tied", "SUPPLIER_LATEST_IMPORT_AMBIGUOUS"),
        ("missing_sku", "SUPPLIER_LATEST_SKU_MISSING_OR_AMBIGUOUS"),
        ("profile", "SUPPLIER_IMPORT_PROFILE_CHANGED"),
        ("version", "SUPPLIER_IMPORT_PROFILE_CHANGED"),
        ("stale", "SUPPLIER_FILE_RECEIPT_STALE_OR_FUTURE"),
        ("future", "SUPPLIER_FILE_RECEIPT_STALE_OR_FUTURE"),
        ("chronology", "SUPPLIER_IMPORT_TIME_UNCONFIRMED"),
        ("capture", "SUPPLIER_SNAPSHOT_TIME_UNCONFIRMED"),
        ("effective", "SUPPLIER_SNAPSHOT_TIME_UNCONFIRMED"),
        ("fraction", "SUPPLIER_QUANTITY_PROVENANCE_UNCONFIRMED"),
        ("coercion", "SUPPLIER_QUANTITY_PROVENANCE_UNCONFIRMED"),
        ("row_quantity", "SUPPLIER_QUANTITY_PROVENANCE_UNCONFIRMED"),
        ("availability", "SUPPLIER_AVAILABILITY_PROVENANCE_UNCONFIRMED"),
        ("case", "SUPPLIER_CASE_FACTOR_CONFLICT"),
        ("warnings", "SUPPLIER_ROW_QUANTITY_WARNINGS"),
        ("foreign_file", "SUPPLIER_FILE_PROVENANCE_UNCONFIRMED"),
        ("foreign_row", "SUPPLIER_ROW_IDENTITY_UNCONFIRMED"),
    ],
)
def test_unproven_source_stays_blocked(
    db_session: Session, context: Context, fault: str, code: str
) -> None:
    ctx = context.calculation
    if fault == "missing":
        context.job.vendor_id = factories.make_vendor(db_session, ctx.policy.org).id
    elif fault in {"running", "failed", "missing_sku", "profile"}:
        file = factories.make_import_file(
            db_session, ctx.policy.org, ctx.policy.vendor, uploaded_at=ctx.at - timedelta(seconds=9)
        )
        factories.make_import_job(
            db_session,
            ctx.policy.org,
            ctx.policy.vendor,
            file,
            status=ImportJobStatus.RUNNING
            if fault == "running"
            else ImportJobStatus.FAILED
            if fault == "failed"
            else ImportJobStatus.COMPLETED,
            vendor_import_profile_id=context.profile.id if fault != "profile" else None,
            profile_version=context.profile.version,
            total_rows=1,
            processed_rows=1,
            matched_rows=1,
            created_at=ctx.at - timedelta(seconds=8),
            started_at=ctx.at - timedelta(seconds=7),
            completed_at=None if fault == "running" else ctx.at - timedelta(seconds=1),
        )
    elif fault == "tied":
        file = factories.make_import_file(db_session, ctx.policy.org, ctx.policy.vendor)
        factories.make_import_job(
            db_session, ctx.policy.org, ctx.policy.vendor, file, created_at=context.job.created_at
        )
    elif fault == "partial":
        context.job.status = ImportJobStatus.COMPLETED_WITH_ERRORS
    elif fault == "counts":
        context.job.processed_rows = 0
    elif fault == "version":
        context.job.profile_version = 99
    elif fault == "stale":
        context.file.uploaded_at = ctx.at - timedelta(days=2)
    elif fault == "future":
        context.file.uploaded_at = ctx.at + timedelta(minutes=1)
    elif fault == "chronology":
        context.job.started_at = (
            None  # DB requires chronological stored jobs; missing time still blocks.
        )
        context.job.status = ImportJobStatus.PENDING
        context.job.completed_at = None
    elif fault == "capture":
        context.stock.captured_at = ctx.at + timedelta(minutes=1)
    elif fault == "effective":
        context.stock.effective_at = context.file.uploaded_at - timedelta(minutes=1)
    elif fault in {"fraction", "row_quantity"}:
        context.stock.quantity_available = Decimal("1.5") if fault == "fraction" else Decimal("9")
    elif fault == "coercion":
        context.row.normalized_data = {**context.row.normalized_data, "quantity": "10"}
    elif fault == "availability":
        context.stock.availability_status = AvailabilityStatus.OUT_OF_STOCK
    elif fault == "case":
        context.profile.quantity_semantics = {
            "unit": "case",
            "case_pack_from": "fixed",
            "fixed_pack_size": 12,
        }
    elif fault == "warnings":
        context.row.normalized_data = {
            **context.row.normalized_data,
            "issues": [{"field": "quantity_available"}],
        }
    elif fault == "foreign_file":
        org = factories.make_organization(db_session)
        vendor = factories.make_vendor(db_session, org)
        context.job.import_file_id = factories.make_import_file(db_session, org, vendor).id
    else:
        org = factories.make_organization(db_session)
        context.row.product_id = factories.make_product(db_session, org).id
    db_session.commit()
    result = check(db_session, context)
    assert result.status == "BLOCKED" and code in result.issues
    assert result.stock_base_units is None and result.availability == "UNKNOWN"


@pytest.mark.parametrize(
    "quantity,availability,status,suggested",
    [
        (None, "AVAILABLE", "REVIEW_REQUIRED", 42),
        (0, "OUT_OF_STOCK", "BLOCKED", None),
        (1, "AVAILABLE", "REVIEW_REQUIRED", 6),
        (0, "AVAILABLE", "BLOCKED", None),
        (10, "OUT_OF_STOCK", "BLOCKED", None),
        (None, "UNKNOWN", "BLOCKED", None),
    ],
)
def test_quantity_unknown_zero_cap_and_availability_conflicts(
    db_session: Session,
    context: Context,
    quantity: int | None,
    availability: str,
    status: str,
    suggested: int | None,
) -> None:
    value = Decimal(quantity) if quantity is not None else None
    context.stock.quantity_available = value
    context.row.quantity = quantity
    context.stock.availability_status = AvailabilityStatus(availability)
    context.row.availability_status = AvailabilityStatus(availability)
    context.row.normalized_data = {
        **context.row.normalized_data,
        "quantity": quantity,
        "availability": availability,
    }
    db_session.commit()
    result = evaluation.preview(db_session, context.calculation)
    [pool] = result.snapshot.pools
    assert pool.result.status == status and pool.result.suggested_units == suggested
    assert not result.snapshot.purchasing_eligible


@pytest.mark.parametrize("change", ["quantity", "freshness", "profile", "terms"])
def test_changed_supplier_evidence_rejects_old_preview(
    db_session: Session, context: Context, change: str
) -> None:
    ctx = context.calculation
    first = evaluation.preview(db_session, ctx)
    if change == "quantity":
        context.stock.quantity_available = Decimal("12")
        context.row.quantity = 12
        context.row.normalized_data = {**context.row.normalized_data, "quantity": 12}
    elif change == "freshness":
        context.file.uploaded_at = ctx.at - timedelta(days=2)
    elif change == "profile":
        context.profile.is_active = False
    else:
        ctx.policy.vendor.minimum_order_value = Decimal("500")
    db_session.commit()
    with pytest.raises(service.RecommendationConflict):
        evaluation.record(db_session, ctx, evaluation.request(first))
    refreshed = evaluation.preview(db_session, ctx)
    if change in {"profile", "terms"}:
        assert "POLICY_IDENTITY_EVIDENCE_CHANGED" in refreshed.snapshot.policy_blocking_reasons
    saved = evaluation.record(db_session, ctx, evaluation.request(refreshed))
    assert saved.snapshot.rule_version == "replenishment-evaluation-v2"


@pytest.mark.parametrize(
    "field",
    [
        "profile_id",
        "base_units_per_source_quantity",
        "freshness_confirmation_reference",
        "availability_confirmation_reference",
    ],
)
def test_unconfirmed_supplier_settings_remain_drafts(db_session: Session, field: str) -> None:
    ctx = evaluation.setup(db_session)
    current = policies.get_policy(db_session, ctx.policy.operator, ctx.policy_id)
    data = current.head.definition.model_dump(mode="json")
    data["supplier"] = {**supplier_settings(), field: None}
    draft = policies.record_draft(
        db_session,
        ctx.policy.operator,
        ctx.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(data),
            reason="Synthetic unknown supplier choice retained",
        ),
    )
    preview = policies.preview_review(db_session, ctx.policy.manager, draft.policy_id, "APPROVE")
    assert not preview.review_allowed
    assert any(code.startswith("SUPPLIER_") for code in preview.blocking_reasons)


def test_foreign_profile_is_not_exposed_in_review(db_session: Session) -> None:
    ctx = evaluation.setup(db_session)
    foreign_org = factories.make_organization(db_session)
    foreign_vendor = factories.make_vendor(db_session, foreign_org)
    foreign_profile = factories.make_import_profile(
        db_session, foreign_org, foreign_vendor, name="Foreign private profile"
    )
    current = policies.get_policy(db_session, ctx.policy.operator, ctx.policy_id)
    data = current.head.definition.model_dump(mode="json")
    data["supplier"] = {**supplier_settings(), "profile_id": str(foreign_profile.id)}
    draft = policies.record_draft(
        db_session,
        ctx.policy.operator,
        ctx.policy_id,
        PolicyDraftRequest(
            expected_head_id=current.head.id,
            definition=PurchasingPolicyDefinition.model_validate(data),
            reason="Synthetic foreign profile must stay unknown",
        ),
    )
    preview = policies.preview_review(db_session, ctx.policy.manager, draft.policy_id, "APPROVE")
    assert preview.review_context["supplier"]["profile"] is None
    assert "Foreign private profile" not in preview.model_dump_json()


def test_actual_csv_import_supplies_accepted_provenance(
    db_session: Session, context: Context, tmp_path: Path
) -> None:
    from app.imports.storage import LocalStorageBackend
    from app.services.import_processing import process_import_job
    from app.services.imports import receive_upload
    from app.services.matching import match_import_job
    from app.services.snapshots import snapshot_import_job

    ctx = context.calculation
    storage = LocalStorageBackend(tmp_path / "supplier-raw")
    content = f"SKU,Qty\n{ctx.policy.vendor_product.vendor_sku},8\n".encode()
    outcome = receive_upload(
        db_session,
        storage,
        principal=ctx.policy.operator,
        vendor_id=ctx.policy.vendor.id,
        profile_id=context.profile.id,
        filename="synthetic-supplier.csv",
        content=content,
        declared_mime="text/csv",
        max_bytes=10000,
    )
    process_import_job(
        db_session, storage, organization_id=ctx.policy.org.id, job_id=outcome.job.id
    )
    match_import_job(db_session, organization_id=ctx.policy.org.id, job_id=outcome.job.id)
    job = snapshot_import_job(db_session, organization_id=ctx.policy.org.id, job_id=outcome.job.id)
    assert job.status == ImportJobStatus.COMPLETED
    ctx.at = datetime.now(UTC)
    preview = evaluation.preview(db_session, ctx)
    assert isinstance(preview.snapshot, SupplierRecommendationSnapshot)
    assert preview.snapshot.supplier.status == "VERIFIED", preview.snapshot.supplier.issues
    assert preview.snapshot.supplier.stock_base_units == 48
    assert preview.snapshot.supplier.evidence["job"]["id"] == str(job.id)
    saved = evaluation.record(db_session, ctx, evaluation.request(preview))
    assert (
        saved.status == "REVIEW_REQUIRED" and saved.snapshot.pools[0].result.suggested_units == 42
    )


def test_supplier_policy_downgrade_refuses_history(migrated_engine: Engine) -> None:
    from tests.integration.conftest import (
        create_database,
        downgrade_migrations,
        drop_database,
        run_migrations,
    )

    url = migrated_engine.url.set(database=f"supplier_{uuid.uuid4().hex}_test")
    create_database(url)
    engine = create_engine(url)
    try:
        run_migrations(url)
        with Session(engine) as session:
            ctx = setup(session)
            calc = ctx.calculation
            evaluation.record(session, calc, evaluation.request(evaluation.preview(session, calc)))
        with pytest.raises(DBAPIError, match="Cannot discard supplier evaluation history"):
            downgrade_migrations(url, "a61117202605")
    finally:
        engine.dispose()
        drop_database(url)
