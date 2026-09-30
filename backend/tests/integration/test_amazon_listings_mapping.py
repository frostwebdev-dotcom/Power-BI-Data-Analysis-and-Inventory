"""Mapping Amazon listings to products against a real PostgreSQL (CLAUDE.md §5).

One product carries a UPC identifier, one carries an AMAZON_SKU identifier
(what a Nineyard /api/Skus sync will populate), and a three-row listings
fixture is mapped over them. The resulting marketplace_listings,
product_mapping_exceptions and audit_events rows are asserted exactly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    MarketplaceListing,
    Organization,
    Product,
    ProductMappingException,
)
from app.models.enums import (
    ActorType,
    ExceptionReason,
    ExceptionStatus,
    IdentifierType,
    ListingStatus,
    MappingStatus,
    MatchMethod,
    SourceSystem,
)
from app.services.amazon_inventory import ParsedListing, parse_listings_report
from app.services.amazon_listings import (
    AUDIT_EXCEPTION_OPENED,
    AUDIT_EXCEPTION_RESOLVED,
    AUDIT_MAPPING_APPROVED,
    map_listings,
)
from app.services.system_user import SYSTEM_USER_EMAIL
from tests.integration import factories
from tests.unit.test_amazon_inventory_parse import HEADER, row

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
MARKETPLACE = "ATVPDKIKX0DER"

UPC = "012345678905"
UPC_CANONICAL = "00012345678905"
EAN_UNKNOWN = "4006381333931"

THREE_ROWS = (
    HEADER
    + "\n"
    + "\n".join(
        [
            # a: the catalog source already names this SKU
            row("SKU-MAPPED", id_type="1", product_id="B000MAPPED", asin="B000MAPPED"),
            # b: a UPC that resolves to exactly one product → suggestion
            row("SKU-UPC", id_type="3", product_id=UPC, asin="B000UPC001"),
            # c: an EAN nothing in the catalog carries → no match
            row(
                "SKU-NONE",
                id_type="4",
                product_id=EAN_UNKNOWN,
                asin="B000NONE01",
                status="Inactive",
            ),
        ]
    )
    + "\n"
).encode()


def parsed_rows(content: bytes = THREE_ROWS) -> list[ParsedListing]:
    result = parse_listings_report(content)
    assert result.rows_failed == 0
    return result.listings


def run_mapping(session: Session, organization: Organization, content: bytes = THREE_ROWS) -> Any:
    return map_listings(
        session, organization.id, parsed_rows(content), marketplace_id=MARKETPLACE, now=lambda: NOW
    )


def state(session: Session, organization: Organization) -> dict[str, tuple[Any, Any]]:
    return {
        sku: (listing.mapping_status, listing.product_id)
        for sku, listing in listings_by_sku(session, organization).items()
    }


def seed_catalog(session: Session, organization: Organization) -> tuple[uuid.UUID, uuid.UUID]:
    """One product reachable by UPC, one by AMAZON_SKU. Returns their ids."""
    by_upc = factories.make_product(session, organization, name="Blue Widget, 12 pack")
    factories.make_identifier(
        session,
        organization,
        by_upc,
        identifier_type=IdentifierType.UPC,
        raw_value=UPC,
        normalized_value=UPC_CANONICAL,
        has_valid_checksum=True,
    )
    by_sku = factories.make_product(session, organization, name="Red Gadget")
    factories.make_identifier(
        session,
        organization,
        by_sku,
        identifier_type=IdentifierType.AMAZON_SKU,
        raw_value="SKU-MAPPED",
        normalized_value="SKU-MAPPED",
        source_system=SourceSystem.NINEYARD,
    )
    return by_upc.id, by_sku.id


def listings_by_sku(session: Session, organization: Organization) -> dict[str, MarketplaceListing]:
    rows = session.execute(
        select(MarketplaceListing).where(MarketplaceListing.organization_id == organization.id)
    ).scalars()
    return {listing.seller_sku: listing for listing in rows}


def exceptions_by_listing(
    session: Session, organization: Organization
) -> dict[uuid.UUID, list[ProductMappingException]]:
    rows = session.execute(
        select(ProductMappingException)
        .where(ProductMappingException.organization_id == organization.id)
        .order_by(ProductMappingException.created_at)
    ).scalars()
    grouped: dict[uuid.UUID, list[ProductMappingException]] = {}
    for item in rows:
        assert item.marketplace_listing_id is not None
        grouped.setdefault(item.marketplace_listing_id, []).append(item)
    return grouped


def audit_actions(
    session: Session, organization: Organization
) -> list[tuple[str, uuid.UUID | None]]:
    rows = session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization.id,
            AuditEvent.action.like("amazon.listing.%"),
        )
        .order_by(AuditEvent.occurred_at, AuditEvent.action)
    ).scalars()
    return [(event.action, event.entity_id) for event in rows]


# --- the three-row fixture, asserted exactly ---------------------------------------------


def test_three_rows_resolve_through_the_chain_exactly(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    by_upc, by_sku = seed_catalog(db_session, organization)

    summary = run_mapping(db_session, organization)

    assert summary.as_json() == {
        "listings_seen": 3,
        "listings_created": 3,
        "listings_updated": 0,
        "approved": 1,
        "suggested": 1,
        "unmapped": 0,
        "ambiguous": 0,
        "conflicting": 0,
        "already_approved": 0,
        "left_alone": 0,
        "inactive_skipped": 1,
        "exceptions_opened": 1,
        "exceptions_resolved": 0,
    }

    listings = listings_by_sku(db_session, organization)
    assert set(listings) == {"SKU-MAPPED", "SKU-UPC", "SKU-NONE"}

    # a — approved automatically by priority 4, approver = the system user.
    mapped = listings["SKU-MAPPED"]
    assert mapped.mapping_status is MappingStatus.APPROVED
    assert mapped.product_id == by_sku
    assert mapped.mapping_method is MatchMethod.AMAZON_SKU_MAPPING
    assert mapped.approved_at == NOW
    assert mapped.approved_by is not None and mapped.approved_by.email == SYSTEM_USER_EMAIL
    assert mapped.asin == "B000MAPPED"
    assert mapped.listing_status is ListingStatus.ACTIVE
    assert mapped.raw["item-name"] == "Blue Widget, 12 pack"

    # b — a UPC hit is a suggestion: PENDING with the candidate, not approved.
    suggested = listings["SKU-UPC"]
    assert suggested.mapping_status is MappingStatus.PENDING
    assert suggested.product_id == by_upc
    assert suggested.mapping_method is MatchMethod.UPC
    assert suggested.approved_by_user_id is None and suggested.approved_at is None

    # c — nothing fired.
    unmapped = listings["SKU-NONE"]
    assert unmapped.mapping_status is MappingStatus.UNMAPPED
    assert unmapped.product_id is None
    assert unmapped.mapping_method is None
    assert unmapped.listing_status is ListingStatus.INACTIVE

    # Exceptions: current unresolved listings only. Historical inactive rows
    # remain visible as listings but do not inflate the operational queue.
    exceptions = exceptions_by_listing(db_session, organization)
    assert set(exceptions) == {suggested.id}

    [suggestion] = exceptions[suggested.id]
    assert suggestion.reason is ExceptionReason.SUGGESTION_ONLY
    assert suggestion.status is ExceptionStatus.PENDING
    assert suggestion.suggested_product_id == by_upc
    assert suggestion.vendor_id is None
    assert suggestion.candidates == {
        "products": [
            {"product_id": str(by_upc), "rule": "UPC", "priority": 1, "identifier": UPC_CANONICAL}
        ]
    }
    assert [(r["rule"], r["outcome"]) for r in suggestion.match_evaluations["rules"]] == [
        ("AMAZON_SKU_MAPPING", "no_match"),
        ("UPC", "matched"),
    ]
    assert suggestion.match_evaluations["evaluated_at"] == NOW.isoformat()

    # Audit: one approval and one queue item, both attributed to the system.
    assert sorted(audit_actions(db_session, organization)) == sorted(
        [
            (AUDIT_MAPPING_APPROVED, mapped.id),
            (AUDIT_EXCEPTION_OPENED, suggestion.id),
        ]
    )
    approval = db_session.execute(
        select(AuditEvent).where(AuditEvent.action == AUDIT_MAPPING_APPROVED)
    ).scalar_one()
    assert approval.actor_type is ActorType.SYSTEM
    assert approval.before is not None and approval.before["mapping_status"] == "UNMAPPED"
    assert approval.after is not None and approval.after["mapping_status"] == "APPROVED"
    assert "mapping_status" in (approval.changed_fields or [])


# --- idempotency and permanence ------------------------------------------------------


def test_a_second_run_changes_nothing_and_opens_nothing(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    seed_catalog(db_session, organization)
    run_mapping(db_session, organization)
    before_listings = {
        k: (v.mapping_status, v.product_id)
        for k, v in listings_by_sku(db_session, organization).items()
    }
    before_audit = audit_actions(db_session, organization)

    summary = run_mapping(db_session, organization)

    assert summary.listings_created == 0 and summary.listings_updated == 3
    assert summary.already_approved == 1
    assert summary.exceptions_opened == 0  # the open suggestion is refreshed, not duplicated
    after = {
        k: (v.mapping_status, v.product_id)
        for k, v in listings_by_sku(db_session, organization).items()
    }
    assert after == before_listings
    assert sum(len(v) for v in exceptions_by_listing(db_session, organization).values()) == 1
    assert audit_actions(db_session, organization) == before_audit


def test_an_approved_mapping_is_never_modified(db_session: Session) -> None:
    """§5.2: approved is permanent, even when the catalog now says otherwise."""
    organization = factories.make_organization(db_session)
    human_choice = factories.make_product(db_session, organization, name="What a person chose")
    reviewer = factories.make_user(db_session, organization)
    approved_at = datetime(2026, 1, 1, tzinfo=UTC)
    factories.make_marketplace_listing(
        db_session,
        organization,
        human_choice,
        seller_sku="SKU-MAPPED",
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        approved_by_user_id=reviewer.id,
        approved_at=approved_at,
        asin="OLDASIN",
    )
    # The catalog source now points the same SKU at a different product.
    seed_catalog(db_session, organization)

    summary = run_mapping(db_session, organization)

    listing = listings_by_sku(db_session, organization)["SKU-MAPPED"]
    assert listing.product_id == human_choice.id
    assert listing.mapping_status is MappingStatus.APPROVED
    assert listing.mapping_method is MatchMethod.MANUAL_APPROVAL
    assert listing.approved_by_user_id == reviewer.id
    assert listing.approved_at == approved_at
    # Only the marketplace-reported facts moved.
    assert listing.asin == "B000MAPPED"
    assert listing.listing_status is ListingStatus.ACTIVE
    assert summary.already_approved == 1 and summary.approved == 0
    assert (AUDIT_MAPPING_APPROVED, listing.id) not in audit_actions(db_session, organization)


def test_a_later_priority_4_hit_approves_a_pending_listing_and_closes_its_exception(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    by_upc, _ = seed_catalog(db_session, organization)
    run_mapping(db_session, organization)
    pending_listing = listings_by_sku(db_session, organization)["SKU-UPC"]
    assert pending_listing.mapping_status is MappingStatus.PENDING
    # The catalog source catches up and names the SKU — for the same product.
    product = db_session.get(Product, by_upc)
    assert product is not None
    factories.make_identifier(
        db_session,
        organization,
        product,
        identifier_type=IdentifierType.AMAZON_SKU,
        raw_value="SKU-UPC",
        normalized_value="SKU-UPC",
    )

    summary = run_mapping(db_session, organization)

    listing = listings_by_sku(db_session, organization)["SKU-UPC"]
    assert listing.mapping_status is MappingStatus.APPROVED
    assert listing.product_id == by_upc
    assert listing.mapping_method is MatchMethod.AMAZON_SKU_MAPPING
    [exception] = exceptions_by_listing(db_session, organization)[listing.id]
    assert exception.status is ExceptionStatus.APPROVED
    assert exception.resolved_product_id == by_upc
    assert exception.resolved_by is not None and exception.resolved_by.email == SYSTEM_USER_EMAIL
    assert exception.resolved_at == NOW
    assert summary.approved == 1 and summary.exceptions_resolved == 1
    assert (AUDIT_EXCEPTION_RESOLVED, exception.id) in audit_actions(db_session, organization)


def test_a_conflicting_identifier_is_queued_not_approved(db_session: Session) -> None:
    organization = factories.make_organization(db_session)
    by_upc, by_sku = seed_catalog(db_session, organization)
    # The SKU the catalog names carries a UPC that belongs to the *other* product.
    content = (HEADER + "\n" + row("SKU-MAPPED", id_type="3", product_id=UPC) + "\n").encode()

    summary = map_listings(
        db_session,
        organization.id,
        parsed_rows(content),
        marketplace_id=MARKETPLACE,
        now=lambda: NOW,
    )

    listing = listings_by_sku(db_session, organization)["SKU-MAPPED"]
    assert listing.mapping_status is MappingStatus.UNMAPPED
    assert listing.product_id is None
    [exception] = exceptions_by_listing(db_session, organization)[listing.id]
    assert exception.reason is ExceptionReason.CONFLICTING_IDENTIFIER
    assert {c["product_id"] for c in exception.candidates["products"]} == {str(by_sku), str(by_upc)}
    assert summary.conflicting == 1 and summary.approved == 0


def test_identical_item_names_with_different_upcs_resolve_independently(
    db_session: Session,
) -> None:
    organization = factories.make_organization(db_session)
    product_a = factories.make_product(db_session, organization, name="Same Name")
    product_b = factories.make_product(db_session, organization, name="Same Name")
    factories.make_identifier(
        db_session, organization, product_a, normalized_value=UPC_CANONICAL, has_valid_checksum=True
    )
    factories.make_identifier(
        db_session,
        organization,
        product_b,
        identifier_type=IdentifierType.EAN,
        normalized_value="04006381333931",
        has_valid_checksum=True,
    )
    content = (
        HEADER
        + "\n"
        + row("SKU-A", name="Identical Widget", id_type="3", product_id=UPC)
        + "\n"
        + row("SKU-B", name="Identical Widget", id_type="4", product_id=EAN_UNKNOWN)
        + "\n"
    ).encode()

    map_listings(
        db_session,
        organization.id,
        parsed_rows(content),
        marketplace_id=MARKETPLACE,
        now=lambda: NOW,
    )

    listings = listings_by_sku(db_session, organization)
    assert listings["SKU-A"].product_id == product_a.id
    assert listings["SKU-B"].product_id == product_b.id
    assert listings["SKU-A"].raw["item-name"] == listings["SKU-B"].raw["item-name"]


def test_mapping_is_scoped_to_the_organization(db_session: Session) -> None:
    """A catalog identifier in another tenant must be invisible (ADR 0012)."""
    other = factories.make_organization(db_session)
    seed_catalog(db_session, other)
    organization = factories.make_organization(db_session)

    summary = run_mapping(db_session, organization)

    assert summary.approved == 0 and summary.suggested == 0 and summary.unmapped == 2\n    assert summary.inactive_skipped == 1
    assert all(
        listing.product_id is None for listing in listings_by_sku(db_session, organization).values()
    )
