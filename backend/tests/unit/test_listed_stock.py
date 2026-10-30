"""Supplier-confirmed availability is independent of an exact stock count."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.imports.extract import IssueCode, extract_row, plan_columns
from app.imports.profile_rules import ProfileRules
from app.imports.readers import ReadOptions, UnreadableFile, open_rows
from app.models.enums import AvailabilityStatus, FileFormat, ImportRowStatus


def listed_rules(*, quantity: bool = False, derived_sku: bool = False) -> ProfileRules:
    columns: list[dict[str, str]] = [
        {"target": "upc", "source": "UPC"},
        {"target": "unit_cost", "source": "Price"},
    ]
    if quantity:
        columns.append({"target": "quantity_available", "source": "Qty"})
    return ProfileRules.model_validate(
        {
            "column_map": {"columns": columns},
            "availability_rules": {"available_when": "listed"},
            "normalization_rules": {"vendor_sku_from_upc": derived_sku},
        }
    )


def test_missing_quantity_stays_null_while_price_is_per_piece() -> None:
    rules = listed_rules()
    plan = plan_columns(rules, ["UPC", "Price"])
    row = extract_row(rules, plan, 2, ["012345678905", "12.50"])
    assert plan.header_ok
    assert row.quantity is None
    assert row.availability_status is AvailabilityStatus.AVAILABLE
    assert row.unit_cost == Decimal("12.50")
    assert row.status is ImportRowStatus.OK
    assert row.issues[0].code is IssueCode.QUANTITY_NOT_PROVIDED


@pytest.mark.parametrize(("source", "quantity"), [("", None), ("0", 0), ("12", 12), ("240+", None)])
def test_listed_mode_preserves_counts_and_does_not_invent_exact_values(
    source: str, quantity: int | None
) -> None:
    rules = listed_rules(quantity=True)
    row = extract_row(
        rules, plan_columns(rules, ["UPC", "Price", "Qty"]), 2, ["012345678905", "12.50", source]
    )
    assert row.quantity == quantity
    assert row.availability_status is AvailabilityStatus.AVAILABLE
    assert row.raw["quantity_available"] == source
    if source.endswith("+"):
        assert row.issues[0].code is IssueCode.QUANTITY_NOT_EXACT
        assert row.status is ImportRowStatus.WARNING


@pytest.mark.parametrize("source", ["-1", "1.5", "garbage", "NaN", "-20+", "2++"])
def test_listed_mode_still_rejects_bad_or_negative_quantities(source: str) -> None:
    rules = listed_rules(quantity=True)
    row = extract_row(
        rules, plan_columns(rules, ["UPC", "Price", "Qty"]), 2, ["012345678905", "12.50", source]
    )
    assert row.status is ImportRowStatus.ERROR
    assert row.quantity is None


def test_default_quantity_mode_still_requires_a_mapping_and_exact_count() -> None:
    with pytest.raises(ValidationError, match="quantity_available must be mapped"):
        ProfileRules.model_validate({"column_map": {"columns": [{"target": "upc", "source": "U"}]}})
    rules = listed_rules(quantity=True)
    default = rules.model_copy(
        update={
            "availability_rules": rules.availability_rules.model_copy(
                update={"available_when": "quantity_gt_zero"}
            )
        }
    )
    for source in ["", "240+"]:
        row = extract_row(
            default,
            plan_columns(default, ["UPC", "Price", "Qty"]),
            2,
            ["012345678905", "12.50", source],
        )
        assert row.status is ImportRowStatus.ERROR
        assert row.availability_status is AvailabilityStatus.UNKNOWN


def test_quantity_mapping_can_be_optional_only_when_listed() -> None:
    rules = listed_rules(quantity=True)
    assert plan_columns(rules, ["UPC", "Price"]).header_ok
    rules.column_map.columns[-1].required = True
    assert not plan_columns(rules, ["UPC", "Price"]).header_ok


def test_upc_line_keys_are_opt_in_validated_and_marked_as_derived() -> None:
    for enabled in [False, True]:
        rules = listed_rules(derived_sku=enabled)
        row = extract_row(
            rules, plan_columns(rules, ["UPC", "Price"]), 2, ["012345678905", "12.50"]
        )
        assert row.vendor_sku == ("UPC:00012345678905" if enabled else None)
        assert row.raw_upc == "012345678905"
        assert row.normalized_json().get("vendor_sku_from_upc", False) is enabled
    rules = listed_rules(derived_sku=True)
    for upc in ["012345678906", "bad", "012345678905 / 036000291452"]:
        row = extract_row(rules, plan_columns(rules, ["UPC", "Price"]), 2, [upc, "12.50"])
        assert row.vendor_sku is None


def test_legacy_excel_keeps_sheet_row_numbers_and_text_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xlrd

    rows: list[list[str | float]] = [["Report"], ["UPC", "Qty"], ["0012345678905", 7.0], ["", ""]]
    sheet = SimpleNamespace(
        name="Stock",
        nrows=len(rows),
        row=lambda i: [
            SimpleNamespace(
                value=v, ctype=xlrd.XL_CELL_TEXT if isinstance(v, str) else xlrd.XL_CELL_NUMBER
            )
            for v in rows[i]
        ],
    )
    release = Mock()
    workbook = SimpleNamespace(
        sheet_names=lambda: ["Stock"],
        sheet_by_name=lambda name: sheet,
        sheet_by_index=lambda index: sheet,
        release_resources=release,
    )
    monkeypatch.setattr(xlrd, "open_workbook", lambda **kwargs: workbook)
    content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fixture"
    options = ReadOptions(file_format=FileFormat.XLSX, sheet_name="Stock", header_row_index=1)
    stream = open_rows(content, options)
    assert stream.sheet == "Stock"
    assert [(r.row_number, r.cells) for r in stream.rows] == [
        (3, ["0012345678905", "7"]),
        (4, ["", ""]),
    ]
    release.assert_called_once()
    assert content.endswith(b"fixture")
    with pytest.raises(UnreadableFile, match="not found"):
        open_rows(content, ReadOptions(file_format=FileFormat.XLSX, sheet_name="Missing"))
    with pytest.raises(UnreadableFile, match="out of range"):
        open_rows(content, ReadOptions(file_format=FileFormat.XLSX, sheet_index=1))


def test_corrupt_legacy_excel_is_an_explicit_file_failure() -> None:
    with pytest.raises(UnreadableFile, match="not a readable XLS workbook"):
        open_rows(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1broken", ReadOptions(file_format=FileFormat.XLSX)
        )
