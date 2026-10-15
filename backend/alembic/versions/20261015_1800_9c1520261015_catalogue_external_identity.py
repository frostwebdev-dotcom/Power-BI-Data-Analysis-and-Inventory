"""Separate Nineyard API identity and retain ambiguous barcode owners.

Revision ID: 9c1520261015
Revises: e71a20261007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c1520261015"
down_revision: str | None = "e71a20261007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("nineyard_item_id", sa.Text(), nullable=True))
    # Only retained Nineyard API provenance establishes the external ID. Never
    # infer it from a numeric-looking business catalogue number.
    op.execute("""
        WITH links AS (
            SELECT organization_id, local_entity_id, min(source_record_id) AS item_id
            FROM source_records
            WHERE source_system = 'NINEYARD' AND source_entity_type = 'catalog_item'
              AND local_entity_type = 'products' AND source_record_id ~ '^[0-9]+$'
            GROUP BY organization_id, local_entity_id
            HAVING count(DISTINCT source_record_id) = 1
        )
        UPDATE products p SET nineyard_item_id = links.item_id
        FROM links
        WHERE p.id = links.local_entity_id AND p.organization_id = links.organization_id
          AND NOT EXISTS (
              SELECT 1 FROM links other
              WHERE other.organization_id = links.organization_id
                AND other.item_id = links.item_id
                AND other.local_entity_id <> links.local_entity_id
          )
    """)
    op.create_index(
        "uq_products_organization_id_nineyard_item_id",
        "products",
        ["organization_id", "nineyard_item_id"],
        unique=True,
    )
    op.drop_index("uq_product_identifiers_global_value", table_name="product_identifiers")
    op.create_index(
        "uq_product_identifiers_global_value",
        "product_identifiers",
        ["organization_id", "identifier_type", "normalized_value"],
        unique=True,
        postgresql_where=sa.text(
            "is_active and vendor_id is null and marketplace_listing_id is null"
            " and identifier_type not in ('UPC', 'EAN', 'GTIN')"
        ),
    )
    op.create_index(
        "uq_product_identifiers_barcode_value",
        "product_identifiers",
        ["organization_id", "product_id", "identifier_type", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("is_active and identifier_type in ('UPC', 'EAN', 'GTIN')"),
    )


def downgrade() -> None:
    # Refuse a lossy downgrade. Choosing a barcode owner would silently change
    # matching outcomes. An operator must explicitly resolve duplicates first.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM product_identifiers
                WHERE is_active AND identifier_type IN ('UPC', 'EAN', 'GTIN')
                GROUP BY organization_id, identifier_type, normalized_value
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Resolve shared active barcodes before downgrading catalogue identity';
            END IF;
        END $$
    """)
    op.drop_index("uq_product_identifiers_barcode_value", table_name="product_identifiers")
    op.drop_index("uq_product_identifiers_global_value", table_name="product_identifiers")
    op.create_index(
        "uq_product_identifiers_global_value",
        "product_identifiers",
        ["organization_id", "identifier_type", "normalized_value"],
        unique=True,
        postgresql_where=sa.text(
            "is_active and vendor_id is null and marketplace_listing_id is null"
        ),
    )
    op.drop_index("uq_products_organization_id_nineyard_item_id", table_name="products")
    op.drop_column("products", "nineyard_item_id")
