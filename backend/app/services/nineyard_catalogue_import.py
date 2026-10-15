"""Plan or atomically apply an authoritative Nineyard Products CSV export.

No vendor/Amazon mapping is created, approved, or superseded here. Existing
product UUIDs remain the join target, and CSV-only items stay inactive until
the API confirms their lifecycle state. The caller owns commit/rollback.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.imports.storage import StorageBackend
from app.integrations.nineyard.catalogue_csv import CatalogueExport, CatalogueRow
from app.models import Product, ProductIdentifier, SourceRecord
from app.models.enums import ActorType, IdentifierType, ProductStatus, SourceSystem
from app.repositories.scoping import TenantScope
from app.services import audit
from app.services.nineyard_identity import lock_catalogue

BARCODE_TYPES = (IdentifierType.UPC, IdentifierType.EAN, IdentifierType.GTIN)


@dataclass
class CataloguePlan:
    assignments: list[tuple[CatalogueRow, Product | None]]
    identifiers: list[ProductIdentifier]
    counts: dict[str, int]
    errors: list[dict[str, Any]]


def plan_catalogue_import(
    session: Session, organization_id: uuid.UUID, export: CatalogueExport
) -> CataloguePlan:
    """Read the tenant's catalogue without changing ORM objects or writing rows."""
    scope = TenantScope(organization_id)
    products = list(session.scalars(scope.select(Product)))
    identifiers = list(session.scalars(scope.select(ProductIdentifier)))
    by_external = {p.nineyard_item_id: p for p in products if p.nineyard_item_id is not None}
    by_catalogue = {p.catalog_item_number.casefold(): p for p in products}
    catalogue_aliases = {
        identifier.normalized_value.casefold(): identifier.product_id
        for identifier in identifiers
        if identifier.is_active and identifier.identifier_type is IdentifierType.CATALOG_ITEM_NUMBER
    }
    barcode_owners: defaultdict[str, set[str]] = defaultdict(set)
    existing_barcodes: set[tuple[uuid.UUID, str]] = set()
    for identifier in identifiers:
        if identifier.is_active and identifier.identifier_type in BARCODE_TYPES:
            barcode_owners[identifier.normalized_value].add(str(identifier.product_id))
            existing_barcodes.add((identifier.product_id, identifier.normalized_value))
    counts: Counter[str] = Counter()
    errors = list(export.errors)
    assignments: list[tuple[CatalogueRow, Product | None]] = []
    assigned_ids: set[uuid.UUID] = set()
    for row in export.rows:
        product = by_external.get(row.item_id)
        named_product = by_catalogue.get(row.catalog_item_number.casefold())
        if product is None and named_product is not None:
            if named_product.nineyard_item_id is None:
                product = named_product
            else:
                errors.append({"row": row.row_number, "code": "EXTERNAL_ID_CONFLICT"})
                continue
        if product is None:
            legacy = by_catalogue.get(row.item_id.casefold())
            if legacy is not None and legacy.nineyard_item_id is None:
                errors.append({"row": row.row_number, "code": "MISSING_EXTERNAL_PROVENANCE"})
                continue
        owner = catalogue_aliases.get(row.catalog_item_number.casefold())
        if (named_product is not None and named_product is not product) or (
            owner is not None and (product is None or owner != product.id)
        ):
            errors.append({"row": row.row_number, "code": "CATALOGUE_NUMBER_CONFLICT"})
            continue
        if product is not None and product.id in assigned_ids:
            errors.append({"row": row.row_number, "code": "DUPLICATE_PRODUCT_TARGET"})
            continue
        if product is not None:
            assigned_ids.add(product.id)
            counts["existing_products"] += 1
            counts["catalogue_numbers_changed"] += (
                product.catalog_item_number != row.catalog_item_number
            )
        else:
            counts["new_products_inactive"] += 1
        for barcode in row.barcodes:
            exists = product is not None and (product.id, barcode.canonical) in existing_barcodes
            counts["barcodes_unchanged" if exists else "barcodes_added"] += 1
            barcode_owners[barcode.canonical].add(
                str(product.id) if product is not None else f"new:{row.item_id}"
            )
        assignments.append((row, product))
    counts["shared_barcodes_after_import"] = sum(len(ids) > 1 for ids in barcode_owners.values())
    counts["blocking_errors"] = len(errors)
    return CataloguePlan(assignments, identifiers, dict(counts), errors)


