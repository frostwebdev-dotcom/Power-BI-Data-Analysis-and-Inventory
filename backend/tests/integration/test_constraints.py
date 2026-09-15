"""Unique and check constraints.

Each test provokes exactly one violation. Where a constraint is partial, there
is also a test proving the permitted case still works — a unique index that
rejects everything would pass a one-sided test while being useless.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    AmazonSyncJobType,
    AvailabilityEventType,
    AvailabilityStatus,
    ExceptionReason,
    ExceptionStatus,
    FileFormat,
    IdentifierType,
    ImportJobStatus,
    MappingStatus,
    SourceSystem,
    SyncStatus,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# --- Identifier uniqueness ---------------------------------------------------


def test_a_upc_resolves_to_only_one_product(db_session: Session) -> None:
    """Match priority 1 is only unambiguous if the database enforces it."""
    organization = factories.make_organization(db_session)
    first = factories.make_product(db_session, organization)
    second = factories.make_product(db_session, organization)

    factories.make_identifier(db_session, organization, first, normalized_value="00012345678905")

    with pytest.raises(IntegrityError):
        factories.make_identifier(
            db_session, organization, second, normalized_value="00012345678905"
        )


def test_the_same_upc_may_exist_in_a_different_organization(db_session: Session) -> None:
    """Uniqueness is per tenant, not global."""
    first_org = factories.make_organization(db_session)
    second_org = factories.make_organization(db_session)
    first_product = factories.make_product(db_session, first_org)
    second_product = factories.make_product(db_session, second_org)

    factories.make_identifier(
        db_session, first_org, first_product, normalized_value="00012345678905"
    )
    factories.make_identifier(
        db_session, second_org, second_product, normalized_value="00012345678905"
    )

    db_session.flush()


def test_an_inactive_identifier_frees_the_value_for_reuse(db_session: Session) -> None:
    """The unique index is partial on ``is_active``, so history stays queryable."""
    organization = factories.make_organization(db_session)
    old_product = factories.make_product(db_session, organization)
    new_product = factories.make_product(db_session, organization)

    factories.make_identifier(
        db_session,
        organization,
        old_product,
        normalized_value="00012345678905",
        is_active=False,
    )
    factories.make_identifier(
        db_session, organization, new_product, normalized_value="00012345678905"
    )

    db_session.flush()


def test_two_vendors_may_use_the_same_sku_string(db_session: Session) -> None:
    """A vendor SKU is unique within its vendor, not across all of them."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    first_vendor = factories.make_vendor(db_session, organization)
    second_vendor = factories.make_vendor(db_session, organization)

    for vendor in (first_vendor, second_vendor):
        factories.make_identifier(
            db_session,
            organization,
            product,
            identifier_type=IdentifierType.VENDOR_SKU,
            normalized_value="widget-12",
            source_system=SourceSystem.VENDOR_IMPORT,
            vendor_id=vendor.id,
        )

    db_session.flush()


def test_a_vendor_sku_identifier_requires_a_vendor(db_session: Session) -> None:
    """Context columns must match the identifier type."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_identifier(
            db_session,
            organization,
            product,
            identifier_type=IdentifierType.VENDOR_SKU,
            source_system=SourceSystem.VENDOR_IMPORT,
        )


def test_a_upc_identifier_may_not_carry_vendor_context(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_identifier(
            db_session,
            organization,
            product,
            identifier_type=IdentifierType.UPC,
            vendor_id=vendor.id,
        )


# --- Catalog and marketplace -------------------------------------------------


def test_catalog_item_number_is_unique_per_organization(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_product(db_session, organization, catalog_item_number="NY-1001")

    with pytest.raises(IntegrityError):
        factories.make_product(db_session, organization, catalog_item_number="NY-1001")


def test_a_product_may_hold_several_amazon_skus(db_session: Session) -> None:
    """One catalog item, many marketplace SKUs — never packed into one column."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    for sku in ("SKU-A", "SKU-B", "SKU-C"):
        factories.make_marketplace_listing(db_session, organization, product, seller_sku=sku)

    db_session.flush()
    db_session.refresh(product)
    assert len(product.marketplace_listings) == 3


