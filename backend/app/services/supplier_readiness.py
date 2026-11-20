"""Verify as-stated supplier observations against explicit manager-reviewed settings."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.imports.profile_rules import ProfileRules, normalize_header
from app.models import Vendor, VendorImportProfile, VendorProduct
from app.models.enums import ImportJobStatus, ImportRowStatus, MatchResult
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.repositories.supplier_readiness import SupplierReadinessRepository
from app.schemas.supplier_readiness import SupplierPolicy, SupplierReadinessResponse
from app.services import audit


def profile_evidence(profile: VendorImportProfile) -> dict[str, Any]:
    return audit.snapshot(
        profile, exclude=frozenset({"created_at", "updated_at", "created_by_user_id"})
    )


def review_supplier_policy(
    repository: PurchasingPolicyRepository, vendor: Vendor, policy: SupplierPolicy
) -> tuple[dict[str, Any], tuple[str, ...]]:
    blocked: set[str] = set()
    profile = (
        repository.owned(VendorImportProfile, policy.profile_id) if policy.profile_id else None
    )
    if profile is None or profile.vendor_id != vendor.id:
        profile = None
        blocked.add("SUPPLIER_PROFILE_UNCONFIRMED")
    elif not profile.is_active:
        blocked.add("SUPPLIER_PROFILE_INACTIVE")
    if profile:
        try:
            ProfileRules.from_columns(
                **{
                    key: getattr(profile, key)
                    for key in (
                        "column_map",
                        "normalization_rules",
                        "availability_rules",
                        "quantity_semantics",
                        "price_semantics",
                        "pack_size_handling",
                    )
                }
            )
        except ValidationError:
            blocked.add("SUPPLIER_PROFILE_INVALID")
    if policy.base_units_per_source_quantity is None or not _reference(
        policy.quantity_confirmation_reference
    ):
        blocked.add("SUPPLIER_QUANTITY_UNITS_UNCONFIRMED")
    if not _reference(policy.freshness_confirmation_reference):
        blocked.add("SUPPLIER_RECEIPT_AGE_POLICY_UNCONFIRMED")
    if not _reference(policy.availability_confirmation_reference):
        blocked.add("SUPPLIER_AVAILABILITY_POLICY_UNCONFIRMED")
    return {
        "profile": profile_evidence(profile) if profile else None,
        "vendor_constraints": {
            "minimum_order_quantity": vendor.minimum_order_quantity,
            "minimum_order_value": str(vendor.minimum_order_value)
            if vendor.minimum_order_value is not None
            else None,
            "currency": vendor.currency,
            "purchasing_terms": vendor.purchasing_terms,
        },
    }, tuple(sorted(blocked))


def _reference(value: str | None) -> bool:
    return value is not None and len(value.strip()) >= 10


def whole_quantity(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        return None
    number = Decimal(value)
    return (
        int(number)
        if number.is_finite() and number >= 0 and number == number.to_integral_value()
        else None
    )


def availability_from_profile(rules: dict[str, Any], quantity: object, raw: dict[str, Any]) -> str:
    mode = rules.get("available_when", "quantity_gt_zero")
    if mode == "listed":
        return "AVAILABLE"
    if mode == "quantity_gt_zero":
        return (
            "UNKNOWN"
            if type(quantity) is not int or quantity < 0
            else "AVAILABLE"
            if quantity > 0
            else "OUT_OF_STOCK"
        )
    column = rules.get("status_column")
    if mode != "status_column" or not isinstance(column, str):
        return "UNKNOWN"
    values = [
        value for key, value in raw.items() if normalize_header(key) == normalize_header(column)
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        return "UNKNOWN"
    status = values[0].strip().lower()
    if status in {value.lower() for value in rules.get("available_values", [])}:
        return "AVAILABLE"
    if status in {value.lower() for value in rules.get("unavailable_values", [])}:
        return "OUT_OF_STOCK"
    return "UNKNOWN"


def check_supplier_readiness(
    session: Session,
    organization_id: uuid.UUID,
    product_id: uuid.UUID,
    vendor_product_id: uuid.UUID,
    policy: SupplierPolicy,
    *,
    at: datetime,
) -> SupplierReadinessResponse:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Supplier evaluation time must be timezone-aware")
    repository = SupplierReadinessRepository(session, organization_id)
    identities = PurchasingPolicyRepository(session, organization_id)
    line = identities.owned(VendorProduct, vendor_product_id)
    vendor = identities.owned(Vendor, line.vendor_id) if line else None
    issues: set[str] = set()
    review: set[str] = set()
    evidence: dict[str, Any] = {}
    availability = "UNKNOWN"
    stock: int | None = None

    def result() -> SupplierReadinessResponse:
        return SupplierReadinessResponse(
            evaluated_at=at,
            status="BLOCKED" if issues else "VERIFIED",
            availability=availability if not issues else "UNKNOWN",
            stock_base_units=stock if not issues else None,
            issues=tuple(sorted(issues)),
            review_reasons=tuple(sorted(review)),
            evidence=evidence,
        )

    if line is None or vendor is None or line.product_id != product_id:
        issues.add("SUPPLIER_IDENTITY_UNCONFIRMED")
        return result()
    context, policy_blocks = review_supplier_policy(identities, vendor, policy)
    evidence.update(context)
    issues.update(policy_blocks)
    if vendor.minimum_order_value is not None and vendor.minimum_order_value > 0:
        review.add("SUPPLIER_MINIMUM_VALUE_NOT_EVALUATED")
    if vendor.minimum_order_quantity or vendor.purchasing_terms:
        review.add("SUPPLIER_COMMERCIAL_TERMS_REQUIRE_REVIEW")
    if policy_blocks:
        return result()
    jobs = repository.jobs(vendor.id)
    if not jobs:
        issues.add("SUPPLIER_IMPORT_MISSING")
        return result()
    job = jobs[0]
    evidence["job"] = audit.snapshot(
        job,
        exclude=frozenset({"error_details", "error_message", "triggered_by_user_id", "updated_at"}),
    )
    if len(jobs) > 1 and jobs[1].created_at == job.created_at:
        issues.add("SUPPLIER_LATEST_IMPORT_AMBIGUOUS")
    if (
        job.status != ImportJobStatus.COMPLETED
        or job.current_stage is not None
        or job.error_rows
        or job.exception_rows
        or job.total_rows <= 0
        or job.processed_rows + job.skipped_rows != job.total_rows
        or job.matched_rows != job.processed_rows
    ):
        issues.add("SUPPLIER_IMPORT_NOT_CLEAN_COMPLETE")
    profile = (
        identities.owned(VendorImportProfile, policy.profile_id) if policy.profile_id else None
    )
    if (
        profile is None
        or profile.vendor_id != vendor.id
        or job.vendor_import_profile_id != profile.id
        or job.profile_version != profile.version
    ):
        issues.add("SUPPLIER_IMPORT_PROFILE_CHANGED")
    file = repository.file(job.import_file_id)
    if file is None or file.vendor_id != vendor.id:
        issues.add("SUPPLIER_FILE_PROVENANCE_UNCONFIRMED")
        return result()
    evidence["file"] = {
        key: value
        for key, value in audit.snapshot(file).items()
        if key
        in {
            "id",
            "sha256",
            "vendor_id",
            "organization_id",
            "size_bytes",
            "uploaded_at",
        }
    }
    if file.uploaded_at > at or at - file.uploaded_at > timedelta(
        seconds=policy.inventory_max_age_seconds
    ):
        issues.add("SUPPLIER_FILE_RECEIPT_STALE_OR_FUTURE")
    if (
        job.started_at is None
        or job.completed_at is None
        or not file.uploaded_at <= job.created_at <= job.started_at <= job.completed_at <= at
    ):
        issues.add("SUPPLIER_IMPORT_TIME_UNCONFIRMED")
    rows = repository.rows(job.id, line)
    snapshots = repository.snapshots(job.id, line.id)
    if len(rows) != 1 or len(snapshots) != 1:
        issues.add("SUPPLIER_LATEST_SKU_MISSING_OR_AMBIGUOUS")
        return result()
    row, snapshot = rows[0], snapshots[0]
    if not isinstance(cast(object, row.normalized_data), dict) or not isinstance(
        cast(object, row.raw_data), dict
    ):
        issues.add("SUPPLIER_ROW_PROVENANCE_UNCONFIRMED")
        return result()
    evidence["row"] = {
        "id": str(row.id),
        "row_number": row.row_number,
        "vendor_product_id": str(row.vendor_product_id),
        "product_id": str(row.product_id),
        "status": row.status.value,
        "match_result": row.match_result.value if row.match_result else None,
        "quantity": str(row.quantity) if row.quantity is not None else None,
        "normalized": {
            key: row.normalized_data.get(key)
            for key in (
                "quantity",
                "availability",
                "pack_size",
                "unit_of_measure",
                "issues",
            )
        },
    }
    evidence["snapshot"] = {
        key: value
        for key, value in audit.snapshot(snapshot).items()
        if key
        not in {
            "unit_cost",
            "currency",
            "created_at",
            "updated_at",
        }
    }
    if (
        row.vendor_product_id != line.id
        or row.product_id != product_id
        or row.normalized_vendor_sku != line.normalized_vendor_sku
        or snapshot.product_id != product_id
        or snapshot.vendor_id != vendor.id
        or row.status not in (ImportRowStatus.OK, ImportRowStatus.WARNING)
        or row.match_result != MatchResult.MATCHED
    ):
        issues.add("SUPPLIER_ROW_IDENTITY_UNCONFIRMED")
    if (
        snapshot.effective_at != file.uploaded_at
        or job.started_at is None
        or job.completed_at is None
        or not job.started_at <= snapshot.captured_at <= job.completed_at <= at
    ):
        issues.add("SUPPLIER_SNAPSHOT_TIME_UNCONFIRMED")
    normalized = row.normalized_data
    row_issues = normalized.get("issues")
    if not isinstance(row_issues, list) or any(
        not isinstance(value, dict)
        or not isinstance(value.get("field"), str)
        or value.get("field")
        in {
            "quantity_available",
            "quantity",
            "pack_size",
            "availability",
            "unit_of_measure",
        }
        for value in row_issues
    ):
        issues.add("SUPPLIER_ROW_QUANTITY_WARNINGS")
    reported = normalized.get("quantity")
    if snapshot.quantity_available is None and row.quantity is None and reported is None:
        stock = None
    elif (
        type(reported) is not int
        or reported < 0
        or whole_quantity(snapshot.quantity_available) != reported
        or whole_quantity(row.quantity) != reported
    ):
        issues.add("SUPPLIER_QUANTITY_PROVENANCE_UNCONFIRMED")
    elif policy.base_units_per_source_quantity is not None:
        stock = reported * policy.base_units_per_source_quantity
    if (
        normalized.get("availability") != snapshot.availability_status.value
        or row.availability_status != snapshot.availability_status
        or profile is None
        or availability_from_profile(profile.availability_rules, reported, row.raw_data)
        != snapshot.availability_status.value
    ):
        issues.add("SUPPLIER_AVAILABILITY_PROVENANCE_UNCONFIRMED")
    availability = {
        "AVAILABLE": "IN_STOCK",
        "OUT_OF_STOCK": "OUT_OF_STOCK",
        "DISCONTINUED": "OUT_OF_STOCK",
    }.get(snapshot.availability_status.value, "UNKNOWN")
    if (availability == "IN_STOCK" and stock == 0) or (
        availability == "OUT_OF_STOCK" and stock is not None and stock > 0
    ):
        issues.add("SUPPLIER_AVAILABILITY_QUANTITY_CONFLICT")
    if profile and profile.quantity_semantics.get("unit") == "case":
        semantics = profile.quantity_semantics
        pack = (
            semantics.get("fixed_pack_size")
            if semantics.get("case_pack_from") == "fixed"
            else normalized.get("pack_size")
        )
        if type(pack) is not int or pack <= 0 or pack != policy.base_units_per_source_quantity:
            issues.add("SUPPLIER_CASE_FACTOR_CONFLICT")
    return result()
