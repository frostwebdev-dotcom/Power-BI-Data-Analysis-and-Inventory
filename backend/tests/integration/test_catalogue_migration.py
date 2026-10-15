"""Backfill only proven external identities; do not discard barcode ambiguity."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, OperationalError

from tests.integration.conftest import (
    check_migrations,
    create_database,
    database_exists,
    downgrade_migrations,
    drop_database,
    run_migrations,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch_database_url(test_database_url: URL) -> Iterator[URL]:
    scratch = test_database_url.set(database=f"{test_database_url.database}_catalogue")
    try:
        if database_exists(scratch):
            drop_database(scratch)
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable for catalogue migration checks")
    create_database(scratch)
    try:
        yield scratch
    finally:
        drop_database(scratch)


def test_external_id_backfill_uses_provenance_and_roundtrips(scratch_database_url: URL) -> None:
    run_migrations(scratch_database_url, "e71a20261007")
    org_id, product_id, unbound_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_engine(scratch_database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Test', 'test')"),
            {"id": org_id},
        )
        for pid, number in ((product_id, "123"), (unbound_id, "999")):
            connection.execute(
                text("""
                INSERT INTO products (id, organization_id, catalog_item_number, name)
                VALUES (:id, :org, :number, 'Original product')
            """),
                {"id": pid, "org": org_id, "number": number},
            )
        connection.execute(
            text("""
            INSERT INTO source_records (
                id, organization_id, source_system, source_entity_type, source_record_id,
                local_entity_type, local_entity_id, payload, payload_sha256
            ) VALUES (:id, :org, 'NINEYARD', 'catalog_item', '123', 'products', :product,
                      '{"itemId":123}'::jsonb, :digest)
        """),
            {"id": uuid.uuid4(), "org": org_id, "product": product_id, "digest": "a" * 64},
        )
    run_migrations(scratch_database_url)
    with engine.connect() as connection:
        values = dict(
            connection.execute(
                text("SELECT id, nineyard_item_id FROM products WHERE organization_id = :org"),
                {"org": org_id},
            ).tuples()
        )
        assert values == {product_id: "123", unbound_id: None}
    check_migrations(scratch_database_url)
    downgrade_migrations(scratch_database_url, "e71a20261007")
    run_migrations(scratch_database_url)
    check_migrations(scratch_database_url)
    engine.dispose()


def test_downgrade_refuses_to_choose_a_shared_barcode_owner(scratch_database_url: URL) -> None:
    run_migrations(scratch_database_url)
    engine = create_engine(scratch_database_url)
    org_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Test', 'test')"),
            {"id": org_id},
        )
        for number in ("ABC", "DEF"):
            pid = uuid.uuid4()
            connection.execute(
                text("""
                INSERT INTO products (id, organization_id, catalog_item_number, name)
                VALUES (:id, :org, :number, 'Product')
            """),
                {"id": pid, "org": org_id, "number": number},
            )
            connection.execute(
                text("""
                INSERT INTO product_identifiers (
                    id, organization_id, product_id, identifier_type, raw_value,
                    normalized_value, source_system, has_valid_checksum
                ) VALUES (:id, :org, :product, 'UPC', '012345678905',
                          '00012345678905', 'NINEYARD', true)
            """),
                {"id": uuid.uuid4(), "org": org_id, "product": pid},
            )
    with pytest.raises(DBAPIError, match="Resolve shared active barcodes"):
        downgrade_migrations(scratch_database_url, "e71a20261007")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM product_identifiers")).scalar_one() == 2
        )
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "9c1520261015"
        )
    engine.dispose()
