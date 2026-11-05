"""Supplier offer identities remain separate without guessing catalog products."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.imports.extract import IssueCode, extract_row, normalize_vendor_sku, plan_columns
from app.imports.mapping import map_table
from app.imports.profile_rules import ProfileRules
from app.models.enums import AvailabilityStatus, ImportRowStatus

HEADERS = ["UPC", "Description", "Qty", "Price", "Condition", "SKU"]


def offer_rules(**normalization: Any) -> ProfileRules:
    return ProfileRules.model_validate(
        {
            "column_map": {
                "columns": [
                    {"target": "upc", "source": 0},
                    {"target": "description", "source": 1},
                    {"target": "quantity_available", "source": 2},
                    {"target": "unit_cost", "source": 3},
                    {"target": "offer_variant", "source": 4},
                    {"target": "vendor_sku", "source": 5},
                ]
            },
            "normalization_rules": {"vendor_sku_from_offer": True, **normalization},
            "availability_rules": {"available_when": "listed"},
        }
    )


def test_offer_key_ignores_cost_quantity_and_source_row_position() -> None:
    rules = offer_rules()
    plan = plan_columns(rules, HEADERS)
    first = extract_row(rules, plan, 2, ["012345678905", "Widget", "12", "3", "CLEAN", ""])
    later = extract_row(rules, plan, 99, ["12345678905", " widget ", "500+", "4", "clean", ""])
    assert first.vendor_sku == later.vendor_sku
    assert first.vendor_sku and first.vendor_sku.startswith("OFFER:")
    assert first.normalized_vendor_sku == normalize_vendor_sku(first.vendor_sku)
    assert later.quantity is None and later.unit_cost == Decimal("4")
    assert later.availability_status is AvailabilityStatus.AVAILABLE


def test_offer_variant_and_description_separate_reused_barcode_offers() -> None:
    rules = offer_rules(review_shared_upc_offers=True)
    preview = map_table(
        rules,
        HEADERS,
        [
            ["012345678905", "Widget", "12", "3", "CLEAN", ""],
            ["012345678905", "Widget", "5", "2", "DAMAGED BOX", ""],
            ["012345678905", "Widget two pack", "2", "5", "CLEAN", ""],
        ],
    )
    assert len({r.vendor_sku for r in preview.rows}) == 3
    for row in preview.rows:
        assert row.upc_shared_by_offers and row.has_valid_checksum
        assert row.normalized_upc == "00012345678905"
        assert IssueCode.UPC_SHARED_BY_OFFERS in {i.code for i in row.issues}


def test_identical_offer_is_not_flagged_as_shared_barcode() -> None:
    preview = map_table(
        offer_rules(review_shared_upc_offers=True),
        HEADERS,
        [
            ["012345678905", "Widget", "12", "3", "CLEAN", ""],
            ["012345678905", "Widget", "8", "4", "CLEAN", ""],
        ],
    )
    assert preview.rows[0].vendor_sku == preview.rows[1].vendor_sku
    assert not any(r.upc_shared_by_offers for r in preview.rows)


@pytest.mark.parametrize("barcode", ["", "BAD-CODE", "012345678906"])
def test_missing_or_invalid_upc_keeps_an_offer_for_manual_mapping(barcode: str) -> None:
    rules = offer_rules()
    row = extract_row(rules, plan_columns(rules, HEADERS), 2, [barcode, "Pouch", "1", "2", "", ""])
    assert row.vendor_sku_from_offer and row.vendor_sku
    assert row.status is not ImportRowStatus.ERROR
    assert not row.has_valid_checksum
    if barcode:
        assert IssueCode.UPC_INVALID in {i.code for i in row.issues}


def test_existing_supplier_sku_is_authoritative() -> None:
    rules = offer_rules()
    row = extract_row(rules, plan_columns(rules, HEADERS), 2, ["", "Widget", "1", "2", "", "SUP-1"])
    assert row.vendor_sku == "SUP-1" and not row.vendor_sku_from_offer


def test_declared_brand_prefix_is_preserved_in_description() -> None:
    rules = offer_rules(description_prefix_columns=["Brand"])
    headers = [*HEADERS, "Brand"]
    plan = plan_columns(rules, headers)
    row = extract_row(rules, plan, 2, ["012345678905", "ISHQ", "", "25", "", "", "Arabian Crest"])
    assert row.description == "Arabian Crest ISHQ"
    assert row.raw["description"] == "ISHQ"
    assert not plan_columns(rules, HEADERS).header_ok


def test_section_heading_is_skipped_with_original_values() -> None:
    rules = offer_rules(skip_non_product_rows=True)
    row = extract_row(rules, plan_columns(rules, HEADERS), 88, ["TESTERS", "", "", "", "", ""])
    assert row.status is ImportRowStatus.SKIPPED and row.row_number == 88
    assert row.raw["upc"] == "TESTERS"
    assert row.issues[0].code is IssueCode.NON_PRODUCT_ROW
    assert row.vendor_sku is None and row.availability_status is None


@pytest.mark.parametrize(
    "cells",
    [
        ["", "Widget", "", "", "", ""],
        ["", "", "1", "", "", ""],
        ["", "", "", "2", "", ""],
    ],
)
def test_incomplete_product_is_not_misclassified_as_section(cells: list[str]) -> None:
    rules = offer_rules(skip_non_product_rows=True)
    assert not extract_row(rules, plan_columns(rules, HEADERS), 2, cells).is_skipped


def test_legacy_defaults_do_not_derive_or_skip() -> None:
    rules = offer_rules(vendor_sku_from_offer=False)
    row = extract_row(rules, plan_columns(rules, HEADERS), 2, ["", "Widget", "1", "2", "", ""])
    assert row.vendor_sku is None and row.status is ImportRowStatus.ERROR
    assert not row.is_skipped


@pytest.mark.parametrize(
    "options",
    [
        {"vendor_sku_from_upc": True},
        {"vendor_sku_from_offer": False, "review_shared_upc_offers": True},
    ],
)
def test_conflicting_identity_rules_are_rejected(options: dict[str, bool]) -> None:
    with pytest.raises(ValidationError):
        offer_rules(**options)
