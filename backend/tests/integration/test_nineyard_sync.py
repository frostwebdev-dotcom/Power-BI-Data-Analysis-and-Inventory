"""Nineyard catalog synchronization against PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.nineyard.catalog import SkuProductMapping
from app.models import NineyardSyncRun, Product, ProductIdentifier, SourceRecord
from app.models.enums import IdentifierType, ListingStatus, SourceSystem, SyncStatus, TriggerType
from app.services.nineyard_sync import amazon_listing_skus, run_nineyard_sync
from tests.integration import factories

NOW = datetime(2026, 9, 26, 20, 0, tzinfo=UTC)
ITEM = {
    "itemId": 101,
    "title": "Blue Widget",
    "brand": "Example",
    "notes": "Twelve pack",
    "caseQty": 12,
    "vendorUPC": "012345678905",
    "deleteFlag": False,
    "model": "BW-12",
}


@dataclass
class FakeSource:
    items: list[dict[str, Any]]
    mappings: list[SkuProductMapping]
    fail: bool = False

    def iter_items(self) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("Nineyard disconnected")
        return self.items

    def iter_sku_mappings(self) -> list[SkuProductMapping]:
        return self.mappings


def test_only_active_tenant_amazon_skus_are_selected(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    other = factories.make_organization(db_session)
    factories.make_marketplace_listing(
        db_session,
        organization,
        None,
        seller_sku="ACTIVE-SKU",
        listing_status=ListingStatus.ACTIVE,
    )
    factories.make_marketplace_listing(
        db_session,
        organization,
        None,
        seller_sku="INACTIVE-SKU",
        is_active=False,
        listing_status=ListingStatus.ACTIVE,
    )
    factories.make_marketplace_listing(
        db_session,
        organization,
        None,
        seller_sku="AMAZON-INACTIVE-SKU",
        listing_status=ListingStatus.INACTIVE,
    )
    factories.make_marketplace_listing(
        db_session,
        other,
        None,
        seller_sku="OTHER-TENANT-SKU",
        listing_status=ListingStatus.ACTIVE,
    )

    assert amazon_listing_skus(db_session, organization.id) == ("ACTIVE-SKU",)


def test_sync_creates_catalog_identifiers_and_raw_payload(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    source = FakeSource([ITEM], [SkuProductMapping("AMZ-SKU-101", (101,))])

    run = run_nineyard_sync(
        db_session,
        organization.id,
        source,
        TriggerType.MANUAL,
        now=lambda: NOW,
    )

    assert run.status is SyncStatus.COMPLETED
    assert (run.items_seen, run.items_created, run.items_updated, run.items_unchanged) == (
        1,
        1,
        0,
        0,
    )
    product = db_session.execute(
        select(Product).where(Product.organization_id == organization.id)
    ).scalar_one()
    assert product.catalog_item_number == "101"
    assert product.name == "Blue Widget"
    assert product.pack_size == 12
    assert product.attributes == {"model": "BW-12"}

    identifiers = {
        (row.identifier_type, row.normalized_value)
        for row in db_session.execute(
            select(ProductIdentifier).where(ProductIdentifier.organization_id == organization.id)
        ).scalars()
    }
    assert (IdentifierType.CATALOG_ITEM_NUMBER, "101") in identifiers
    assert (IdentifierType.UPC, "00012345678905") in identifiers
    assert (IdentifierType.AMAZON_SKU, "AMZ-SKU-101") in identifiers

    raw = db_session.execute(
        select(SourceRecord).where(SourceRecord.organization_id == organization.id)
    ).scalar_one()
    assert raw.source_system is SourceSystem.NINEYARD
    assert raw.source_record_id == "101"
    assert raw.payload == ITEM
    assert len(raw.payload_sha256) == 64


def test_identical_second_sync_is_idempotent(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    source = FakeSource([ITEM], [SkuProductMapping("AMZ-SKU-101", (101,))])
    clock = iter((NOW, NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=1)))

    first = run_nineyard_sync(
        db_session,
        organization.id,
        source,
        TriggerType.MANUAL,
        now=lambda: next(clock),
    )
    second = run_nineyard_sync(
        db_session,
        organization.id,
        source,
        TriggerType.MANUAL,
        now=lambda: next(clock),
    )

    assert first.items_created == 1
    assert second.items_created == 0
    assert second.items_updated == 0
    assert second.items_unchanged == 1
    assert second.error_details["sku_mappings_unchanged"] == 1
    assert (
        len(
            db_session.execute(
                select(SourceRecord).where(SourceRecord.organization_id == organization.id)
            )
            .scalars()
            .all()
        )
        == 1
    )


def test_network_failure_marks_run_failed_and_preserves_catalog(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    existing = factories.make_product(
        db_session, organization, catalog_item_number="EXISTING", name="Keep me"
    )

    with pytest.raises(RuntimeError, match="disconnected"):
        run_nineyard_sync(
            db_session,
            organization.id,
            FakeSource([], [], fail=True),
            TriggerType.MANUAL,
            now=lambda: NOW,
        )

    db_session.expire_all()
    assert db_session.get(Product, existing.id) is not None
    run = db_session.execute(
        select(NineyardSyncRun).where(NineyardSyncRun.organization_id == organization.id)
    ).scalar_one()
    assert run.status is SyncStatus.FAILED
    assert run.error_message == "Nineyard disconnected"
