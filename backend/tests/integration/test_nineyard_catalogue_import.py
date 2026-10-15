"""A catalogue repair preserves UUIDs, approvals, provenance and uncertainty."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.imports.storage import LocalStorageBackend
from app.integrations.nineyard.catalog import SkuProductMapping
from app.integrations.nineyard.catalogue_csv import read_catalogue_export
from app.matching.engine import MatchInput, evaluate
from app.models import AuditEvent, Product, ProductIdentifier, SourceRecord
from app.models.enums import IdentifierType, MappingStatus, MatchResult, SourceSystem, TriggerType
from app.repositories.matching import MatchIndexRepository
from app.services.nineyard_catalogue_import import apply_catalogue_import, plan_catalogue_import
from app.services.nineyard_sync import run_nineyard_sync
from tests.integration import factories
from tests.integration.test_nineyard_sync import FakeSource
from tests.unit.test_nineyard_catalogue_csv import csv_bytes

pytestmark = pytest.mark.integration


def test_dry_run_then_apply_preserves_approvals_uuid_and_raw_bytes(
    db_session: Session, tmp_path: Path
) -> None:
    org = factories.make_organization(db_session)
    other_org = factories.make_organization(db_session)
    product = factories.make_product(
        db_session, org, catalog_item_number="101", nineyard_item_id="101"
    )
    other = factories.make_product(
        db_session, other_org, catalog_item_number="101", nineyard_item_id="101"
    )
    factories.make_identifier(
        db_session,
        org,
        product,
        identifier_type=IdentifierType.CATALOG_ITEM_NUMBER,
        normalized_value="101",
    )
    vendor = factories.make_vendor(db_session, org)
    user = factories.make_user(db_session, org)
    mapping = factories.make_vendor_product(
        db_session,
        org,
        vendor,
        product_id=product.id,
        mapping_status=MappingStatus.APPROVED,
        mapping_approved_by_user_id=user.id,
        mapping_approved_at=product.created_at,
    )
    listing = factories.make_marketplace_listing(
        db_session,
        org,
        product,
        mapping_status=MappingStatus.APPROVED,
        approved_by_user_id=user.id,
        approved_at=product.created_at,
    )
    content = csv_bytes(["101", "ABC-001", "Verified product", "012345678905,036000291452"])
    export = read_catalogue_export(content)
    plan = plan_catalogue_import(db_session, org.id, export)
    assert not plan.errors
    assert plan.counts["catalogue_numbers_changed"] == 1
    assert not db_session.new and not db_session.dirty
    assert product.catalog_item_number == "101"
    storage = LocalStorageBackend(tmp_path)
    report = apply_catalogue_import(
        db_session,
        org.id,
        export,
        content=content,
        file_name="export.csv",
        storage=storage,
        operator_label="Operator",
    )
    assert product.catalog_item_number == "ABC-001"
    assert other.catalog_item_number == "101"
    assert mapping.product_id == product.id and mapping.mapping_status is MappingStatus.APPROVED
    assert mapping.mapping_approved_by_user_id == user.id
    assert mapping.mapping_approved_at == product.created_at
    assert listing.product_id == product.id and listing.mapping_status is MappingStatus.APPROVED
    assert storage.open(report["raw_storage_uri"]) == content
    identifiers = list(
        db_session.scalars(
            select(ProductIdentifier).where(
                ProductIdentifier.organization_id == org.id, ProductIdentifier.is_active.is_(True)
            )
        )
    )
    assert {(i.identifier_type, i.normalized_value) for i in identifiers} == {
        (IdentifierType.CATALOG_ITEM_NUMBER, "ABC-001"),
        (IdentifierType.UPC, "00012345678905"),
        (IdentifierType.UPC, "00036000291452"),
    }
    repeat = apply_catalogue_import(
        db_session,
        org.id,
        export,
        content=content,
        file_name="export.csv",
        storage=storage,
        operator_label="Operator",
    )
    assert repeat["changed_products"] == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.organization_id == org.id)
        )
        == 2
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.organization_id == org.id)
        )
        == 1
    )


def test_shared_barcode_is_ambiguous_and_inactive_csv_only_products_do_not_match(
    db_session: Session, tmp_path: Path
) -> None:
    org = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, org)
    first = factories.make_product(db_session, org, nineyard_item_id="1")
    second = factories.make_product(db_session, org, nineyard_item_id="2")
    content = csv_bytes(
        ["1", "ABC", "First", "012345678905"],
        ["2", "DEF", "Second", "012345678905"],
        ["3", "GHI", "Unconfirmed status", "036000291452"],
    )
    apply_catalogue_import(
        db_session,
        org.id,
        read_catalogue_export(content),
        content=content,
        file_name="export.csv",
        storage=LocalStorageBackend(tmp_path),
        operator_label="Operator",
    )
    index = MatchIndexRepository(db_session, org.id).build(vendor.id)
    assert set(index.by_upc["00012345678905"]) == {first.id, second.id}
    result = evaluate(
        MatchInput(vendor_id=vendor.id, normalized_upc="00012345678905", upc_valid_checksum=True),
        index,
    )
    assert result.result is MatchResult.AMBIGUOUS
    assert "00036000291452" not in index.by_upc
    third = db_session.scalar(
        select(Product).where(Product.organization_id == org.id, Product.nineyard_item_id == "3")
    )
    assert third is not None and not third.is_active


def test_identity_conflict_prevents_all_writes_and_retention(
    db_session: Session, tmp_path: Path
) -> None:
    org = factories.make_organization(db_session)
    original = factories.make_product(
        db_session, org, catalog_item_number="ORIGINAL", nineyard_item_id="1"
    )
    factories.make_product(db_session, org, catalog_item_number="TAKEN", nineyard_item_id="2")
    content = csv_bytes(["1", "TAKEN", "Wrong target", "012345678905"])
    with pytest.raises(ValueError, match="blocked"):
        apply_catalogue_import(
            db_session,
            org.id,
            read_catalogue_export(content),
            content=content,
            file_name="export.csv",
            storage=LocalStorageBackend(tmp_path),
            operator_label="Operator",
        )
    assert original.catalog_item_number == "ORIGINAL"
    assert not db_session.new and not db_session.dirty
    assert not list(tmp_path.iterdir())


def test_later_api_sync_uses_external_id_preserves_export_and_keeps_sku_relationship(
    db_session: Session, tmp_path: Path
) -> None:
    org = factories.make_organization(db_session)
    product = factories.make_product(
        db_session, org, catalog_item_number="101", nineyard_item_id="101"
    )
    content = csv_bytes(["101", "ABC-001", "Verified product", "012345678905"])
    apply_catalogue_import(
        db_session,
        org.id,
        read_catalogue_export(content),
        content=content,
        file_name="export.csv",
        storage=LocalStorageBackend(tmp_path),
        operator_label="Operator",
    )
    run = run_nineyard_sync(
        db_session,
        org.id,
        FakeSource(
            [{"itemId": 101, "itemName": "API-OLD", "title": "API title", "vendorUPC": None}],
            [SkuProductMapping("SKU-101", (101,))],
        ),
        TriggerType.MANUAL,
    )
    assert run.items_created == 0
    assert product.catalog_item_number == "ABC-001"
    assert (
        product.attributes["nineyard_catalogue_export_sha256"]
        == hashlib.sha256(content).hexdigest()
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Product).where(Product.organization_id == org.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(ProductIdentifier.product_id).where(
                ProductIdentifier.organization_id == org.id,
                ProductIdentifier.identifier_type == IdentifierType.AMAZON_SKU,
                ProductIdentifier.normalized_value == "SKU-101",
            )
        )
        == product.id
    )
    raw = db_session.scalar(
        select(SourceRecord).where(
            SourceRecord.organization_id == org.id,
            SourceRecord.source_entity_type == "catalog_item",
        )
    )
    assert raw is not None and raw.source_record_id == "101"
    assert raw.source_system is SourceSystem.NINEYARD


def test_transaction_rollback_removes_catalogue_changes_and_audits(
    db_session: Session, tmp_path: Path
) -> None:
    org = factories.make_organization(db_session)
    product = factories.make_product(
        db_session, org, catalog_item_number="101", nineyard_item_id="101"
    )
    content = csv_bytes(["101", "ABC-001", "Product", "012345678905"])
    product_id = product.id
    with pytest.raises(RuntimeError, match="simulated failure"), db_session.begin_nested():
        apply_catalogue_import(
            db_session,
            org.id,
            read_catalogue_export(content),
            content=content,
            file_name="export.csv",
            storage=LocalStorageBackend(tmp_path),
            operator_label="Operator",
        )
        raise RuntimeError("simulated failure")
    db_session.expire_all()
    reloaded = db_session.get(Product, product_id)
    assert reloaded is not None and reloaded.catalog_item_number == "101"
    for model in (AuditEvent, SourceRecord, ProductIdentifier):
        assert (
            db_session.scalar(
                select(func.count()).select_from(model).where(model.organization_id == org.id)
            )
            == 0
        )
