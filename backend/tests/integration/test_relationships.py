"""Foreign-key wiring and delete behaviour.

Deletes are issued as Core statements rather than through the ORM, because the
point is to prove what *the database* does. An ORM delete would apply
SQLAlchemy's own cascade rules first and the test would pass without the
constraint existing at all.

The policy in one line: data that something else depends on cannot be deleted
(``RESTRICT``); rows that are part of their parent go with it (``CASCADE``);
references that are merely provenance are blanked (``SET NULL``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AmazonSyncRun,
    AuditEvent,
    ImportJob,
    ImportJobRow,
    MarketplaceListing,
    OosStatusHistory,
    OosWatchlistEntry,
    Organization,
    Product,
    ProductIdentifier,
    ProductMappingException,
    User,
    Vendor,
    VendorInventorySnapshot,
)
from app.models.enums import ActorType, IdentifierType, SourceSystem
from app.models.identity import User as UserModel
from tests.integration import factories

pytestmark = pytest.mark.integration


# --- RESTRICT: things other data depends on ----------------------------------


def test_an_organization_with_data_cannot_be_deleted(db_session: Session) -> None:
    """Removing a tenant is an ordered operation, never an unreviewed cascade."""
    organization = factories.make_organization(db_session)
    factories.make_vendor(db_session, organization)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Organization).where(Organization.id == organization.id))
        db_session.flush()


def test_a_product_referenced_by_a_vendor_mapping_cannot_be_deleted(
    db_session: Session,
) -> None:
    """Products are deactivated, never deleted, so mappings stay interpretable."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    product = factories.make_product(db_session, organization)
    user = factories.make_user(db_session, organization)
    factories.make_vendor_product(
        db_session,
        organization,
        vendor,
        product_id=product.id,
        mapping_status="APPROVED",
        mapping_approved_by_user_id=user.id,
        mapping_approved_at=datetime.now(UTC),
    )

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Product).where(Product.id == product.id))
        db_session.flush()


