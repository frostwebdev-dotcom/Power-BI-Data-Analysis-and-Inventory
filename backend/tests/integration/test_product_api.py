"""Catalogue lookup stays bounded and cannot cross organization boundaries."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.cli.staging_check import report
from app.core.config import Settings
from app.core.security import RoleCode
from app.imports.storage import build_storage_backend, sha256_hex
from app.models import Organization, User
from app.models.enums import IdentifierType, ListingStatus, SyncStatus, TriggerType
from tests.integration import factories
from tests.integration.test_exception_api import (
    Headers,
    Login,
)
from tests.integration.test_exception_api import (
    api_settings as api_settings,
)
from tests.integration.test_exception_api import (
    client as client,
)
from tests.integration.test_exception_api import (
    login as login,
)
from tests.integration.test_exception_api import (
    manager as manager,
)
from tests.integration.test_exception_api import (
    organization as organization,
)
from tests.integration.test_exception_api import (
    raw_dir as raw_dir,
)

pytestmark = pytest.mark.integration
PRODUCTS = "/api/v1/products"


def test_search_pagination_inactive_and_tenant_scope(
    client: TestClient, login: Login, db_session: Session, organization: Organization
) -> None:
    headers, _ = login(RoleCode.VIEWER)
    other = factories.make_organization(db_session)
    a = factories.make_product(
        db_session, organization, catalog_item_number="A", name="Blue Widget"
    )
    b = factories.make_product(db_session, organization, catalog_item_number="B", name="100%_Pure")
    factories.make_product(db_session, organization, catalog_item_number="C", is_active=False)
    foreign = factories.make_product(db_session, other, catalog_item_number="D", name="Blue Widget")
    factories.make_identifier(db_session, organization, a, normalized_value="00012345678905")
    factories.make_identifier(
        db_session,
        organization,
        b,
        identifier_type=IdentifierType.ASIN,
        normalized_value="B0TESTASIN",
    )
    factories.make_identifier(db_session, other, foreign, normalized_value="00012345678905")

    for page, expected in [(1, a), (2, b)]:
        response = client.get(PRODUCTS, headers=headers, params={"page": page, "page_size": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["id"] for item in body["items"]] == [str(expected.id)]
    assert (
        client.get(PRODUCTS, headers=headers, params={"include_inactive": True}).json()["total"]
        == 3
    )
    for query, expected in [
        ("blue", a),
        ("A", a),
        ("012345678905", a),
        ("012-345-678-905", a),
        ("%_", b),
        ("B0TESTASIN", b),
    ]:
        body = client.get(PRODUCTS, headers=headers, params={"q": query}).json()
        assert [item["id"] for item in body["items"]] == [str(expected.id)]
    assert client.get(PRODUCTS, headers=headers, params={"q": "missing"}).json()["total"] == 0
    assert client.get(PRODUCTS, headers=headers, params={"page_size": 201}).status_code == 422


def test_details_children_are_scoped_paginated_and_display_only(
    client: TestClient, login: Login, db_session: Session, organization: Organization
) -> None:
    headers, _ = login(RoleCode.VIEWER)
    product = factories.make_product(
        db_session, organization, attributes={"private": "not exposed"}
    )
    foreign = factories.make_product(db_session, factories.make_organization(db_session))
    for sku in ["A", "B"]:
        factories.make_marketplace_listing(
            db_session,
            organization,
            product,
            seller_sku=sku,
            raw={"item-name": f"Title {sku}", "secret": "not exposed"},
        )
    factories.make_identifier(db_session, organization, product, normalized_value="00012345678905")
    factories.make_identifier(
        db_session,
        organization,
        product,
        identifier_type=IdentifierType.ASIN,
        normalized_value="B0INACTIVE",
        is_active=False,
    )

    assert "attributes" not in client.get(f"{PRODUCTS}/{product.id}", headers=headers).json()
    identifiers = client.get(
        f"{PRODUCTS}/{product.id}/identifiers", headers=headers, params={"page_size": 1}
    ).json()
    assert identifiers["total"] == 2
    assert len(identifiers["items"]) == 1
    listings = client.get(
        f"{PRODUCTS}/{product.id}/listings", headers=headers, params={"page_size": 1, "page": 2}
    ).json()
    assert listings["total"] == 2
    assert listings["items"][0]["seller_sku"] == "B"
    assert listings["items"][0]["name"] == "Title B"
    assert "raw" not in listings["items"][0]
    for product_id in [foreign.id, uuid.uuid4()]:
        for suffix in ["", "/identifiers", "/listings"]:
            assert (
                client.get(f"{PRODUCTS}/{product_id}{suffix}", headers=headers).status_code == 404
            )
    assert client.get(PRODUCTS).status_code == 401
    assert client.get(f"{PRODUCTS}/{product.id}/identifiers").status_code == 401


def test_amazon_exception_context_and_explicit_product_selection(
    client: TestClient,
    manager: tuple[Headers, User],
    db_session: Session,
    organization: Organization,
) -> None:
    headers, user = manager
    listing = factories.make_marketplace_listing(
        db_session,
        organization,
        None,
        seller_sku="4-456164-SC-NK",
        asin="B0TESTASIN",
        listing_status=ListingStatus.ACTIVE,
        raw={"item-name": "Source perfume", "secret": "not exposed"},
    )
    exception = factories.make_mapping_exception(
        db_session, organization, None, marketplace_listing_id=listing.id
    )
    product = factories.make_product(
        db_session, organization, catalog_item_number="456164", name="Perfume"
    )
    queue = client.get("/api/v1/exceptions", headers=headers).json()["items"]
    item = next(row for row in queue if row["id"] == str(exception.id))
    assert item["source"] == "amazon"
    assert item["seller_sku"] == listing.seller_sku
    assert item["vendor_sku"] is None
    assert item["description"] == "Source perfume"
    detail = client.get(f"/api/v1/exceptions/{exception.id}", headers=headers).json()
    assert detail["listing"]["asin"] == "B0TESTASIN"
    assert detail["listing"]["name"] == "Source perfume"
    assert "raw" not in detail["listing"]
    # Reading/searching alone never approves. Only an explicit decision writes the mapping.
    assert detail["status"] == "PENDING"
    response = client.post(
        f"/api/v1/exceptions/{exception.id}/approve",
        headers=headers,
        json={"product_id": str(product.id), "note": "Checked catalogue"},
    )
    assert response.status_code == 200
    approved = response.json()["exception"]["listing"]
    assert approved["product_id"] == str(product.id)
    assert approved["mapping_status"] == "APPROVED"
    assert approved["approved_by_user_id"] == str(user.id)
    assert approved["approved_at"] is not None


def test_staging_evidence_scopes_runs_and_verifies_raw_files(
    db_session: Session, organization: Organization, api_settings: Settings
) -> None:
    foreign = factories.make_organization(db_session)
    factories.make_product(db_session, organization)
    factories.make_product(db_session, foreign)
    vendor = factories.make_vendor(db_session, organization)
    content = b"SKU,Qty\r\nABC,0\r\n"
    uri = build_storage_backend(api_settings).put(
        content, "sample.csv", organization_id=organization.id
    )
    factories.make_import_file(
        db_session,
        organization,
        vendor,
        storage_uri=uri,
        sha256=sha256_hex(content),
        size_bytes=len(content),
    )
    factories.make_amazon_sync_run(
        db_session, organization, trigger_type=TriggerType.SCHEDULED, status=SyncStatus.COMPLETED
    )
    factories.make_amazon_sync_run(
        db_session, foreign, trigger_type=TriggerType.SCHEDULED, status=SyncStatus.FAILED
    )
    evidence = report(db_session, api_settings, organization.slug)
    assert evidence["products"] == 1
    assert evidence["latest_scheduled_runs"]["FBA_INVENTORY"]["status"] == "COMPLETED"
    assert evidence["latest_scheduled_runs"]["NINEYARD_CATALOG"] is None
    assert evidence["raw_files"] == {"checked": 1, "verified": 1, "failed_ids": []}
    missing = factories.make_import_file(
        db_session, organization, vendor, storage_uri="local://missing.csv"
    )
    assert report(db_session, api_settings, organization.slug)["raw_files"]["failed_ids"] == [
        str(missing.id)
    ]
