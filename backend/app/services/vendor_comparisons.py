"""Explicit selected-policy observations with exact, qualified goods-price ordering."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from sqlalchemy.orm import Session

from app.core.security import Principal
from app.db.transaction import transaction
from app.models import Product
from app.repositories.purchasing_policies import PurchasingPolicyRepository
from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import (
    CommercialRecommendationSnapshot,
    RecommendationPreview,
    RecommendationPreviewRequest,
)
from app.schemas.replenishment_review import (
    BasePrice,
    ComparedOffer,
    VendorComparisonRequest,
    VendorComparisonResponse,
)
from app.services import purchasing_policies as policies
from app.services.recommendations import preview_recommendation


def normalized_price(snapshot: object) -> BasePrice | None:
    if not isinstance(snapshot, CommercialRecommendationSnapshot):
        return None
    units = snapshot.approved_policy.definition.amazon.listing_units
    if not units or any(unit.base_units_per_listing_unit is None for unit in units):
        return None
    price = snapshot.commercial.price
    factor = price.base_units_per_price_unit
    tax = price.evidence.get("price_semantics", {}).get("includes_tax")
    if (
        snapshot.policy_blocking_reasons
        or snapshot.supplier.status != "VERIFIED"
        or price.status != "VERIFIED"
        or price.currency != "USD"
        or price.reported_price is None
        or not price.reported_price.is_finite()
        or price.reported_price < 0
        or type(factor) is not int
        or factor <= 0
        or type(tax) is not bool
    ):
        return None
    exact = Fraction(price.reported_price) / factor
    with localcontext(Context(prec=28)):
        display = Decimal(exact.numerator) / Decimal(exact.denominator)
    return BasePrice(
        reported_price=price.reported_price,
        base_units_per_price_unit=factor,
        includes_tax=tax,
        exact_numerator=str(exact.numerator),
        exact_denominator=str(exact.denominator),
        display_per_base_unit=display,
        display_is_rounded=Fraction(display) != exact,
    )


def compare_observations(
    product: Product,
    observations: tuple[tuple[PolicyIdentity, RecommendationPreview], ...],
    requested_order: str,
    at: datetime,
) -> VendorComparisonResponse:
    offers: list[ComparedOffer] = []
    unit_projections: set[tuple[tuple[str, int | None], ...]] = set()
    taxes: set[bool] = set()
    issues: set[str] = set()
    for identity, preview in observations:
        snapshot = preview.snapshot
        price = normalized_price(snapshot)
        if price is not None:
            unit_projections.add(
                tuple(
                    sorted(
                        (str(unit.listing_id), unit.base_units_per_listing_unit)
                        for unit in snapshot.approved_policy.definition.amazon.listing_units
                    )
                )
            )
            taxes.add(price.includes_tax)
        offers.append(
            ComparedOffer(
                identity=identity,
                policy_id=snapshot.policy_id,
                approved_revision_id=snapshot.approved_policy.id,
                base_price=price,
                price_comparable=False,
                comparison_reasons=() if price else ("VERIFIED_CURRENT_BASE_PRICE_UNAVAILABLE",),
                evidence_sha256=preview.evidence_sha256,
                snapshot=snapshot,
            )
        )
    if len(unit_projections) > 1:
        issues.add("APPROVED_BASE_UNIT_POLICIES_DIFFER")
    if len(taxes) > 1:
        issues.add("REPORTED_PRICE_TAX_BASIS_DIFFERS")
    valid = sum(offer.base_price is not None for offer in offers)
    if valid < len(offers):
        issues.add("SOME_SELECTED_OFFERS_HAVE_NO_VERIFIED_CURRENT_PRICE")
    compatible = len(unit_projections) == 1 and len(taxes) == 1 and valid >= 2
    if valid < 2:
        issues.add("TWO_VERIFIED_CURRENT_PRICES_REQUIRED")
    for index, offer in enumerate(offers):
        offers[index] = offer.model_copy(
            update={
                "price_comparable": compatible and offer.base_price is not None,
                "comparison_reasons": tuple(
                    sorted(set(offer.comparison_reasons) | (issues if not compatible else set()))
                ),
            }
        )

    def label(offer: ComparedOffer) -> tuple[str, ...]:
        i = offer.identity
        return (
            i.vendor_name.casefold(),
            i.vendor_code.casefold(),
            i.vendor_sku.casefold(),
            str(offer.policy_id),
        )

    applied = "BASE_PRICE" if requested_order == "BASE_PRICE" and compatible else "VENDOR_NAME"
    if applied == "BASE_PRICE":
        offers.sort(
            key=lambda offer: (
                offer.base_price is None,
                Fraction(
                    int(offer.base_price.exact_numerator), int(offer.base_price.exact_denominator)
                )
                if offer.base_price
                else Fraction(0),
                label(offer),
            )
        )
    else:
        offers.sort(key=label)
    return VendorComparisonResponse(
        product_id=product.id,
        catalog_item_number=product.catalog_item_number,
        product_name=product.name,
        evaluated_at=at,
        selected_count=len(offers),
        comparable_price_count=valid if compatible else 0,
        price_comparison_status="UNAVAILABLE"
        if not compatible
        else "AVAILABLE"
        if valid == len(offers)
        else "PARTIAL",
        requested_order="BASE_PRICE" if requested_order == "BASE_PRICE" else "VENDOR_NAME",
        applied_order=applied,
        issues=tuple(sorted(issues)),
        offers=tuple(offers),
    )


def preview_vendor_comparison(
    session: Session,
    actor: Principal,
    payload: VendorComparisonRequest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> VendorComparisonResponse:
    actor.require_roles(policies.READ)
    repository = PurchasingPolicyRepository(session, actor.organization_id)
    with transaction(session):
        policies.validate_actor(repository, actor)
        product = repository.owned(Product, payload.product_id)
        if product is None:
            raise policies.PolicyNotFound()
        roots = []
        for choice in payload.choices:
            root = repository.root(choice.policy_id)
            if root is None or root.product_id != product.id:
                raise policies.PolicyNotFound()
            roots.append((root, choice))
        # One deterministic stream order prevents inverse user selections from
        # acquiring the same policy locks in opposite orders.
        roots.sort(
            key=lambda pair: (
                str(pair[0].product_id),
                str(pair[0].vendor_product_id),
                pair[0].marketplace_id,
                str(pair[0].id),
            )
        )
        for root, _ in roots:
            repository.lock(root.product_id, root.vendor_product_id, root.marketplace_id)
        at = now()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Comparison time must be timezone-aware")
        observations = []
        for root, choice in roots:
            preview = preview_recommendation(
                session,
                actor,
                root.id,
                RecommendationPreviewRequest(
                    expected_approved_revision_id=choice.expected_approved_revision_id,
                    financial=payload.financial,
                ),
                now=lambda: at,
            )
            identity = repository.identity(root.product_id, root.vendor_product_id)
            if identity is None:
                raise policies.PolicyNotFound()
            observations.append((PolicyIdentity.model_validate(identity), preview))
        return compare_observations(product, tuple(observations), payload.order_by, at)
