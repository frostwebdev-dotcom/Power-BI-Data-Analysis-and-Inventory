"""Schema-wide invariants that must hold for every table, forever (AC-1.1 - AC-1.6).

These are the tests that catch a future model added without the mixins. They
inspect the *live* database created by the migration, not the ORM metadata, so
they also catch a migration that drifted from the models.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.integration import factories

# Alembic's own bookkeeping table is not ours and follows none of our
# conventions.
EXCLUDED_TABLES = ("alembic_version",)

# The tenant root itself cannot carry a tenant reference.
TABLES_WITHOUT_ORGANIZATION_ID = {"organizations"}

pytestmark = pytest.mark.integration


def _all_tables(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select table_name from information_schema.tables"
                " where table_schema = 'public' and table_type = 'BASE TABLE'"
                " and table_name <> all(:excluded) order by table_name"
            ),
            {"excluded": list(EXCLUDED_TABLES)},
        ).scalars()
        return list(rows)


def test_the_migration_creates_every_required_entity(migrated_engine: Engine) -> None:
    required = {
        "organizations",
        "users",
        "roles",
        "user_roles",
        "products",
        "product_identifiers",
        "marketplace_listings",
        "vendors",
        "vendor_products",
        "vendor_import_profiles",
        "import_files",
        "import_jobs",
        "import_job_rows",
        "vendor_inventory_snapshots",
        "product_mapping_exceptions",
        "oos_watchlist",
        "oos_status_history",
        "availability_events",
        "nineyard_sync_runs",
        "source_records",
        "audit_events",
        "amazon_sync_runs",
        "amazon_order_lines",
        "amazon_inventory_snapshots",
        "vendor_contacts",
    }

    assert required <= set(_all_tables(migrated_engine))


def test_every_primary_key_is_a_uuid(migrated_engine: Engine) -> None:
    """AC-1.1. A business key must never become the primary key (ADR 0002)."""
    with migrated_engine.connect() as connection:
        offenders = connection.execute(
            text(
                """
                select tc.table_name, kcu.column_name, c.udt_name
                from information_schema.table_constraints tc
                join information_schema.key_column_usage kcu
                  on kcu.constraint_name = tc.constraint_name
                 and kcu.table_schema = tc.table_schema
                join information_schema.columns c
                  on c.table_schema = tc.table_schema
                 and c.table_name = tc.table_name
                 and c.column_name = kcu.column_name
                where tc.constraint_type = 'PRIMARY KEY'
                  and tc.table_schema = 'public'
                  and tc.table_name <> all(:excluded)
                  and c.udt_name <> 'uuid'
                """
            ),
            {"excluded": list(EXCLUDED_TABLES)},
        ).all()

    assert offenders == [], f"non-UUID primary keys: {offenders}"


def test_no_column_stores_a_naive_timestamp(migrated_engine: Engine) -> None:
    """AC-1.2. ``TIMESTAMP WITHOUT TIME ZONE`` silently loses the offset."""
    with migrated_engine.connect() as connection:
        offenders = connection.execute(
            text(
                "select table_name, column_name from information_schema.columns"
                " where table_schema = 'public'"
                " and data_type = 'timestamp without time zone'"
            )
        ).all()

    assert offenders == [], f"naive timestamp columns: {offenders}"


def test_every_table_has_created_at_and_updated_at(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        offenders = (
            connection.execute(
                text(
                    """
                select t.table_name
                from information_schema.tables t
                where t.table_schema = 'public'
                  and t.table_type = 'BASE TABLE'
                  and t.table_name <> all(:excluded)
                  and not (
                    exists (select 1 from information_schema.columns c
                            where c.table_name = t.table_name
                              and c.column_name = 'created_at')
                    and exists (select 1 from information_schema.columns c
                                where c.table_name = t.table_name
                                  and c.column_name = 'updated_at')
                  )
                """
                ),
                {"excluded": list(EXCLUDED_TABLES)},
            )
            .scalars()
            .all()
        )

    assert offenders == [], f"tables missing timestamps: {offenders}"


def test_every_organization_owned_table_carries_organization_id(
    migrated_engine: Engine,
) -> None:
    """The tenant-scoping rule, checked structurally rather than by review."""
    tables = set(_all_tables(migrated_engine))

    with migrated_engine.connect() as connection:
        scoped = set(
            connection.execute(
                text(
                    "select table_name from information_schema.columns"
                    " where table_schema = 'public' and column_name = 'organization_id'"
                )
            )
            .scalars()
            .all()
        )

    missing = tables - scoped - TABLES_WITHOUT_ORGANIZATION_ID
    assert missing == set(), f"tables without organization_id: {sorted(missing)}"


def test_no_foreign_key_targets_a_business_key(migrated_engine: Engine) -> None:
    """AC-1.4. Every FK points at a UUID, never at a catalog number or SKU."""
    with migrated_engine.connect() as connection:
        offenders = connection.execute(
            text(
                """
                select tc.table_name, kcu.column_name, c.udt_name
                from information_schema.table_constraints tc
                join information_schema.key_column_usage kcu
                  on kcu.constraint_name = tc.constraint_name
                join information_schema.columns c
                  on c.table_name = tc.table_name
                 and c.column_name = kcu.column_name
                where tc.constraint_type = 'FOREIGN KEY'
                  and tc.table_schema = 'public'
                  and c.udt_name <> 'uuid'
                """
            )
        ).all()

    assert offenders == [], f"foreign keys on non-UUID columns: {offenders}"


def test_constraint_names_follow_the_convention(migrated_engine: Engine) -> None:
    """AC-1.6. Unnamed constraints make future Alembic migrations unreliable."""
    with migrated_engine.connect() as connection:
        offenders = connection.execute(
            text(
                """
                select conname, contype
                from pg_constraint
                where connamespace = 'public'::regnamespace
                  and contype in ('c', 'f', 'p', 'u')
                  and conrelid::regclass::text <> all(:excluded)
                  and conname !~ '^(ck|fk|pk|uq)_'
                """
            ),
            {"excluded": list(EXCLUDED_TABLES)},
        ).all()

    assert offenders == [], f"constraints not following the naming convention: {offenders}"


def test_uuid_primary_keys_are_generated_server_side(db_session: Session) -> None:
    """A row inserted by raw SQL still gets a valid key."""
    db_session.execute(
        text("insert into organizations (name, slug) values ('Raw Insert', :slug)"),
        {"slug": factories.unique("raw")},
    )
    generated = db_session.execute(
        text("select id from organizations where name = 'Raw Insert'")
    ).scalar_one()

    assert generated is not None


def test_timestamps_are_stored_in_utc_regardless_of_session_timezone(
    db_session: Session,
) -> None:
    """AC-1.3. A client in another timezone must not shift what is stored."""
    db_session.execute(text("set local timezone to 'America/New_York'"))

    organization = factories.make_organization(db_session)
    db_session.refresh(organization)

    assert organization.created_at.tzinfo is not None
    # Compare against a UTC 'now' — within a generous window, this fails loudly
    # if the value was written as local wall-clock time.
    drift = abs((datetime.now(UTC) - organization.created_at).total_seconds())
    assert drift < 300, f"created_at appears to be local time, drift={drift}s"


def test_amazon_order_lines_carry_no_buyer_or_shipping_column(migrated_engine: Engine) -> None:
    """ADR 0011: no buyer PII, ever. Checked against the live table, not the model."""
    with migrated_engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "select column_name from information_schema.columns"
                    " where table_schema = 'public' and table_name = 'amazon_order_lines'"
                )
            )
            .scalars()
            .all()
        )

    offenders = {
        c
        for c in columns
        if c.startswith(("ship_", "buyer_", "recipient_"))
        or any(word in c for word in ("address", "phone", "email", "postal", "city"))
    }
    assert offenders == set(), f"PII-shaped columns on amazon_order_lines: {sorted(offenders)}"