def test_a_seller_sku_is_unique_within_a_marketplace(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    factories.make_marketplace_listing(db_session, organization, product, seller_sku="SKU-A")

    with pytest.raises(IntegrityError):
        factories.make_marketplace_listing(db_session, organization, product, seller_sku="SKU-A")


def test_an_approved_listing_must_name_its_approver(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_marketplace_listing(
            db_session,
            organization,
            product,
            mapping_status=MappingStatus.APPROVED,
        )


# --- Vendor products and mappings --------------------------------------------


def test_vendor_sku_is_unique_within_a_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_product(db_session, organization, vendor, vendor_sku="ABC-1")

    with pytest.raises(IntegrityError):
        factories.make_vendor_product(db_session, organization, vendor, vendor_sku="ABC-1")


def test_an_approved_mapping_must_point_at_a_product(db_session: Session) -> None:
    """An approved mapping with no product would break match priority 3."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_vendor_product(
            db_session,
            organization,
            vendor,
            mapping_status=MappingStatus.APPROVED,
            mapping_approved_at=datetime.now(UTC),
        )


def test_a_product_cannot_be_attached_to_an_unmapped_row(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    product = factories.make_product(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_vendor_product(
            db_session,
            organization,
            vendor,
            product_id=product.id,
            mapping_status=MappingStatus.UNMAPPED,
        )


def test_an_approved_mapping_is_accepted_when_complete(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    product = factories.make_product(db_session, organization)
    user = factories.make_user(db_session, organization)

    vendor_product = factories.make_vendor_product(
        db_session,
        organization,
        vendor,
        product_id=product.id,
        mapping_status=MappingStatus.APPROVED,
        mapping_approved_by_user_id=user.id,
        mapping_approved_at=datetime.now(UTC),
    )

    assert vendor_product.product_id == product.id


# --- Import profiles ---------------------------------------------------------


def test_only_one_active_profile_per_vendor_and_name(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_import_profile(db_session, organization, vendor, name="inventory")

    with pytest.raises(IntegrityError):
        factories.make_import_profile(db_session, organization, vendor, name="inventory", version=2)


def test_an_older_inactive_profile_version_may_coexist(db_session: Session) -> None:
    """Superseded versions stay so historical imports remain interpretable."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_import_profile(
        db_session, organization, vendor, name="inventory", version=1, is_active=False
    )
    factories.make_import_profile(db_session, organization, vendor, name="inventory", version=2)

    db_session.flush()


def test_a_sheet_selector_requires_an_xlsx_profile(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_import_profile(
            db_session,
            organization,
            vendor,
            file_format=FileFormat.CSV,
            sheet_name="Sheet1",
        )


# --- Import idempotency ------------------------------------------------------


def test_the_same_file_content_cannot_be_stored_twice(db_session: Session) -> None:
    """SHA-256 uniqueness is the file-level idempotency guarantee."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    digest = factories.fake_sha256("identical-bytes")

    factories.make_import_file(db_session, organization, vendor, sha256=digest)

    with pytest.raises(IntegrityError):
        factories.make_import_file(db_session, organization, vendor, sha256=digest)


def test_sha256_must_be_lowercase_hex(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_import_file(db_session, organization, vendor, sha256="not-a-digest")


def test_a_file_cannot_be_imported_twice_concurrently(db_session: Session) -> None:
    """One live job per file: a successful import is never silently repeated."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)

    factories.make_import_job(db_session, organization, vendor, import_file)

    with pytest.raises(IntegrityError):
        factories.make_import_job(db_session, organization, vendor, import_file)


def test_a_failed_import_may_be_retried(db_session: Session) -> None:
    """The partial index excludes FAILED and CANCELLED, so a retry is allowed."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)

    started = datetime.now(UTC)
    factories.make_import_job(
        db_session,
        organization,
        vendor,
        import_file,
        status=ImportJobStatus.FAILED,
        started_at=started,
        completed_at=started + timedelta(seconds=5),
    )
    retried = factories.make_import_job(
        db_session, organization, vendor, import_file, status=ImportJobStatus.PENDING
    )

    assert retried.status is ImportJobStatus.PENDING


def test_a_completed_job_must_record_when_it_finished(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)

    with pytest.raises(IntegrityError):
        factories.make_import_job(
            db_session,
            organization,
            vendor,
            import_file,
            status=ImportJobStatus.COMPLETED,
            started_at=datetime.now(UTC),
        )


def test_counts_cannot_be_negative(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)

    with pytest.raises(IntegrityError):
        factories.make_import_job(db_session, organization, vendor, import_file, total_rows=-1)


def test_row_numbers_are_unique_within_a_job(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)

    factories.make_import_job_row(db_session, organization, job, row_number=7)

    with pytest.raises(IntegrityError):
        factories.make_import_job_row(db_session, organization, job, row_number=7)


def test_a_matched_row_must_record_the_deciding_rule(db_session: Session) -> None:
    """An unexplained match is what the deterministic chain exists to prevent."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)

    with pytest.raises(IntegrityError):
        factories.make_import_job_row(db_session, organization, job, match_result="MATCHED")


def test_match_priority_stays_within_the_chain(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)

    with pytest.raises(IntegrityError):
        factories.make_import_job_row(db_session, organization, job, match_priority=6)


# --- Inventory history and availability --------------------------------------


def test_one_snapshot_per_vendor_line_per_import(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)

    factories.make_snapshot(db_session, organization, vendor, vendor_product, job)

    with pytest.raises(IntegrityError):
        factories.make_snapshot(db_session, organization, vendor, vendor_product, job)


def test_history_accumulates_across_imports(db_session: Session) -> None:
    """Successive imports add rows; they never overwrite the previous state."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)

    snapshots = []
    for index in range(3):
        import_file = factories.make_import_file(db_session, organization, vendor)
        job = factories.make_import_job(db_session, organization, vendor, import_file)
        snapshots.append(
            factories.make_snapshot(
                db_session,
                organization,
                vendor,
                vendor_product,
                job,
                quantity_available=Decimal(index),
                effective_at=datetime.now(UTC) + timedelta(days=index),
            )
        )

    assert len({snapshot.id for snapshot in snapshots}) == 3


def test_a_cost_requires_a_currency(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)

    with pytest.raises(IntegrityError):
        factories.make_snapshot(
            db_session,
            organization,
            vendor,
            vendor_product,
            job,
            unit_cost=Decimal("9.99"),
        )


def test_one_snapshot_raises_a_given_event_only_once(db_session: Session) -> None:
    """The de-duplication guarantee that stops repeated alerts."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    snapshot = factories.make_snapshot(db_session, organization, vendor, vendor_product, job)

    factories.make_availability_event(db_session, organization, vendor, vendor_product, snapshot)

    with pytest.raises(IntegrityError):
        factories.make_availability_event(
            db_session, organization, vendor, vendor_product, snapshot
        )


def test_an_event_must_record_an_actual_change(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    snapshot = factories.make_snapshot(db_session, organization, vendor, vendor_product, job)

    with pytest.raises(IntegrityError):
        factories.make_availability_event(
            db_session,
            organization,
            vendor,
            vendor_product,
            snapshot,
            previous_status=AvailabilityStatus.AVAILABLE,
            new_status=AvailabilityStatus.AVAILABLE,
        )


def test_the_opposite_transition_from_one_snapshot_is_still_allowed(
    db_session: Session,
) -> None:
    """The unique key includes event_type, so it de-duplicates per direction."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    snapshot = factories.make_snapshot(db_session, organization, vendor, vendor_product, job)

    factories.make_availability_event(
        db_session,
        organization,
        vendor,
        vendor_product,
        snapshot,
        event_type=AvailabilityEventType.BECAME_AVAILABLE,
    )
    factories.make_availability_event(
        db_session,
        organization,
        vendor,
        vendor_product,
        snapshot,
        event_type=AvailabilityEventType.BECAME_UNAVAILABLE,
        previous_status=AvailabilityStatus.AVAILABLE,
        new_status=AvailabilityStatus.OUT_OF_STOCK,
    )

    db_session.flush()


# --- Watchlist ---------------------------------------------------------------


def test_a_product_is_watched_once_across_all_vendors(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    factories.make_watchlist_entry(db_session, organization, product)

    with pytest.raises(IntegrityError):
        factories.make_watchlist_entry(db_session, organization, product)


def test_the_same_product_may_be_watched_per_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    first_vendor = factories.make_vendor(db_session, organization)
    second_vendor = factories.make_vendor(db_session, organization)

    factories.make_watchlist_entry(db_session, organization, product, vendor_id=first_vendor.id)
    factories.make_watchlist_entry(db_session, organization, product, vendor_id=second_vendor.id)

    db_session.flush()


def test_a_deactivated_watch_frees_the_slot(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    factories.make_watchlist_entry(
        db_session,
        organization,
        product,
        is_active=False,
        deactivated_at=datetime.now(UTC),
    )
    factories.make_watchlist_entry(db_session, organization, product)

    db_session.flush()


def test_a_watch_records_buying_intent(db_session: Session) -> None:
    """The watchlist expresses intent to purchase, not a zero-stock report."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    entry = factories.make_watchlist_entry(
        db_session,
        organization,
        product,
        desired_quantity=Decimal("24"),
        max_unit_cost=Decimal("8.5000"),
        reason="Reorder for Q4",
    )

    assert entry.desired_quantity == Decimal("24.000")


def test_desired_quantity_must_be_positive(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_watchlist_entry(
            db_session, organization, product, desired_quantity=Decimal("0")
        )


# --- Exception queue ---------------------------------------------------------


def test_an_approved_exception_must_name_product_and_approver(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(
            db_session, organization, vendor, status=ExceptionStatus.APPROVED
        )


def test_a_pending_exception_carries_no_resolution(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    user = factories.make_user(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(
            db_session,
            organization,
            vendor,
            status=ExceptionStatus.PENDING,
            resolved_by_user_id=user.id,
            resolved_at=datetime.now(UTC),
        )


def test_only_one_pending_exception_per_vendor_line(db_session: Session) -> None:
    """Re-importing an unresolvable SKU must not pile up duplicate queue items."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)

    factories.make_mapping_exception(
        db_session, organization, vendor, vendor_product_id=vendor_product.id
    )

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(
            db_session, organization, vendor, vendor_product_id=vendor_product.id
        )


def test_a_resolved_exception_frees_the_queue_slot(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    product = factories.make_product(db_session, organization)
    user = factories.make_user(db_session, organization)

    factories.make_mapping_exception(
        db_session,
        organization,
        vendor,
        vendor_product_id=vendor_product.id,
        status=ExceptionStatus.APPROVED,
        resolved_product_id=product.id,
        resolved_by_user_id=user.id,
        resolved_at=datetime.now(UTC),
    )
    factories.make_mapping_exception(
        db_session, organization, vendor, vendor_product_id=vendor_product.id
    )

    db_session.flush()


# --- Identity ----------------------------------------------------------------


def test_email_is_unique_within_an_organization(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_user(db_session, organization, email="buyer@example.test")

    with pytest.raises(IntegrityError):
        factories.make_user(db_session, organization, email="buyer@example.test")


def test_the_same_person_may_belong_to_two_organizations(db_session: Session) -> None:
    first = factories.make_organization(db_session)
    second = factories.make_organization(db_session)

    factories.make_user(db_session, first, email="buyer@example.test")
    factories.make_user(db_session, second, email="buyer@example.test")

    db_session.flush()


def test_email_must_be_stored_lowercase(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    with pytest.raises(IntegrityError):
        factories.make_user(db_session, organization, email="Buyer@Example.test")


def test_vendor_code_is_unique_within_an_organization(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_vendor(db_session, organization, code="ACME")

    with pytest.raises(IntegrityError):
        factories.make_vendor(db_session, organization, code="ACME")


# --- Amazon sync runs ----------------------------------------------------------


def test_only_one_running_amazon_run_per_job_type(db_session: Session) -> None:
    """A scheduler tick must not start a second orders pull mid-flight."""
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.ORDERS_REPORT,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(
            db_session,
            organization,
            job_type=AmazonSyncJobType.ORDERS_REPORT,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )


def test_different_job_types_may_run_concurrently(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    for job_type in AmazonSyncJobType:
        factories.make_amazon_sync_run(
            db_session,
            organization,
            job_type=job_type,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )


def test_a_finished_run_frees_the_running_slot(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    started = datetime.now(UTC)
    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.FBA_INVENTORY,
        status=SyncStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(minutes=1),
    )

    factories.make_amazon_sync_run(
        db_session,
        organization,
        job_type=AmazonSyncJobType.FBA_INVENTORY,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(UTC),
    )


def test_the_running_slot_is_per_organization(db_session: Session) -> None:
    for _ in range(2):
        organization = factories.make_organization(db_session)
        factories.make_amazon_sync_run(
            db_session,
            organization,
            job_type=AmazonSyncJobType.LISTINGS_REPORT,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )


def test_a_running_amazon_run_cannot_have_completed_at(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    started = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(
            db_session,
            organization,
            status=SyncStatus.RUNNING,
            started_at=started,
            completed_at=started + timedelta(seconds=5),
        )


def test_a_completed_amazon_run_must_have_started(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(
            db_session,
            organization,
            status=SyncStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )


def test_an_amazon_run_cannot_complete_before_it_started(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    started = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(
            db_session,
            organization,
            status=SyncStatus.COMPLETED,
            started_at=started,
            completed_at=started - timedelta(seconds=1),
        )


def test_amazon_run_counts_cannot_be_negative(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(db_session, organization, rows_failed=-1)


def test_an_amazon_run_window_cannot_end_before_it_starts(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    start = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        factories.make_amazon_sync_run(
            db_session,
            organization,
            window_start=start,
            window_end=start - timedelta(days=1),
        )


# --- Amazon order lines --------------------------------------------------------


def test_one_line_per_order_and_sku(db_session: Session) -> None:
    """The flat-file report has no order-item id; (order, SKU) is the grain."""
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)
    factories.make_amazon_order_line(
        db_session, organization, run, amazon_order_id="111-1234567-1234567", seller_sku="A"
    )

    with pytest.raises(IntegrityError):
        factories.make_amazon_order_line(
            db_session, organization, run, amazon_order_id="111-1234567-1234567", seller_sku="A"
        )


def test_the_same_order_may_carry_several_skus(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)
    for sku in ("A", "B", "C"):
        factories.make_amazon_order_line(
            db_session, organization, run, amazon_order_id="111-1234567-1234567", seller_sku=sku
        )


def test_the_same_order_id_may_exist_in_another_organization(db_session: Session) -> None:
    for _ in range(2):
        organization = factories.make_organization(db_session)
        run = factories.make_amazon_sync_run(db_session, organization)
        factories.make_amazon_order_line(
            db_session, organization, run, amazon_order_id="111-0000000-0000000", seller_sku="A"
        )


def test_quantity_ordered_cannot_be_negative(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_order_line(db_session, organization, run, quantity_ordered=-1)


def test_a_zero_quantity_line_is_allowed(db_session: Session) -> None:
    """A fully cancelled line still records that the order existed."""
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    factories.make_amazon_order_line(
        db_session, organization, run, quantity_ordered=0, order_status="Canceled"
    )


def test_an_item_price_requires_a_currency(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_order_line(
            db_session, organization, run, item_price=Decimal("19.99"), currency=None
        )


def test_an_item_price_cannot_be_negative(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_order_line(
            db_session, organization, run, item_price=Decimal("-1.00"), currency="USD"
        )


@pytest.mark.parametrize("field", ["amazon_order_id", "seller_sku"])
def test_order_line_identifiers_cannot_be_blank(db_session: Session, field: str) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_order_line(db_session, organization, run, **{field: "   "})


# --- Amazon inventory snapshots ------------------------------------------------


def test_one_inventory_snapshot_per_sku_per_run(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)
    factories.make_amazon_inventory_snapshot(db_session, organization, run, seller_sku="A")

    with pytest.raises(IntegrityError):
        factories.make_amazon_inventory_snapshot(db_session, organization, run, seller_sku="A")


def test_inventory_history_accumulates_across_runs(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    for _ in range(3):
        run = factories.make_amazon_sync_run(db_session, organization)
        factories.make_amazon_inventory_snapshot(db_session, organization, run, seller_sku="A")


@pytest.mark.parametrize(
    "field",
    [
        "fulfillable",
        "inbound_working",
        "inbound_shipped",
        "inbound_receiving",
        "reserved_total",
        "unfulfillable_total",
        "researching_total",
        "fbm_quantity",
    ],
)
def test_inventory_quantities_cannot_be_negative(db_session: Session, field: str) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_inventory_snapshot(db_session, organization, run, **{field: -1})


def test_fbm_quantity_may_be_unknown(db_session: Session) -> None:
    """Merchant-fulfilled stock comes from a different read; null is honest."""
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    snapshot = factories.make_amazon_inventory_snapshot(
        db_session, organization, run, fbm_quantity=None
    )
    db_session.refresh(snapshot)

    assert snapshot.fbm_quantity is None
    assert snapshot.fulfillable == 0  # server default, not null


def test_inventory_snapshot_sku_cannot_be_blank(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_amazon_inventory_snapshot(db_session, organization, run, seller_sku=" ")


# --- Marketplace listing mapping state (3de5c4e5def0) --------------------------------


def test_an_approved_listing_must_point_at_a_product(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_marketplace_listing(
            db_session,
            organization,
            None,
            mapping_status=MappingStatus.APPROVED,
            approved_by_user_id=user.id,
            approved_at=datetime.now(UTC),
        )


def test_a_product_cannot_be_attached_to_an_unmapped_listing(db_session: Session) -> None:
    """Mirrors vendor_products: a product arrives only through a mapping."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_marketplace_listing(
            db_session, organization, product, mapping_status=MappingStatus.UNMAPPED
        )


def test_an_unmapped_listing_without_a_product_is_accepted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    listing = factories.make_marketplace_listing(db_session, organization, None)
    db_session.refresh(listing)

    assert listing.product_id is None
    assert listing.mapping_status is MappingStatus.UNMAPPED


def test_a_pending_listing_may_carry_its_candidate(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    listing = factories.make_marketplace_listing(
        db_session, organization, product, mapping_status=MappingStatus.PENDING
    )

    assert listing.product_id == product.id


# --- AMAZON_SKU identifiers may precede their listing -----------------------------------


def test_an_amazon_sku_identifier_may_exist_without_a_listing(db_session: Session) -> None:
    """The catalog source names the SKU before Amazon reports the listing."""
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)

    identifier = factories.make_identifier(
        db_session,
        organization,
        product,
        identifier_type=IdentifierType.AMAZON_SKU,
        normalized_value="SKU-EARLY",
    )

    assert identifier.marketplace_listing_id is None


def test_an_unlinked_amazon_sku_resolves_to_one_product_per_organization(
    db_session: Session,
) -> None:
    """Two products claiming the same SKU would make priority 4 ambiguous by
    construction; the global unique index forbids it."""
    organization = factories.make_organization(db_session)
    first = factories.make_product(db_session, organization)
    second = factories.make_product(db_session, organization)
    factories.make_identifier(
        db_session,
        organization,
        first,
        identifier_type=IdentifierType.AMAZON_SKU,
        normalized_value="SKU-TWICE",
    )

    with pytest.raises(IntegrityError):
        factories.make_identifier(
            db_session,
            organization,
            second,
            identifier_type=IdentifierType.AMAZON_SKU,
            normalized_value="SKU-TWICE",
        )


def test_an_amazon_sku_identifier_still_cannot_carry_a_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_identifier(
            db_session,
            organization,
            product,
            identifier_type=IdentifierType.AMAZON_SKU,
            normalized_value="SKU-V",
            vendor_id=vendor.id,
        )


# --- Exceptions about a marketplace listing ----------------------------------------------


def test_an_exception_must_be_about_something(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(db_session, organization, None)


def test_an_exception_about_a_listing_needs_no_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    listing = factories.make_marketplace_listing(db_session, organization, None)

    exception = factories.make_mapping_exception(
        db_session, organization, None, marketplace_listing_id=listing.id
    )

    assert exception.vendor_id is None
    assert exception.marketplace_listing_id == listing.id


def test_a_vendor_product_exception_still_needs_its_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    listing = factories.make_marketplace_listing(db_session, organization, None)

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(
            db_session,
            organization,
            None,
            marketplace_listing_id=listing.id,
            vendor_product_id=vendor_product.id,
        )


def test_only_one_pending_exception_per_listing(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    listing = factories.make_marketplace_listing(db_session, organization, None)
    factories.make_mapping_exception(
        db_session, organization, None, marketplace_listing_id=listing.id
    )

    with pytest.raises(IntegrityError):
        factories.make_mapping_exception(
            db_session,
            organization,
            None,
            marketplace_listing_id=listing.id,
            reason=ExceptionReason.AMBIGUOUS_MATCH,
        )


def test_a_resolved_listing_exception_frees_the_queue_slot(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    listing = factories.make_marketplace_listing(db_session, organization, None)
    factories.make_mapping_exception(
        db_session,
        organization,
        None,
        marketplace_listing_id=listing.id,
        status=ExceptionStatus.REJECTED,
        resolved_by_user_id=user.id,
        resolved_at=datetime.now(UTC),
    )

    factories.make_mapping_exception(
        db_session, organization, None, marketplace_listing_id=listing.id
    )


# --- Vendor contacts and minimum order requirements (44c932e601b0) -----------------------


def test_a_contact_email_is_unique_per_vendor_case_insensitively(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_contact(db_session, organization, vendor, email="ann@acme.test")

    with pytest.raises(IntegrityError):
        factories.make_vendor_contact(db_session, organization, vendor, email="Ann@ACME.test")


def test_the_same_contact_email_may_exist_at_another_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    for _ in range(2):
        vendor = factories.make_vendor(db_session, organization)
        factories.make_vendor_contact(db_session, organization, vendor, email="shared@rep.test")


def test_only_one_active_primary_contact_per_vendor(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_contact(db_session, organization, vendor, is_primary=True)

    with pytest.raises(IntegrityError):
        factories.make_vendor_contact(db_session, organization, vendor, is_primary=True)


def test_an_inactive_primary_does_not_block_a_new_one(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_contact(
        db_session, organization, vendor, is_primary=True, is_active=False
    )

    factories.make_vendor_contact(db_session, organization, vendor, is_primary=True)


def test_a_contact_email_must_look_like_an_address(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_vendor_contact(db_session, organization, vendor, email="not-an-address")


def test_a_contact_name_cannot_be_blank(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        factories.make_vendor_contact(db_session, organization, vendor, name="  ")


@pytest.mark.parametrize("field", ["minimum_order_quantity", "minimum_order_value"])
def test_minimum_order_requirements_cannot_be_negative(db_session: Session, field: str) -> None:
    organization = factories.make_organization(db_session)

    with pytest.raises(IntegrityError):
        factories.make_vendor(db_session, organization, **{field: -1})


def test_minimum_order_requirements_may_be_absent(db_session: Session) -> None:
    organization = factories.make_organization(db_session)

    vendor = factories.make_vendor(db_session, organization)
    db_session.refresh(vendor)

    assert vendor.minimum_order_quantity is None
    assert vendor.minimum_order_value is None
    assert vendor.purchasing_terms == {}
