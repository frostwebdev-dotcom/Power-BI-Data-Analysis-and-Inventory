"""Synthetic historical amounts, exact identity and missing-evidence semantics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.catalog import MarketplaceListing
from app.models.enums import MatchMethod
from app.models.financial import FinancialImportRow
from app.schemas.amazon_readiness import ReadinessListing
from app.schemas.financial_context import FinancialSelection
from app.services.connectbooks import preview_connectbooks_xlsx
from app.services.recommendation_financials import historical_row, mapping_evidence
from tests.unit.test_connectbooks import changed, profile, workbook


def example(**changes: Any) -> tuple[ReadinessListing, MarketplaceListing, FinancialImportRow]:
    listing = MarketplaceListing(
        id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        seller_sku="0000123",
        asin="B000000001",
        approved_at=datetime(2026, 11, 17, tzinfo=UTC),
        approved_by_user_id=uuid.uuid4(),
        mapping_method=MatchMethod.MANUAL_APPROVAL,
    )
    source = preview_connectbooks_xlsx(workbook([changed(**changes)]), profile()).rows[0]
    row = FinancialImportRow(
        id=uuid.uuid4(),
        source_row_number=2,
        seller_sku=listing.seller_sku,
        row_kind="SKU",
        mapping_status="LINKED",
        match_priority=4,
        listing_id=listing.id,
        product_id=listing.product_id,
        mapping_snapshot=mapping_evidence(listing),
        source_snapshot=source.model_dump(mode="json"),
        review_reasons=[],
        net_sales=source.amounts["net_sales"],
        fees=source.amounts["fees"],
        cost=source.amounts["cost"],
        ad_spend=source.amounts["ad_spend"],
        reported_profit=source.amounts["profit"],
        computed_profit=source.computed_profit,
    )
    known = ReadinessListing(
        listing_id=listing.id,
        seller_sku=listing.seller_sku,
        asin=listing.asin,
        mapping_status="APPROVED",
        mapping_method="MANUAL_APPROVAL",
        approved_at=listing.approved_at,
        approved_by_user_id=listing.approved_by_user_id,
        base_units_per_listing_unit=1,
        snapshot_id=None,
        captured_at=None,
        fba_sold_listing_units=None,
        fbm_sold_listing_units=None,
    )
    return known, listing, row


def test_exact_signed_source_money_is_historical_and_serializes_as_strings() -> None:
    known, current, source = example()
    assert current.product_id is not None
    row = historical_row(known, current.product_id, [source], None, False, current)
    assert row.included_in_totals and row.amounts["fees"] == Decimal(-15)
    assert row.computed_profit == Decimal(50)
    assert row.model_dump(mode="json")["amounts"]["cost"] == "40"
    assert row.mapping_snapshot == row.current_mapping_snapshot


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "unresolved",
        "product",
        "asin",
        "approval",
        "unapproved",
        "binding_missing",
        "source_money",
        "source_invalid",
    ],
)
def test_unknown_or_changed_identity_never_becomes_zero_or_totals(fault: str) -> None:
    known, current, source = example()
    rows = [source]
    if fault == "missing":
        rows = []
    if fault == "duplicate":
        rows *= 2
    if fault == "unresolved":
        source.mapping_status = "UNRESOLVED"
    if fault == "product":
        current.product_id = uuid.uuid4()
    if fault == "asin":
        current.asin = "B000000002"
    if fault == "approval":
        current.approved_at = datetime.now(UTC)
    if fault == "unapproved":
        current.approved_by_user_id = None
    if fault == "source_money":
        source.cost = Decimal(999)
    if fault == "source_invalid":
        source.source_snapshot = {}
    assert current.product_id is not None
    row = historical_row(known, current.product_id, rows, None, fault == "binding_missing", current)
    assert not row.included_in_totals and row.amounts == {} and row.computed_profit is None
    assert row.review_reasons


def test_zero_cost_warning_and_unknown_roi_are_preserved() -> None:
    known, current, source = example(Cost=0, Profit=90, ROI=0)
    assert current.product_id is not None
    row = historical_row(known, current.product_id, [source], None, False, current)
    assert row.amounts["cost"] == 0 and row.computed_roi_percent is None
    assert "ZERO_COST_WITH_POSITIVE_SALES" in row.review_reasons


def test_financial_binding_choice_must_be_explicit() -> None:
    with pytest.raises(ValidationError):
        FinancialSelection.model_validate({"import_id": uuid.uuid4()})
    assert (
        FinancialSelection(
            import_id=uuid.uuid4(), expected_binding_revision_id=None
        ).expected_binding_revision_id
        is None
    )
