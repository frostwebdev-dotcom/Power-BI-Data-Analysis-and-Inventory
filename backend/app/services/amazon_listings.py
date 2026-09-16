"""Map Amazon seller SKUs to catalog products — through the priority chain only.

This is the first code in the repository that performs matching, so the
rules it lives under are restated here rather than referenced (CLAUDE.md
§5.1, §5.2):

* A listing resolves to a product through **exactly one rule at a time**,
  evaluated in a fixed order, stopping at the first rule that yields exactly
  one product. Every rule tried, and what it returned, is recorded.
* A rule that returns more than one product is **ambiguous**. The listing
  goes to the exception queue; it does not fall through to a weaker rule and
  no candidate is picked.
* **Item name is never an input.** It is stored in ``raw`` for display and
  nothing here reads it.
* **An approved mapping is permanent.** A listing whose ``mapping_status`` is
  already ``APPROVED`` is updated for ``asin``, ``listing_status`` and ``raw``
  and nothing else — not by this code, not by a later run.

The two rules that apply to a marketplace listing:

* **Priority 4 — AMAZON_SKU_MAPPING.** A ``product_identifiers`` row of type
  ``AMAZON_SKU`` with this seller SKU. That row is the catalog source
  (Nineyard) saying "this SKU is this item"; it is a previously approved
  mapping in the sense of §5.1, so the listing is approved automatically,
  with the organization's system user as approver.
* **Priority 1 — UPC.** The listing's ``product-id`` when its type is UPC or
  EAN, normalised by :mod:`app.matching.normalize`, against ``UPC``/``EAN``/
  ``GTIN`` identifiers. A single hit is a **suggestion**, not an approval:
  an Amazon-SKU link inferred from a UPC still needs a person (§5.1 row 5).
  The listing becomes ``PENDING`` with the candidate attached and a queue
  item with reason ``SUGGESTION_ONLY``.

Priority 4 is evaluated first because it is the direct evidence; priority 1
is evaluated as well, always, so the attribution is complete — and if the
two disagree, nothing is approved: the queue item's reason is
``CONFLICTING_IDENTIFIER`` and both candidates are shown. Never guess.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.transaction import transaction
from app.matching.engine import (
    MatchInput,
    RuleEvaluation,
    rule_amazon_sku_mapping,
    rule_upc,
)
from app.matching.normalize import normalize_gtin
from app.models.amazon import AmazonSyncRun
from app.models.catalog import MarketplaceListing, ProductIdentifier
from app.models.enums import (
    ActorType,
    AmazonSyncJobType,
    ExceptionReason,
    ExceptionStatus,
    IdentifierType,
    ListingStatus,
    MappingStatus,
    Marketplace,
    MatchMethod,
    SyncStatus,
    TriggerType,
)
from app.models.matching import ProductMappingException
from app.repositories.scoping import ScopedRepository, TenantScope
from app.services import audit
from app.services.amazon_runs import SyncAlreadyRunning
from app.services.system_user import ensure_system_user

if TYPE_CHECKING:
    from app.services.amazon_inventory import ParsedListing
    from app.services.amazon_orders import ReportFetcher

_logger = get_logger(__name__)

AUDIT_MAPPING_APPROVED: Final = "amazon.listing.mapping_approved"
AUDIT_EXCEPTION_OPENED: Final = "amazon.listing.exception_opened"
AUDIT_EXCEPTION_RESOLVED: Final = "amazon.listing.exception_resolved"
AUDIT_ACTOR_LABEL: Final = "amazon-listings-mapping"

#: ``product-id-type`` kinds that carry a GTIN usable at priority 1.
GTIN_KINDS: Final = frozenset({"UPC", "EAN"})

#: Identifier types that hold a GTIN in canonical form.
GTIN_IDENTIFIER_TYPES: Final = (IdentifierType.UPC, IdentifierType.EAN, IdentifierType.GTIN)

#: Amazon's listing status vocabulary → ours. Unknown values stay UNKNOWN.
LISTING_STATUS_MAP: Final[dict[str, ListingStatus]] = {
    "active": ListingStatus.ACTIVE,
    "inactive": ListingStatus.INACTIVE,
    "incomplete": ListingStatus.INACTIVE,
}


# --- the read side, kept behind a protocol so the resolver is unit-testable ------


class ListingCatalog(Protocol):
    """What the resolver reads. Distinct product ids, never rows."""

    def products_by_amazon_sku(self, seller_sku: str) -> list[uuid.UUID]: ...

    def products_by_gtin(self, canonical: str) -> list[uuid.UUID]: ...


class SqlListingCatalog(ScopedRepository):
    """The real catalog, tenant-scoped through ADR 0012's helper."""

    def products_by_amazon_sku(self, seller_sku: str) -> list[uuid.UUID]:
        statement = self.select(ProductIdentifier, ProductIdentifier.product_id).where(
            ProductIdentifier.identifier_type == IdentifierType.AMAZON_SKU,
            ProductIdentifier.normalized_value == normalize_seller_sku(seller_sku),
            ProductIdentifier.is_active.is_(True),
        )
        return sorted(set(self.session.execute(statement).scalars().all()))

    def products_by_gtin(self, canonical: str) -> list[uuid.UUID]:
        statement = self.select(ProductIdentifier, ProductIdentifier.product_id).where(
            ProductIdentifier.identifier_type.in_(GTIN_IDENTIFIER_TYPES),
            ProductIdentifier.normalized_value == canonical,
            ProductIdentifier.is_active.is_(True),
            # An identifier whose own check digit failed cannot drive priority 1.
            ProductIdentifier.has_valid_checksum.isnot(False),
        )
        return sorted(set(self.session.execute(statement).scalars().all()))


