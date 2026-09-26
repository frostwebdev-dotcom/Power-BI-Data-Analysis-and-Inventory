"""Synchronize Nineyard catalog items and approved Amazon SKU relationships.

All network reads finish before catalog writes begin.  A transport failure in
the middle of pagination therefore marks the run failed while leaving the last
successful catalog intact.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.transaction import transaction
from app.integrations.nineyard.catalog import SkuProductMapping
from app.matching.normalize import normalize_gtin
from app.models.catalog import Product, ProductIdentifier
from app.models.enums import (
    ActorType,
    IdentifierType,
    ProductStatus,
    SourceSystem,
    SyncStatus,
    TriggerType,
)
from app.models.sync import NineyardSyncRun, SourceRecord
from app.repositories.scoping import TenantScope
from app.services import audit

_logger = get_logger(__name__)

AUDIT_ACTION_COMPLETED: Final = "nineyard.sync.completed"
AUDIT_ACTION_FAILED: Final = "nineyard.sync.failed"
AUDIT_ACTOR_LABEL: Final = "nineyard-catalog-sync"


class CatalogSource(Protocol):
    def iter_items(self) -> Iterable[dict[str, Any]]: ...

    def iter_sku_mappings(self) -> Iterable[SkuProductMapping]: ...


def run_nineyard_sync(
    session: Session,
    organization_id: uuid.UUID,
    source: CatalogSource,
    trigger_type: TriggerType,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> NineyardSyncRun:
    """Pull a complete snapshot and atomically apply it to one organization."""
    started_at = now()
    run = NineyardSyncRun(
        organization_id=organization_id,
        status=SyncStatus.RUNNING,
        trigger_type=trigger_type,
        started_at=started_at,
    )
    with transaction(session):
        session.add(run)

    failure: BaseException | None = None
    try:
        # Materialize the complete remote snapshot first.  No local catalog
        # change occurs if pagination or authentication fails halfway through.
        items = list(source.iter_items())
        sku_mappings = list(source.iter_sku_mappings())
        completed_at = now()
        with transaction(session):
            run = session.merge(run)
            counts, mapping_details = _apply_snapshot(
                session,
                organization_id,
                run,
                items,
                sku_mappings,
                seen_at=completed_at,
            )
            for field, value in counts.items():
                setattr(run, field, value)
            run.error_details = mapping_details
            run.status = (
                SyncStatus.COMPLETED_WITH_ERRORS if counts["items_failed"] else SyncStatus.COMPLETED
            )
            run.completed_at = completed_at
            audit.record(
                session,
                organization_id=organization_id,
                action=AUDIT_ACTION_COMPLETED,
                entity_type=NineyardSyncRun.__tablename__,
                entity_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_label=AUDIT_ACTOR_LABEL,
                after=audit.snapshot(run),
                summary=(
                    f"Nineyard sync {run.status.value.lower()}: {run.items_seen} items seen, "
                    f"{run.items_created} created, {run.items_updated} updated, "
                    f"{mapping_details['sku_identifiers_created']} SKU mappings created"
                ),
            )
        _logger.info(
            "nineyard.sync.completed",
            run_id=str(run.id),
            status=run.status.value,
            items_seen=run.items_seen,
            sku_identifiers_created=mapping_details["sku_identifiers_created"],
        )
        return run
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if failure is not None:
            session.rollback()
            with transaction(session):
                run = session.merge(run)
                run.status = SyncStatus.FAILED
                run.completed_at = now()
                run.error_message = str(failure)
                run.error_details = {
                    "exception": type(failure).__name__,
                    "message": str(failure),
                }
                audit.record(
                    session,
                    organization_id=organization_id,
                    action=AUDIT_ACTION_FAILED,
                    entity_type=NineyardSyncRun.__tablename__,
                    entity_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_label=AUDIT_ACTOR_LABEL,
                    after=audit.snapshot(run),
                    summary=f"Nineyard sync failed: {failure}",
                )
            _logger.error(
                "nineyard.sync.failed",
                run_id=str(run.id),
                error_type=type(failure).__name__,
                error_message=str(failure),
            )


def _apply_snapshot(
    session: Session,
    organization_id: uuid.UUID,
    run: NineyardSyncRun,
    items: list[dict[str, Any]],
    sku_mappings: list[SkuProductMapping],
    *,
    seen_at: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    scope = TenantScope(organization_id)
    products = {
        product.catalog_item_number: product
        for product in session.execute(scope.select(Product)).scalars()
    }
    identifiers = {
        (identifier.identifier_type, identifier.normalized_value): identifier
        for identifier in session.execute(scope.select(ProductIdentifier)).scalars()
        if identifier.is_active
    }
    source_hashes = {
        (record.source_entity_type, record.source_record_id, record.payload_sha256)
        for record in session.execute(scope.select(SourceRecord)).scalars()
        if record.source_system is SourceSystem.NINEYARD
    }

    counts = {
        "items_seen": 0,
        "items_created": 0,
        "items_updated": 0,
        "items_unchanged": 0,
        "items_removed": 0,
        "items_failed": 0,
    }
    details = {
        "sku_mappings_seen": len(sku_mappings),
        "sku_identifiers_created": 0,
        "sku_mappings_unchanged": 0,
        "sku_mappings_skipped": 0,
        "upc_identifiers_created": 0,
        "identifier_conflicts": 0,
    }

    for payload in items:
        counts["items_seen"] += 1
        item_id = payload.get("itemId")
        if not isinstance(item_id, int):
            counts["items_failed"] += 1
            continue
        catalog_number = str(item_id)
        product = products.get(catalog_number)
        values = _product_values(payload, item_id=item_id, seen_at=seen_at)
        if product is None:
            product = Product(
                organization_id=organization_id,
                catalog_item_number=catalog_number,
                **values,
            )
            session.add(product)
            session.flush()
            products[catalog_number] = product
            counts["items_created"] += 1
        else:
            changed = any(
                field != "nineyard_last_seen_at" and getattr(product, field) != value
                for field, value in values.items()
            )
            for field, value in values.items():
                setattr(product, field, value)
            counts["items_updated" if changed else "items_unchanged"] += 1
        if payload.get("deleteFlag") is True:
            counts["items_removed"] += 1

        _ensure_identifier(
            session,
            identifiers,
            organization_id,
            product,
            IdentifierType.CATALOG_ITEM_NUMBER,
            catalog_number,
            catalog_number,
        )
        raw_upc = payload.get("vendorUPC")
        if isinstance(raw_upc, str) and raw_upc.strip():
            normalized = normalize_gtin(raw_upc)
            if normalized.usable and normalized.canonical is not None:
                outcome = _ensure_identifier(
                    session,
                    identifiers,
                    organization_id,
                    product,
                    IdentifierType.UPC,
                    raw_upc,
                    normalized.canonical,
                    has_valid_checksum=True,
                )
                if outcome == "created":
                    details["upc_identifiers_created"] += 1
                elif outcome == "conflict":
                    details["identifier_conflicts"] += 1

        payload_hash = _payload_hash(payload)
        source_key = ("catalog_item", catalog_number, payload_hash)
        if source_key not in source_hashes:
            session.add(
                SourceRecord(
                    organization_id=organization_id,
                    source_system=SourceSystem.NINEYARD,
                    source_entity_type="catalog_item",
                    source_record_id=catalog_number,
                    local_entity_type=Product.__tablename__,
                    local_entity_id=product.id,
                    nineyard_sync_run_id=run.id,
                    payload=payload,
                    payload_sha256=payload_hash,
                    fetched_at=seen_at,
                )
            )
            source_hashes.add(source_key)

    for mapping in sku_mappings:
        if len(mapping.item_ids) != 1:
            details["sku_mappings_skipped"] += 1
            continue
        product = products.get(str(mapping.item_ids[0]))
        if product is None:
            details["sku_mappings_skipped"] += 1
            continue
        outcome = _ensure_identifier(
            session,
            identifiers,
            organization_id,
            product,
            IdentifierType.AMAZON_SKU,
            mapping.seller_sku,
            mapping.seller_sku.strip(),
        )
        if outcome == "created":
            details["sku_identifiers_created"] += 1
        elif outcome == "unchanged":
            details["sku_mappings_unchanged"] += 1
        else:
            details["identifier_conflicts"] += 1
            details["sku_mappings_skipped"] += 1

    session.flush()
    return counts, details


def _product_values(
    payload: Mapping[str, Any], *, item_id: int, seen_at: datetime
) -> dict[str, Any]:
    name = (
        _text(payload.get("title")) or _text(payload.get("itemName")) or f"Nineyard item {item_id}"
    )
    case_qty = payload.get("caseQty")
    deleted = payload.get("deleteFlag") is True
    attributes = {
        key: payload[key]
        for key in (
            "model",
            "length",
            "height",
            "width",
            "weight",
            "imageUrl",
            "caseRoundingSetting",
        )
        if payload.get(key) is not None
    }
    return {
        "name": name,
        "brand": _text(payload.get("brand")),
        "description": _text(payload.get("notes")),
        "pack_size": case_qty if isinstance(case_qty, int) and case_qty > 0 else None,
        "status": ProductStatus.INACTIVE if deleted else ProductStatus.ACTIVE,
        "is_active": not deleted,
        "nineyard_last_seen_at": seen_at,
        "attributes": attributes,
    }


def _ensure_identifier(
    session: Session,
    identifiers: dict[tuple[IdentifierType, str], ProductIdentifier],
    organization_id: uuid.UUID,
    product: Product,
    identifier_type: IdentifierType,
    raw_value: str,
    normalized_value: str,
    *,
    has_valid_checksum: bool | None = None,
) -> str:
    key = (identifier_type, normalized_value)
    existing = identifiers.get(key)
    if existing is not None:
        if existing.product_id != product.id:
            return "conflict"
        existing.raw_value = raw_value
        existing.is_active = True
        if has_valid_checksum is not None:
            existing.has_valid_checksum = has_valid_checksum
        return "unchanged"
    identifier = ProductIdentifier(
        organization_id=organization_id,
        product_id=product.id,
        identifier_type=identifier_type,
        raw_value=raw_value,
        normalized_value=normalized_value,
        source_system=SourceSystem.NINEYARD,
        has_valid_checksum=has_valid_checksum,
        is_primary=identifier_type is IdentifierType.CATALOG_ITEM_NUMBER,
        is_active=True,
    )
    session.add(identifier)
    session.flush()
    identifiers[key] = identifier
    return "created"


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
