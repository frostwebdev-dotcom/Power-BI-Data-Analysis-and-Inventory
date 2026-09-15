"""The migration itself must be reversible and complete (AC-0.3).

This runs against its own scratch database, created and dropped by the test, so
downgrading to base cannot destroy the shared test schema other modules rely on.

The re-upgrade at the end is the part that matters most: dropping tables does
not drop the PostgreSQL enum types they used, so a downgrade that forgets them
leaves the database in a state where the next upgrade fails with
"type already exists" — a failure that only ever shows up in a real deployment.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

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
    """A throwaway database, dropped however the test ends."""
    scratch = test_database_url.set(database=f"{test_database_url.database}_roundtrip")
    try:
        if database_exists(scratch):
            drop_database(scratch)
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc.orig}")

    create_database(scratch)
    try:
        yield scratch
    finally:
        drop_database(scratch)


def _counts(url: URL) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                "tables": connection.execute(
                    text(
                        "select count(*) from information_schema.tables"
                        " where table_schema = 'public' and table_type = 'BASE TABLE'"
                    )
                ).scalar_one(),
                "enums": connection.execute(
                    text("select count(*) from pg_type where typtype = 'e'")
                ).scalar_one(),
                "indexes": connection.execute(
                    text("select count(*) from pg_indexes where schemaname = 'public'")
                ).scalar_one(),
                "triggers": connection.execute(
                    text("select count(*) from pg_trigger where not tgisinternal")
                ).scalar_one(),
            }
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_then_upgrade_succeeds(scratch_database_url: URL) -> None:
    run_migrations(scratch_database_url)
    after_first_upgrade = _counts(scratch_database_url)

    # 25 business tables plus alembic_version; 22 enum types (21 from the
    # initial schema, amazon_sync_job_type from 3767ee979011 — sync_status and
    # trigger_type are shared, not duplicated).
    assert after_first_upgrade["tables"] == 26
    assert after_first_upgrade["enums"] == 22
    assert after_first_upgrade["triggers"] == 1

    downgrade_migrations(scratch_database_url)
    after_downgrade = _counts(scratch_database_url)

    # Only alembic_version survives, and every enum type is gone.
    assert after_downgrade["tables"] == 1
    assert after_downgrade["enums"] == 0, (
        "downgrade left enum types behind; the next upgrade would fail with 'type already exists'"
    )
    assert after_downgrade["triggers"] == 0

    run_migrations(scratch_database_url)
    after_second_upgrade = _counts(scratch_database_url)

    assert after_second_upgrade == after_first_upgrade


def test_the_models_and_the_migration_do_not_drift(scratch_database_url: URL) -> None:
    """``alembic check`` must find nothing left to generate.

    This is the guard against a schema object that exists in the database but
    not in the ORM metadata. An index created only by raw DDL in a migration
    looks *stray* to autogenerate, which will happily emit a ``drop_index`` for
    it in the next revision — silently removing, say, the product-name search
    index. Declaring every object in the models keeps the two in agreement.
    """
    run_migrations(scratch_database_url)

    check_migrations(scratch_database_url)


def test_the_migration_creates_the_expected_index_coverage(
    scratch_database_url: URL,
) -> None:
    """The searches the milestone calls out must each have an index behind them."""
    run_migrations(scratch_database_url)

    engine = create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            indexes = set(
                connection.execute(
                    text("select indexname from pg_indexes where schemaname = 'public'")
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    required = {
        # Catalog Item Number
        "uq_products_organization_id_catalog_item_number",
        # UPC and every other identifier type
        "ix_product_identifiers_type_normalized_value",
        # Vendor SKU
        "uq_vendor_products_vendor_id_vendor_sku",
        "ix_vendor_products_normalized_vendor_sku",
        # Product name — btree for prefix/exact, trigram for substring search
        "ix_products_organization_id_name",
        "ix_products_name_trgm",
        # Vendor
        "uq_vendors_organization_id_code",
        "ix_vendors_organization_id_name",
        # Import status
        "ix_import_jobs_organization_id_status",
        # OOS status
        "ix_oos_watchlist_organization_id_current_status",
        "ix_oos_status_history_organization_id_new_status",
        # Event timestamp
        "ix_availability_events_organization_id_detected_at",
        "ix_audit_events_organization_id_occurred_at",
        # Amazon ingestion (ADR 0011)
        "uq_amazon_sync_runs_running_job",
        "uq_amazon_order_lines_order_sku",
        "ix_amazon_order_lines_sku_purchase_date",
        "ix_amazon_order_lines_purchase_date",
        "uq_amazon_inventory_snapshots_run_sku",
        "ix_amazon_inventory_snapshots_sku_captured_at",
        # Listing mapping (3de5c4e5def0)
        "ix_marketplace_listings_organization_id_mapping_status",
        "uq_product_mapping_exceptions_pending_listing",
        # Vendor contacts (44c932e601b0)
        "uq_vendor_contacts_vendor_id_lower_email",
        "uq_vendor_contacts_primary",
    }

    assert required <= indexes, f"missing indexes: {sorted(required - indexes)}"


def test_the_amazon_revision_reverses_without_touching_shared_enums(
    scratch_database_url: URL,
) -> None:
    """Downgrading one step must drop only the enum it created.

    ``sync_status`` and ``trigger_type`` are shared with ``nineyard_sync_runs``;
    dropping them would take that table's columns down with them.
    """
    run_migrations(scratch_database_url)
    downgrade_migrations(scratch_database_url, "506fd0ecc33a")

    engine = create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            enums = set(
                connection.execute(text("select typname from pg_type where typtype = 'e'"))
                .scalars()
                .all()
            )
            tables = set(
                connection.execute(
                    text(
                        "select table_name from information_schema.tables"
                        " where table_schema = 'public' and table_type = 'BASE TABLE'"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert "amazon_sync_job_type" not in enums
    assert {"sync_status", "trigger_type"} <= enums
    assert len(enums) == 21
    assert not {t for t in tables if t.startswith("amazon_")}
    assert "nineyard_sync_runs" in tables

    # And it comes back cleanly.
    run_migrations(scratch_database_url)
    check_migrations(scratch_database_url)


def test_the_listing_mapping_revision_reverses_and_reapplies(scratch_database_url: URL) -> None:
    """Check constraints are hand-written in 3de5c4e5def0; both directions must agree."""
    run_migrations(scratch_database_url)
    downgrade_migrations(scratch_database_url, "3767ee979011")

    engine = create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            checks = set(
                connection.execute(
                    text(
                        "select conname from pg_constraint"
                        " where connamespace = 'public'::regnamespace and contype = 'c'"
                    )
                )
                .scalars()
                .all()
            )
            listing_columns = set(
                connection.execute(
                    text(
                        "select column_name from information_schema.columns"
                        " where table_name = 'marketplace_listings'"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert "ck_marketplace_listings_approved_requires_product" not in checks
    assert "ck_product_mapping_exceptions_has_a_subject" not in checks
    assert "ck_product_identifiers_context_matches_identifier_type" in checks  # restored form
    assert "mapping_method" not in listing_columns and "raw" not in listing_columns

    run_migrations(scratch_database_url)
    check_migrations(scratch_database_url)