def apply_catalogue_import(
    session: Session,
    organization_id: uuid.UUID,
    export: CatalogueExport,
    *,
    content: bytes,
    file_name: str,
    storage: StorageBackend,
    operator_label: str,
) -> dict[str, Any]:
    """Re-plan under the same tenant lock used by API sync, then apply together."""
    if not operator_label.strip():
        raise ValueError("An operator label is required for the audit trail")
    if hashlib.sha256(content).hexdigest() != export.sha256:
        raise ValueError("CSV bytes differ from the parsed export")
    lock_catalogue(session, organization_id)
    plan = plan_catalogue_import(session, organization_id, export)
    if plan.errors:
        raise ValueError(
            f"Catalogue import blocked by {len(plan.errors)} identity/structure errors"
        )
    if not export.rows:
        raise ValueError("An empty catalogue cannot be applied")
    now = datetime.now(UTC)
    scope = TenantScope(organization_id)
    existing_file = session.scalar(
        scope.select(SourceRecord).where(
            SourceRecord.source_system == SourceSystem.NINEYARD,
            SourceRecord.source_entity_type == "catalogue_export",
            SourceRecord.source_record_id == export.sha256,
        )
    )
    if existing_file is None:
        storage_uri = storage.put(content, file_name, organization_id=organization_id, at=now)
    else:
        storage_uri = str(existing_file.payload["storage_uri"])
        if storage.open(storage_uri) != content:
            raise ValueError("Retained catalogue bytes do not match the reviewed export")
    retained = set(
        session.execute(
            scope.select(
                SourceRecord,
                SourceRecord.source_entity_type,
                SourceRecord.source_record_id,
                SourceRecord.payload_sha256,
            ).where(SourceRecord.source_system == SourceSystem.NINEYARD)
        ).tuples()
    )
    lookup = {
        (i.product_id, i.identifier_type, i.normalized_value): i
        for i in sorted(plan.identifiers, key=lambda entry: entry.is_active)
    }
    identifiers_by_product: defaultdict[uuid.UUID, list[ProductIdentifier]] = defaultdict(list)
    for existing_identifier in plan.identifiers:
        identifiers_by_product[existing_identifier.product_id].append(existing_identifier)
    changed_products = 0
    for row, product in plan.assignments:
        before = audit.snapshot(product) if product is not None else None
        before_identifiers = (
            [audit.snapshot(i) for i in identifiers_by_product[product.id] if i.is_active]
            if product is not None
            else []
        )
        if product is None:
            product = Product(
                id=uuid.uuid4(),
                organization_id=organization_id,
                catalog_item_number=row.catalog_item_number,
                nineyard_item_id=row.item_id,
                name=row.title or row.catalog_item_number,
                status=ProductStatus.INACTIVE,
                is_active=False,
                attributes={},
            )
            session.add(product)
        product.nineyard_item_id = row.item_id
        product.catalog_item_number = row.catalog_item_number
        if row.title:
            product.name = row.title
        product.attributes = {
            **product.attributes,
            "nineyard_catalogue_export_sha256": export.sha256,
        }
        for old_identifier in identifiers_by_product[product.id]:
            if (
                old_identifier.product_id == product.id
                and old_identifier.identifier_type is IdentifierType.CATALOG_ITEM_NUMBER
                and old_identifier.source_system is SourceSystem.NINEYARD
                and old_identifier.normalized_value != row.catalog_item_number
            ):
                old_identifier.is_active = False
                old_identifier.is_primary = False
        values = [
            (IdentifierType.CATALOG_ITEM_NUMBER, row.catalog_item_number, row.catalog_item_number)
        ] + [(IdentifierType.UPC, barcode.raw, barcode.canonical) for barcode in row.barcodes]
        for kind, raw, canonical in values:
            key = (product.id, kind, canonical)
            identifier = lookup.get(key)
            if identifier is None:
                identifier = ProductIdentifier(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    product_id=product.id,
                    identifier_type=kind,
                    raw_value=raw,
                    normalized_value=canonical,
                    source_system=SourceSystem.NINEYARD,
                    has_valid_checksum=True if kind is IdentifierType.UPC else None,
                    is_active=True,
                    is_primary=kind is IdentifierType.CATALOG_ITEM_NUMBER,
                )
                session.add(identifier)
                plan.identifiers.append(identifier)
                identifiers_by_product[product.id].append(identifier)
                lookup[key] = identifier
            else:
                identifier.is_active = True
                identifier.is_primary = kind is IdentifierType.CATALOG_ITEM_NUMBER
                if kind is IdentifierType.UPC:
                    identifier.has_valid_checksum = True
        after = audit.snapshot(product)
        after_identifiers = [
            audit.snapshot(i) for i in identifiers_by_product[product.id] if i.is_active
        ]
        if before != after or before_identifiers != after_identifiers:
            changed_products += 1
            audit.record(
                session,
                organization_id=organization_id,
                action="nineyard.catalogue_product_imported",
                entity_type="products",
                entity_id=product.id,
                actor_type=ActorType.SYSTEM,
                actor_label=operator_label.strip(),
                before={"product": before, "identifiers": before_identifiers},
                after={"product": after, "identifiers": after_identifiers},
                summary="Imported verified catalogue identity and barcodes from Products export.",
            )
        payload_hash = hashlib.sha256(
            json.dumps(row.raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        source_key = ("catalogue_export_product", row.item_id, payload_hash)
        if source_key not in retained:
            session.add(
                SourceRecord(
                    organization_id=organization_id,
                    source_system=SourceSystem.NINEYARD,
                    source_entity_type=source_key[0],
                    source_record_id=row.item_id,
                    local_entity_type="products",
                    local_entity_id=product.id,
                    payload=row.raw,
                    payload_sha256=payload_hash,
                    fetched_at=now,
                )
            )
            retained.add(source_key)
    if existing_file is None:
        metadata = {
            "storage_uri": storage_uri,
            "file_name": file_name,
            "size_bytes": len(content),
            "sha256": export.sha256,
            "counts": export.counts,
        }
        metadata_hash = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        session.add(
            SourceRecord(
                organization_id=organization_id,
                source_system=SourceSystem.NINEYARD,
                source_entity_type="catalogue_export",
                source_record_id=export.sha256,
                payload=metadata,
                payload_sha256=metadata_hash,
                fetched_at=now,
            )
        )
    session.flush()
    return {
        "mode": "applied",
        "sha256": export.sha256,
        "file": export.counts,
        "database": plan.counts,
        "changed_products": changed_products,
        "raw_storage_uri": storage_uri,
    }