def test_an_import_job_with_inventory_history_cannot_be_deleted(
    db_session: Session,
) -> None:
    """Inventory history is not collateral damage of a job cleanup."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    factories.make_snapshot(db_session, organization, vendor, vendor_product, job)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(ImportJob).where(ImportJob.id == job.id))
        db_session.flush()


def test_a_snapshot_referenced_by_an_event_cannot_be_deleted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    vendor_product = factories.make_vendor_product(db_session, organization, vendor)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    snapshot = factories.make_snapshot(db_session, organization, vendor, vendor_product, job)
    factories.make_availability_event(db_session, organization, vendor, vendor_product, snapshot)

    with pytest.raises(IntegrityError):
        db_session.execute(
            delete(VendorInventorySnapshot).where(VendorInventorySnapshot.id == snapshot.id)
        )
        db_session.flush()


# --- CASCADE: rows that are part of their parent ------------------------------


def test_deleting_a_product_removes_its_identifiers_and_listings(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    factories.make_identifier(db_session, organization, product)
    factories.make_marketplace_listing(db_session, organization, product)

    db_session.execute(delete(Product).where(Product.id == product.id))
    db_session.flush()

    remaining_identifiers = db_session.execute(
        select(ProductIdentifier).where(ProductIdentifier.product_id == product.id)
    ).all()
    remaining_listings = db_session.execute(
        select(MarketplaceListing).where(MarketplaceListing.product_id == product.id)
    ).all()

    assert remaining_identifiers == []
    assert remaining_listings == []


def test_deleting_an_import_job_removes_its_staged_rows(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    factories.make_import_job_row(db_session, organization, job, row_number=1)
    factories.make_import_job_row(db_session, organization, job, row_number=2)

    db_session.execute(delete(ImportJob).where(ImportJob.id == job.id))
    db_session.flush()

    remaining = db_session.execute(
        select(ImportJobRow).where(ImportJobRow.import_job_id == job.id)
    ).all()
    assert remaining == []


def test_deleting_a_watch_entry_removes_its_status_history(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    entry = factories.make_watchlist_entry(db_session, organization, product)
    factories.make_oos_status_history(db_session, organization, entry, product)

    db_session.execute(delete(OosWatchlistEntry).where(OosWatchlistEntry.id == entry.id))
    db_session.flush()

    remaining = db_session.execute(
        select(OosStatusHistory).where(OosStatusHistory.oos_watchlist_id == entry.id)
    ).all()
    assert remaining == []


# --- SET NULL: provenance that must not block a delete ------------------------


def test_a_user_who_has_acted_cannot_be_deleted(db_session: Session) -> None:
    """Audit attribution is permanent, so the user row is not removable.

    ``SET NULL`` was the obvious first choice here and is wrong: it makes
    PostgreSQL UPDATE ``audit_events`` during the delete, which the append-only
    trigger refuses. ``RESTRICT`` states the actual rule — deactivate the user
    instead, as with every other entity in this schema.
    """
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    db_session.add(
        AuditEvent(
            organization_id=organization.id,
            actor_type=ActorType.USER,
            actor_user_id=user.id,
            actor_label=user.email,
            action="vendor.created",
            entity_type="vendor",
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(User).where(User.id == user.id))
        db_session.flush()


def test_deactivating_a_user_keeps_their_audit_entries_attributable(
    db_session: Session,
) -> None:
    """The supported way to retire a user: the trail stays intact and named."""
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    event = AuditEvent(
        organization_id=organization.id,
        actor_type=ActorType.USER,
        actor_user_id=user.id,
        actor_label=user.email,
        action="mapping.approved",
        entity_type="vendor_product",
    )
    db_session.add(event)
    db_session.flush()
    event_id = event.id

    user.is_active = False
    db_session.flush()
    db_session.expire_all()

    surviving = db_session.get(AuditEvent, event_id)
    assert surviving is not None
    assert surviving.actor_user_id == user.id
    assert surviving.actor_label == user.email


def test_a_user_who_has_never_acted_can_be_deleted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    user_id = user.id

    db_session.execute(delete(User).where(User.id == user_id))
    db_session.flush()

    assert db_session.get(User, user_id) is None


# --- Relationship navigation --------------------------------------------------


def test_a_product_exposes_its_identifiers_and_listings(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    product = factories.make_product(db_session, organization)
    factories.make_identifier(
        db_session,
        organization,
        product,
        identifier_type=IdentifierType.CATALOG_ITEM_NUMBER,
        normalized_value=product.catalog_item_number,
        source_system=SourceSystem.NINEYARD,
    )
    factories.make_marketplace_listing(db_session, organization, product)

    db_session.refresh(product)

    assert len(product.identifiers) == 1
    assert len(product.marketplace_listings) == 1
    assert product.identifiers[0].product is product


def test_a_vendor_exposes_its_products_and_profiles(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_product(db_session, organization, vendor)
    factories.make_import_profile(db_session, organization, vendor)

    db_session.refresh(vendor)

    assert len(vendor.vendor_products) == 1
    assert len(vendor.import_profiles) == 1
    assert vendor.organization.id == organization.id


def test_an_import_job_exposes_its_file_and_rows(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    import_file = factories.make_import_file(db_session, organization, vendor)
    job = factories.make_import_job(db_session, organization, vendor, import_file)
    factories.make_import_job_row(db_session, organization, job)

    db_session.refresh(job)

    assert job.import_file.id == import_file.id
    assert len(job.rows) == 1


# --- Amazon ingestion (ADR 0011) ---------------------------------------------


def test_an_amazon_run_with_order_lines_cannot_be_deleted(db_session: Session) -> None:
    """Provenance is not collateral damage of a run cleanup."""
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)
    factories.make_amazon_order_line(db_session, organization, run)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(AmazonSyncRun).where(AmazonSyncRun.id == run.id))
        db_session.flush()


def test_an_amazon_run_with_inventory_snapshots_cannot_be_deleted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)
    factories.make_amazon_inventory_snapshot(db_session, organization, run)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(AmazonSyncRun).where(AmazonSyncRun.id == run.id))
        db_session.flush()


def test_a_run_still_referenced_as_first_seen_cannot_be_deleted(db_session: Session) -> None:
    """Both provenance columns restrict, not only the most recent one."""
    organization = factories.make_organization(db_session)
    first = factories.make_amazon_sync_run(db_session, organization)
    latest = factories.make_amazon_sync_run(db_session, organization)
    factories.make_amazon_order_line(
        db_session,
        organization,
        latest,
        first_seen_sync_run_id=first.id,
        last_seen_sync_run_id=latest.id,
    )

    with pytest.raises(IntegrityError):
        db_session.execute(delete(AmazonSyncRun).where(AmazonSyncRun.id == first.id))
        db_session.flush()


def test_an_empty_amazon_run_can_be_deleted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    run = factories.make_amazon_sync_run(db_session, organization)

    db_session.execute(delete(AmazonSyncRun).where(AmazonSyncRun.id == run.id))
    db_session.flush()

    assert db_session.get(AmazonSyncRun, run.id) is None


def test_a_user_who_triggered_an_amazon_run_cannot_be_deleted(db_session: Session) -> None:
    """RESTRICT, as with audit attribution: deactivate instead."""
    organization = factories.make_organization(db_session)
    user = factories.make_user(db_session, organization)
    factories.make_amazon_sync_run(db_session, organization, triggered_by_user_id=user.id)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(UserModel).where(UserModel.id == user.id))
        db_session.flush()


def test_an_organization_with_amazon_data_cannot_be_deleted(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    factories.make_amazon_sync_run(db_session, organization)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Organization).where(Organization.id == organization.id))
        db_session.flush()


def test_an_order_line_exposes_both_provenance_runs(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    first = factories.make_amazon_sync_run(db_session, organization)
    latest = factories.make_amazon_sync_run(db_session, organization)
    line = factories.make_amazon_order_line(
        db_session,
        organization,
        latest,
        first_seen_sync_run_id=first.id,
        last_seen_sync_run_id=latest.id,
    )
    db_session.refresh(line)

    assert line.first_seen_sync_run.id == first.id
    assert line.last_seen_sync_run.id == latest.id


def test_deleting_a_listing_removes_its_queue_items(db_session: Session) -> None:
    """An exception about a listing is part of the listing (CASCADE)."""
    organization = factories.make_organization(db_session)
    listing = factories.make_marketplace_listing(db_session, organization, None)
    factories.make_mapping_exception(
        db_session, organization, None, marketplace_listing_id=listing.id
    )

    db_session.execute(delete(MarketplaceListing).where(MarketplaceListing.id == listing.id))
    db_session.flush()

    remaining = db_session.execute(
        select(ProductMappingException).where(
            ProductMappingException.marketplace_listing_id == listing.id
        )
    ).all()
    assert remaining == []


def test_a_vendor_with_contacts_cannot_be_deleted(db_session: Session) -> None:
    """Contacts are deactivated with their vendor, never cascaded away (RESTRICT)."""
    organization = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, organization)
    factories.make_vendor_contact(db_session, organization, vendor)

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Vendor).where(Vendor.id == vendor.id))
        db_session.flush()