def normalize_seller_sku(seller_sku: str) -> str:
    """Amazon seller SKUs are case-sensitive; only surrounding whitespace goes."""
    return seller_sku.strip()


# --- the resolver: pure, deterministic, fully attributed --------------------------


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the chain decided for one listing, and why."""

    outcome: Literal["APPROVED", "PENDING", "UNMAPPED"]
    product_id: uuid.UUID | None
    method: MatchMethod | None
    exception_reason: ExceptionReason | None
    candidates: tuple[uuid.UUID, ...]
    evaluations: tuple[RuleEvaluation, ...]

    def evaluations_json(self, *, evaluated_at: datetime) -> dict[str, Any]:
        return {
            "source": "amazon_listings",
            "evaluated_at": evaluated_at.isoformat(),
            "rules": [e.as_json() for e in self.evaluations],
        }

    def candidates_json(self) -> dict[str, Any]:
        products = []
        for evaluation in self.evaluations:
            for candidate in evaluation.candidates:
                products.append(
                    {
                        "product_id": str(candidate),
                        "rule": evaluation.rule,
                        "priority": evaluation.priority,
                        "identifier": evaluation.input,
                    }
                )
        return {"products": products}


def resolve_listing(listing: ParsedListing, catalog: ListingCatalog) -> Resolution:
    """Run the chain for one listing. Reads the catalog; writes nothing.

    The rule implementations are the engine's (:mod:`app.matching.engine`);
    the policy above — priority 4 first, a UPC hit is a suggestion, a
    disagreement is a conflict — is this module's.
    """
    evaluations: list[RuleEvaluation] = []

    # Priority 4 — the catalog source already names this SKU.
    sku = normalize_seller_sku(listing.seller_sku)
    by_sku_rule = rule_amazon_sku_mapping(MatchInput(amazon_seller_sku=sku), catalog)
    evaluations.append(by_sku_rule)
    by_sku = list(by_sku_rule.candidates)
    if len(by_sku) > 1:
        # Ambiguous: stop here. No fall-through, no pick (AC-7.4).
        evaluations.append(_skipped_upc("an earlier rule was ambiguous"))
        return Resolution(
            "UNMAPPED",
            None,
            None,
            ExceptionReason.AMBIGUOUS_MATCH,
            tuple(by_sku),
            tuple(evaluations),
        )

    # Priority 1 — the listing's own UPC/EAN, always evaluated for attribution.
    by_gtin: list[uuid.UUID] = []
    if listing.product_id_kind in GTIN_KINDS:
        normalized = normalize_gtin(listing.product_id)
        if normalized.usable and normalized.canonical is not None:
            upc_rule = rule_upc(
                MatchInput(normalized_upc=normalized.canonical, upc_valid_checksum=True), catalog
            )
            by_gtin = list(upc_rule.candidates)
            evaluations.append(
                RuleEvaluation(
                    rule=upc_rule.rule,
                    priority=upc_rule.priority,
                    input=upc_rule.input,
                    outcome=upc_rule.outcome,
                    candidates=upc_rule.candidates,
                    note=f"{normalized.kind} {listing.product_id!r}",
                )
            )
        else:
            evaluations.append(
                _skipped_upc(
                    f"{listing.product_id!r} is not a usable GTIN: {normalized.problem}",
                    input_value=listing.product_id,
                )
            )
    else:
        evaluations.append(
            _skipped_upc(f"product-id-type is {listing.product_id_kind or 'absent'}, not a GTIN")
        )

    if len(by_sku) == 1:
        [product_id] = by_sku
        if len(by_gtin) == 1 and by_gtin[0] != product_id:
            # Two rules, two different answers. Nobody guesses; a person decides.
            return Resolution(
                "UNMAPPED",
                None,
                None,
                ExceptionReason.CONFLICTING_IDENTIFIER,
                (product_id, by_gtin[0]),
                tuple(evaluations),
            )
        return Resolution(
            "APPROVED",
            product_id,
            MatchMethod.AMAZON_SKU_MAPPING,
            None,
            (product_id,),
            tuple(evaluations),
        )

    if len(by_gtin) == 1:
        # A UPC hit on a marketplace listing is a suggestion, not an approval.
        return Resolution(
            "PENDING",
            by_gtin[0],
            MatchMethod.UPC,
            ExceptionReason.SUGGESTION_ONLY,
            tuple(by_gtin),
            tuple(evaluations),
        )
    if len(by_gtin) > 1:
        return Resolution(
            "UNMAPPED",
            None,
            None,
            ExceptionReason.AMBIGUOUS_MATCH,
            tuple(by_gtin),
            tuple(evaluations),
        )
    return Resolution("UNMAPPED", None, None, ExceptionReason.NO_MATCH, (), tuple(evaluations))


def _skipped_upc(note: str, *, input_value: str | None = None) -> RuleEvaluation:
    return RuleEvaluation(
        rule=MatchMethod.UPC.value, priority=1, input=input_value, outcome="skipped", note=note
    )


@dataclass(slots=True)
class MappingSummary:
    listings_seen: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    approved: int = 0
    suggested: int = 0
    unmapped: int = 0
    ambiguous: int = 0
    conflicting: int = 0
    already_approved: int = 0
    left_alone: int = 0
    exceptions_opened: int = 0
    exceptions_resolved: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "listings_seen": self.listings_seen,
            "listings_created": self.listings_created,
            "listings_updated": self.listings_updated,
            "approved": self.approved,
            "suggested": self.suggested,
            "unmapped": self.unmapped,
            "ambiguous": self.ambiguous,
            "conflicting": self.conflicting,
            "already_approved": self.already_approved,
            "left_alone": self.left_alone,
            "exceptions_opened": self.exceptions_opened,
            "exceptions_resolved": self.exceptions_resolved,
        }


def map_listings(
    session: Session,
    organization_id: uuid.UUID,
    listings: Sequence[ParsedListing],
    *,
    marketplace_id: str,
    catalog: ListingCatalog | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MappingSummary:
    """Upsert every listing and resolve the ones that may still be resolved.

    The caller owns the transaction. Nothing here commits, so the mapping
    lands with the ingestion run that produced it, or not at all.
    """
    catalog = catalog or SqlListingCatalog(session, organization_id)
    summary = MappingSummary()
    system_user_id: uuid.UUID | None = None
    evaluated_at = now()

    for parsed in listings:
        summary.listings_seen += 1
        listing, created = _upsert_listing(session, organization_id, marketplace_id, parsed)
        summary.listings_created += int(created)
        summary.listings_updated += int(not created)

        if listing.mapping_status is MappingStatus.APPROVED:
            # Permanent. Not re-evaluated, not touched (§5.2).
            summary.already_approved += 1
            continue
        if listing.mapping_status in (MappingStatus.REJECTED, MappingStatus.SUPERSEDED):
            # A person has already decided something here; re-running the chain
            # would re-queue a rejected suggestion unchanged (AC-8.5). Phase 8
            # owns what happens next.
            summary.left_alone += 1
            continue

        resolution = resolve_listing(parsed, catalog)
        summary.details.append(
            {
                "seller_sku": listing.seller_sku,
                "outcome": resolution.outcome,
                "reason": resolution.exception_reason.value
                if resolution.exception_reason
                else None,
            }
        )

        if resolution.outcome == "APPROVED":
            if system_user_id is None:
                system_user_id = ensure_system_user(session, organization_id).id
            _approve(session, listing, resolution, system_user_id, evaluated_at, summary)
        elif resolution.outcome == "PENDING":
            _suggest(session, listing, resolution, evaluated_at, summary)
        else:
            _leave_unmapped(session, listing, resolution, evaluated_at, summary)

    session.flush()
    _logger.info(
        "amazon.listings.mapped", organization_id=str(organization_id), **summary.as_json()
    )
    return summary


def _upsert_listing(
    session: Session, organization_id: uuid.UUID, marketplace_id: str, parsed: ParsedListing
) -> tuple[MarketplaceListing, bool]:
    """Create or refresh the listing row. Mapping columns are never touched here."""
    seller_sku = normalize_seller_sku(parsed.seller_sku)
    listing = session.execute(
        TenantScope(organization_id)
        .select(MarketplaceListing)
        .where(
            MarketplaceListing.marketplace == Marketplace.AMAZON,
            MarketplaceListing.marketplace_id == marketplace_id,
            MarketplaceListing.seller_sku == seller_sku,
        )
    ).scalar_one_or_none()
    created = listing is None
    if listing is None:
        listing = MarketplaceListing(
            organization_id=organization_id,
            marketplace=Marketplace.AMAZON,
            marketplace_id=marketplace_id,
            seller_sku=seller_sku,
            mapping_status=MappingStatus.UNMAPPED,
        )
        session.add(listing)

    listing.asin = parsed.asin
    listing.listing_status = LISTING_STATUS_MAP.get(
        (parsed.status or "").strip().lower(), ListingStatus.UNKNOWN
    )
    listing.raw = dict(parsed.raw)
    listing.is_active = True
    session.flush()
    return listing, created


def _approve(
    session: Session,
    listing: MarketplaceListing,
    resolution: Resolution,
    system_user_id: uuid.UUID,
    at: datetime,
    summary: MappingSummary,
) -> None:
    before = audit.snapshot(listing)
    listing.product_id = resolution.product_id
    listing.mapping_status = MappingStatus.APPROVED
    listing.mapping_method = resolution.method
    listing.approved_by_user_id = system_user_id
    listing.approved_at = at
    session.flush()
    summary.approved += 1
    audit.record_change(
        session,
        organization_id=listing.organization_id,
        action=AUDIT_MAPPING_APPROVED,
        instance=listing,
        before=before,
        actor_type=ActorType.SYSTEM,
        actor_label=AUDIT_ACTOR_LABEL,
        summary=(
            f"Listing {listing.seller_sku} approved to product {resolution.product_id} "
            f"by {resolution.method.value if resolution.method else 'unknown'}"
        ),
    )

    # An open queue item for this listing is now answered.
    pending = _pending_exception(session, listing)
    if pending is not None:
        before_exception = audit.snapshot(pending)
        pending.status = ExceptionStatus.APPROVED
        pending.resolved_product_id = resolution.product_id
        pending.resolved_by_user_id = system_user_id
        pending.resolved_at = at
        pending.resolution_note = "Resolved automatically: priority 4 (AMAZON_SKU_MAPPING) matched."
        session.flush()
        summary.exceptions_resolved += 1
        audit.record_change(
            session,
            organization_id=listing.organization_id,
            action=AUDIT_EXCEPTION_RESOLVED,
            instance=pending,
            before=before_exception,
            actor_type=ActorType.SYSTEM,
            actor_label=AUDIT_ACTOR_LABEL,
        )


def _suggest(
    session: Session,
    listing: MarketplaceListing,
    resolution: Resolution,
    at: datetime,
    summary: MappingSummary,
) -> None:
    listing.product_id = resolution.product_id
    listing.mapping_status = MappingStatus.PENDING
    listing.mapping_method = resolution.method
    listing.approved_by_user_id = None
    listing.approved_at = None
    session.flush()
    summary.suggested += 1
    _ensure_exception(session, listing, resolution, at, summary)


def _leave_unmapped(
    session: Session,
    listing: MarketplaceListing,
    resolution: Resolution,
    at: datetime,
    summary: MappingSummary,
) -> None:
    listing.product_id = None
    listing.mapping_status = MappingStatus.UNMAPPED
    listing.mapping_method = None
    listing.approved_by_user_id = None
    listing.approved_at = None
    session.flush()
    if resolution.exception_reason is ExceptionReason.AMBIGUOUS_MATCH:
        summary.ambiguous += 1
    elif resolution.exception_reason is ExceptionReason.CONFLICTING_IDENTIFIER:
        summary.conflicting += 1
    else:
        summary.unmapped += 1
    _ensure_exception(session, listing, resolution, at, summary)


def _ensure_exception(
    session: Session,
    listing: MarketplaceListing,
    resolution: Resolution,
    at: datetime,
    summary: MappingSummary,
) -> None:
    """One open queue item per listing: create it, or refresh the existing one."""
    assert resolution.exception_reason is not None
    pending = _pending_exception(session, listing)
    if pending is None:
        pending = ProductMappingException(
            organization_id=listing.organization_id,
            marketplace_listing_id=listing.id,
            reason=resolution.exception_reason,
            status=ExceptionStatus.PENDING,
            suggested_product_id=(
                resolution.product_id if resolution.outcome == "PENDING" else None
            ),
            candidates=resolution.candidates_json(),
            match_evaluations=resolution.evaluations_json(evaluated_at=at),
        )
        session.add(pending)
        session.flush()
        summary.exceptions_opened += 1
        audit.record(
            session,
            organization_id=listing.organization_id,
            action=AUDIT_EXCEPTION_OPENED,
            entity_type=ProductMappingException.__tablename__,
            entity_id=pending.id,
            actor_type=ActorType.SYSTEM,
            actor_label=AUDIT_ACTOR_LABEL,
            after=audit.snapshot(pending),
            summary=(
                f"Listing {listing.seller_sku}: {resolution.exception_reason.value}"
                f" ({len(resolution.candidates)} candidate(s))"
            ),
        )
        return

    # Refresh in place: the reviewer sees the latest evaluation, not a pile.
    pending.reason = resolution.exception_reason
    pending.suggested_product_id = (
        resolution.product_id if resolution.outcome == "PENDING" else None
    )
    pending.candidates = resolution.candidates_json()
    pending.match_evaluations = resolution.evaluations_json(evaluated_at=at)
    session.flush()


def _pending_exception(
    session: Session, listing: MarketplaceListing
) -> ProductMappingException | None:
    return session.execute(
        TenantScope(listing.organization_id)
        .select(ProductMappingException)
        .where(
            ProductMappingException.marketplace_listing_id == listing.id,
            ProductMappingException.status == ExceptionStatus.PENDING,
        )
    ).scalar_one_or_none()


# --- a standalone run: fetch the listings report and map it -------------------------------


AUDIT_ACTION_COMPLETED: Final = "amazon.listings_sync.completed"
AUDIT_ACTION_FAILED: Final = "amazon.listings_sync.failed"
AUDIT_ACTOR_LABEL_SYNC: Final = "amazon-listings-sync"


class ListingsSyncAlreadyRunning(SyncAlreadyRunning):
    """Another LISTINGS_REPORT run is still RUNNING for this organization."""


def run_listings_sync(
    session: Session,
    organization_id: uuid.UUID,
    trigger_type: TriggerType,
    triggered_by_user_id: uuid.UUID | None = None,
    client: ReportFetcher | None = None,
    *,
    marketplace_id: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AmazonSyncRun:
    """Fetch the merchant listings report and map it, as its own run.

    The inventory sync maps listings too, because it already has the report
    in hand. This run exists for the daily re-evaluation that picks up
    catalog changes (new ``AMAZON_SKU`` identifiers, say) without waiting
    for an inventory pull, and for the CLI. Same run/transaction/audit shape
    as the other two jobs.
    """
    from app.core.config import get_settings
    from app.integrations.amazon import AmazonClient, AmazonConfig
    from app.services.amazon_inventory import LISTINGS_REPORT_TYPE, parse_listings_report
    from app.services.amazon_runs import close_run, fail_run, open_run

    started_at = now()
    settings = get_settings()
    if client is None:
        client = AmazonClient(AmazonConfig.from_settings(settings))
    marketplace_id = marketplace_id or settings.amazon_marketplace_id

    try:
        run = open_run(
            session,
            organization_id=organization_id,
            job_type=AmazonSyncJobType.LISTINGS_REPORT,
            trigger_type=trigger_type,
            marketplace_id=marketplace_id,
            started_at=started_at,
            triggered_by_user_id=triggered_by_user_id,
        )
    except SyncAlreadyRunning as exc:
        raise ListingsSyncAlreadyRunning(str(exc)) from exc

    failure: BaseException | None = None
    try:
        content = client.fetch_report(LISTINGS_REPORT_TYPE, started_at, started_at)
        parsed = parse_listings_report(content)
        with transaction(session):
            summary = map_listings(
                session,
                organization_id,
                parsed.listings,
                marketplace_id=marketplace_id,
                now=lambda: started_at,
            )
            close_run(
                session,
                run,
                status=(
                    SyncStatus.COMPLETED_WITH_ERRORS if parsed.rows_failed else SyncStatus.COMPLETED
                ),
                audit_action=AUDIT_ACTION_COMPLETED,
                actor_label=AUDIT_ACTOR_LABEL_SYNC,
                completed_at=now(),
                rows_seen=parsed.rows_seen,
                rows_created=summary.listings_created,
                rows_updated=summary.listings_updated,
                rows_unchanged=0,
                rows_failed=parsed.rows_failed,
                error_details={
                    **({"rows": parsed.errors[:100]} if parsed.errors else {}),
                    "listings_mapping": summary.as_json(),
                },
            )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if run.status is SyncStatus.RUNNING:
            fail_run(
                session,
                run,
                failure,
                completed_at=now(),
                audit_action=AUDIT_ACTION_FAILED,
                actor_label=AUDIT_ACTOR_LABEL_SYNC,
            )
    return run
