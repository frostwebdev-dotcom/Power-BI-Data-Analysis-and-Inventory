"""Synthetic signed-source fixtures; never commit customer financial workbooks."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from app.cli.connectbooks_preview import main
from app.schemas.connectbooks import ConnectBooksXlsxProfile
from app.services import connectbooks
from app.services.connectbooks import HEADERS, N, preview_connectbooks_xlsx
from app.services.profitability import ProfitabilityPreviewError

BASE: dict[str, object] = {
    "Item": "0000123",
    "FNSKU": "",
    "Item ID": "B000000001",
    "Title": "Synthetic item",
    "Store": "Synthetic Store",
    "Sales Qty": 10,
    "Refund Qty": -2,
    "Total Units": 8,
    "Sales": 100,
    "Refunds": -20,
    "Adjustments": 30,
    "Net Sales": 110,
    "Fees": -15,
    "Ad Spend": 5,
    "Cost": 40,
    "Profit": 50,
    "ROI": Decimal("1.25"),
    "Profit Margin %": Decimal("0.45"),
    "Shipping Desk": -10,
    "Marketplace Commission": -5,
}


@dataclass
class Cell:
    value: str
    kind: str
    formula: str | None = None


def profile(**changes: object) -> ConnectBooksXlsxProfile:
    values: dict[str, object] = {
        "sheet_name": "Main sheet",
        "store_name": "Synthetic Store-Amazon",
        "source_store_name": "Synthetic Store",
        "marketplace_id": "ATVPDKIKX0DER",
        "currency": "USD",
        "reporting_month": 10,
        "reporting_year": 2026,
        "reporting_timezone": "America/New_York",
        "advertising_coverage_confirmed": True,
    }
    values.update(changes)
    return ConnectBooksXlsxProfile.model_validate(values)


def changed(**changes: object) -> dict[str, object]:
    return {**BASE, **changes}


def workbook(
    rows: list[dict[str, object]] | None = None,
    *,
    headers: list[str] | None = None,
    totals: dict[str, object] | None = None,
    footer: bool = True,
) -> bytes:
    """Small in-memory OOXML package for parser tests, not a delivered spreadsheet."""
    rows = rows if rows is not None else [BASE]
    names = headers if headers is not None else list(BASE)
    sheet = ET.Element(f"{N}worksheet")
    body = ET.SubElement(sheet, f"{N}sheetData")

    def append(number: int, values: list[object]) -> None:
        row = ET.SubElement(body, f"{N}row", r=str(number))
        for index, raw in enumerate(values, 1):
            if raw is None:
                continue
            col, letters = index, ""
            while col:
                col, rem = divmod(col - 1, 26)
                letters = chr(65 + rem) + letters
            kind = (
                raw.kind
                if isinstance(raw, Cell)
                else (
                    "inlineStr" if isinstance(raw, str) else "b" if isinstance(raw, bool) else "n"
                )
            )
            cell = ET.SubElement(row, f"{N}c", r=f"{letters}{number}", t=kind)
            if isinstance(raw, Cell) and raw.formula:
                ET.SubElement(cell, f"{N}f").text = raw.formula
            value = raw.value if isinstance(raw, Cell) else str(raw)
            if kind == "inlineStr":
                inline = ET.SubElement(cell, f"{N}is")
                ET.SubElement(inline, f"{N}t").text = value
            else:
                ET.SubElement(cell, f"{N}v").text = value

    append(1, list(names))
    for number, row in enumerate(rows, 2):
        append(number, [row.get(h) for h in names])
    if footer:
        summary: dict[str, object] = {}
        for header in HEADERS.values():
            numeric = [r.get(header) for r in rows]
            total = sum(
                (
                    Decimal(str(v))
                    for v in numeric
                    if isinstance(v, int | Decimal) and not isinstance(v, bool)
                ),
                Decimal(0),
            )
            summary[header] = (
                str(total)
                if header in {"Sales Qty", "Refund Qty", "Total Units"}
                else f"{'-' if total < 0 else ''}${abs(total):,.8f}"
            )
        summary.update(totals or {})
        append(len(rows) + 2, [summary.get(h) for h in names])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{N[1:-1]}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Main sheet" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
    return buffer.getvalue()


def codes(row: object) -> set[str]:
    assert hasattr(row, "issues")
    return {issue.code for issue in row.issues}


def test_signed_totals_preserve_text_sku_and_do_not_double_deduct_shipping() -> None:
    raw = workbook()
    report = preview_connectbooks_xlsx(raw, profile())
    row = report.rows[0]
    assert row.seller_sku == "0000123" and row.row_number == 2
    assert report.status == "RECONCILED_PREVIEW" and row.status == "RECONCILED"
    assert row.computed_profit == 50 and row.computed_roi_percent == 125
    assert row.computed_margin_percent is not None
    assert abs(row.computed_margin_percent - Decimal(5000) / 110) < Decimal("1e-25")
    assert row.reported_roi_percent == 125 and row.reported_margin_percent == 45
    assert row.amounts["fees"] == -15 and row.amounts["refund_qty"] == -2
    assert all(c.status == "MATCH" for c in report.controls)
    assert report.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert report.sku_rows == 1 and report.unallocated_rows == 0
    assert not row.purchasing_eligible and row.identity_status == "UNRESOLVED"
    assert report.preview_only and not report.persistence_performed
    assert not report.identity_resolution_performed and not report.purchasing_eligible


def test_reordered_headers_and_repeated_unused_descriptions_are_supported() -> None:
    headers = [
        *reversed(BASE),
        "Category",
        "Category",
        "Fulfillment Channel",
        "Fulfillment Channel",
    ]
    report = preview_connectbooks_xlsx(workbook(headers=headers), profile())
    assert report.rows[0].computed_profit == 50
    assert report.source_columns["Item"] != "A"


def test_refund_only_row_preserves_negative_cost_and_fee_credit() -> None:
    row = changed(
        **{
            "Sales Qty": 0,
            "Refund Qty": -1,
            "Total Units": -1,
            "Sales": 0,
            "Refunds": -20,
            "Adjustments": 0,
            "Net Sales": -20,
            "Fees": 3,
            "Cost": -10,
            "Ad Spend": 0,
            "Profit": -7,
        }
    )
    result = preview_connectbooks_xlsx(workbook([row]), profile()).rows[0]
    assert result.status == "RECONCILED" and result.computed_profit == -7
    assert result.computed_margin_percent is None and result.computed_roi_percent is None


def test_unitemized_is_in_totals_but_never_treated_as_a_sku() -> None:
    report = preview_connectbooks_xlsx(
        workbook([BASE, changed(Item="Unitemized", **{"Item ID": ""})]), profile()
    )
    row = report.rows[1]
    assert report.sku_rows == 1 and report.unallocated_rows == 1
    assert row.seller_sku is None and row.identity_status == "UNALLOCATED"
    assert row.computed_profit == 50 and row.status == "REVIEW_REQUIRED"
    assert all(c.status == "MATCH" for c in report.controls)
    assert "UNALLOCATED_FINANCIAL_ROW" in codes(row)


def test_zero_cost_with_sales_is_reviewed_and_roi_remains_undefined() -> None:
    row = changed(Cost=0, Profit=90)
    report = preview_connectbooks_xlsx(workbook([row]), profile())
    result = report.rows[0]
    assert result.status == "REVIEW_REQUIRED" and result.computed_profit == 90
    assert result.amounts["cost"] == 0 and result.computed_roi_percent is None
    assert "ZERO_COST_WITH_POSITIVE_SALES" in codes(result)


def test_missing_cost_never_becomes_zero_or_a_complete_total() -> None:
    report = preview_connectbooks_xlsx(workbook([changed(Cost=None)]), profile())
    row = report.rows[0]
    assert row.amounts["cost"] is None and row.computed_profit is None
    control = next(c for c in report.controls if c.field == "cost")
    assert control.detail_total is None and control.status == "INCOMPLETE"
    assert "MISSING_AMOUNT" in codes(row) and report.status == "REVIEW_REQUIRED"


@pytest.mark.parametrize("field", ["Profit", "Net Sales"])
def test_row_reconciliation_failure_invalidates_metrics(field: str) -> None:
    row = changed(**{field: 999})
    result = preview_connectbooks_xlsx(workbook([row]), profile()).rows[0]
    assert result.status == "INVALID" and result.computed_profit is None
    assert any("RECONCILIATION_MISMATCH" in code for code in codes(result))


def test_footer_is_independent_control_and_does_not_get_summed_twice() -> None:
    report = preview_connectbooks_xlsx(workbook(totals={"Profit": "$999.00"}), profile())
    control = next(c for c in report.controls if c.field == "profit")
    assert control.detail_total == 50 and control.reported_total == 999
    assert control.status == "MISMATCH" and report.status == "REVIEW_REQUIRED"


def test_duplicate_skus_are_all_invalidated_without_aggregation() -> None:
    report = preview_connectbooks_xlsx(workbook([BASE, BASE]), profile())
    assert len(report.rows) == 2
    assert all(r.status == "INVALID" and r.computed_profit is None for r in report.rows)
    assert all("DUPLICATE_SELLER_SKU" in codes(r) for r in report.rows)


@pytest.mark.parametrize("item", [123, True, " 0000123", "0000123 "])
def test_identifiers_are_not_coerced_or_trimmed_into_a_match(item: object) -> None:
    row = preview_connectbooks_xlsx(workbook([changed(Item=item)]), profile()).rows[0]
    assert row.status == "INVALID" and row.computed_profit is None


def test_wrong_store_does_not_pass_with_matching_sku() -> None:
    row = preview_connectbooks_xlsx(workbook([changed(Store="Other Store")]), profile()).rows[0]
    assert row.status == "INVALID" and "STORE_MISMATCH" in codes(row)


@pytest.mark.parametrize(
    "cell", ["$40", Cell("NaN", "n"), Cell("#VALUE!", "e"), True, Cell("1e99", "n")]
)
def test_invalid_financial_cells_do_not_produce_metrics(cell: object) -> None:
    result = preview_connectbooks_xlsx(workbook([changed(Cost=cell)]), profile()).rows[0]
    assert result.status == "INVALID" and result.computed_profit is None
    assert result.amounts["cost"] is None


@pytest.mark.parametrize(
    "field,value", [("Sales Qty", -1), ("Refund Qty", 1), ("Total Units", Decimal("1.5"))]
)
def test_signed_quantity_contract_is_explicit(field: str, value: object) -> None:
    row = preview_connectbooks_xlsx(workbook([changed(**{field: value})]), profile()).rows[0]
    assert row.status == "INVALID" and "INVALID_AMOUNT" in codes(row)


def test_fractional_cents_and_decimal_context_are_preserved() -> None:
    cost = Decimal("40.1234567891")
    raw = workbook([changed(Cost=cost, Profit=Decimal(90) - cost)])
    expected = preview_connectbooks_xlsx(raw, profile()).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        assert preview_connectbooks_xlsx(raw, profile()).model_dump_json() == expected
    row = preview_connectbooks_xlsx(raw, profile()).rows[0]
    assert row.amounts["cost"] == cost and row.computed_profit == Decimal("49.8765432109")


def test_missing_period_context_and_ad_coverage_are_reviewed_without_guessed_dates() -> None:
    report = preview_connectbooks_xlsx(
        workbook(),
        profile(reporting_year=None, reporting_timezone=None, advertising_coverage_confirmed=False),
    )
    assert {
        "REPORTING_YEAR_UNCONFIRMED",
        "REPORTING_TIMEZONE_UNCONFIRMED",
        "ADVERTISING_COVERAGE_UNCONFIRMED",
    } <= codes(report)
    assert report.profile.reporting_year is None and report.status == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "changes",
    [
        {"reporting_month": True},
        {"reporting_month": 13},
        {"reporting_year": 0},
        {"reporting_timezone": "Invalid/Zone"},
        {"currency": "EUR"},
        {"store_name": " "},
    ],
)
def test_unsupported_profile_context_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        profile(**changes)


def test_mapped_header_duplicates_are_rejected_even_when_unused_duplicates_are_allowed() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="MAPPED_HEADER_MISSING_OR_AMBIGUOUS"):
        preview_connectbooks_xlsx(workbook(headers=[*BASE, "cost"]), profile())


def test_missing_required_column_is_not_replaced_by_a_similar_one() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="MAPPED_HEADER_MISSING_OR_AMBIGUOUS"):
        preview_connectbooks_xlsx(workbook(headers=[h for h in BASE if h != "Cost"]), profile())


def test_formula_cells_are_rejected_even_with_cached_values() -> None:
    raw = workbook([changed(Cost=Cell("40", "n", "1+39"))])
    with pytest.raises(ProfitabilityPreviewError, match="FORMULAS_NOT_SUPPORTED"):
        preview_connectbooks_xlsx(raw, profile())


def test_summary_is_required_and_must_be_last() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="SUMMARY_ROW_REQUIRED"):
        preview_connectbooks_xlsx(workbook(footer=False), profile())
    with pytest.raises(ProfitabilityPreviewError, match="SUMMARY_MUST_BE_LAST"):
        preview_connectbooks_xlsx(workbook([{"Net Sales": 0}, BASE]), profile())


def test_missing_sku_with_other_identity_is_an_invalid_row_not_a_footer() -> None:
    report = preview_connectbooks_xlsx(workbook([changed(Item=None)]), profile())
    assert report.rows[0].seller_sku is None and report.rows[0].status == "INVALID"


def test_fnsku_only_row_is_not_misclassified_as_the_control_row() -> None:
    row = changed(Item=None, FNSKU="X000000001", **{"Item ID": None, "Title": None, "Store": None})
    report = preview_connectbooks_xlsx(workbook([row]), profile())
    assert len(report.rows) == 1 and report.rows[0].status == "INVALID"
    assert "MISSING_SELLER_SKU" in codes(report.rows[0])


def test_absent_sheet_and_bad_package_have_safe_batch_errors() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="SHEET_NOT_FOUND"):
        preview_connectbooks_xlsx(workbook(), profile(sheet_name="Other"))
    with pytest.raises(ProfitabilityPreviewError, match="UNREADABLE_XLSX"):
        preview_connectbooks_xlsx(b"private malformed financial content", profile())


@pytest.mark.parametrize(
    "limit,value,code",
    [
        ("MAX_XLSX_BYTES", 1, "FILE_SIZE_LIMIT"),
        ("MAX_EXPANDED_BYTES", 1, "ARCHIVE_EXPANSION_LIMIT"),
        ("MAX_ROWS", 0, "ROW_LIMIT"),
        ("MAX_CELLS", 1, "CELL_LIMIT"),
        ("MAX_COLUMNS", 1, "COLUMN_LIMIT"),
    ],
)
def test_resource_limits_never_return_partial_success(
    monkeypatch: pytest.MonkeyPatch, limit: str, value: int, code: str
) -> None:
    monkeypatch.setattr(connectbooks, limit, value)
    with pytest.raises(ProfitabilityPreviewError, match=code):
        preview_connectbooks_xlsx(workbook(), profile())


def test_cli_validates_original_bytes_without_writes_and_has_safe_errors(tmp_path: Path) -> None:
    source, mapping = tmp_path / "input.xlsx", tmp_path / "profile.json"
    raw = workbook()
    source.write_bytes(raw)
    mapping.write_text(profile().model_dump_json())
    output, errors = io.StringIO(), io.StringIO()
    args = ["--profile", str(mapping), "--xlsx", str(source)]
    assert main(args, stdout=output, stderr=errors) == 0
    assert json.loads(output.getvalue())["rows"][0]["computed_profit"] == "50"
    assert source.read_bytes() == raw and errors.getvalue() == ""
    source.write_bytes(b"private-financial-data")
    output, errors = io.StringIO(), io.StringIO()
    assert main(args, stdout=output, stderr=errors) == 2
    assert output.getvalue() == "" and "private-financial-data" not in errors.getvalue()


def test_cli_rejects_duplicate_json_keys_and_missing_paths(tmp_path: Path) -> None:
    mapping = tmp_path / "profile.json"
    mapping.write_text('{"currency":"USD","currency":"EUR"}')
    output, errors = io.StringIO(), io.StringIO()
    args = ["--profile", str(mapping), "--xlsx", str(tmp_path / "absent.xlsx")]
    assert main(args, stdout=output, stderr=errors) == 2
    assert "DUPLICATE_PROFILE_JSON_KEY" in errors.getvalue() and not output.getvalue()
    mapping.write_text(profile().model_dump_json())
    errors = io.StringIO()
    assert main(args, stdout=output, stderr=errors) == 2
    assert "could not be read" in errors.getvalue()


def rewrite_package(raw: bytes, path: str, transform: Callable[[bytes], bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            target.writestr(entry.filename, transform(data) if entry.filename == path else data)
    return output.getvalue()


def test_shared_strings_and_rich_text_keep_leading_zeroes_without_phonetic_text() -> None:
    raw = workbook([changed(Item=Cell("0", "s"))])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(entry.filename, source.read(entry.filename))
        target.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{N[1:-1]}"><si>'
            "<r><t>0000</t></r><r><t>123</t></r>"
            "<rPh><t>not-part-of-sku</t></rPh></si></sst>",
        )
    row = preview_connectbooks_xlsx(output.getvalue(), profile()).rows[0]
    assert row.seller_sku == "0000123" and row.status == "RECONCILED"


def test_invalid_shared_string_index_has_safe_batch_error() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="INVALID_SHARED_STRING"):
        preview_connectbooks_xlsx(workbook([changed(Item=Cell("-1", "s"))]), profile())


@pytest.mark.parametrize("declaration", [b'<!DOCTYPE workbook [<!ENTITY x "secret">]>', b"\x00"])
def test_unsupported_xml_cannot_be_interpreted_as_financial_data(declaration: bytes) -> None:
    raw = rewrite_package(workbook(), "xl/workbook.xml", lambda b: declaration + b)
    with pytest.raises(ProfitabilityPreviewError, match="UNSUPPORTED_XML"):
        preview_connectbooks_xlsx(raw, profile())


@pytest.mark.parametrize(
    "replacement",
    [b'Target="https://example.test/report" TargetMode="External"', b'Target="../../outside.xml"'],
)
def test_external_or_escaped_worksheet_relationships_are_not_followed(replacement: bytes) -> None:
    raw = rewrite_package(
        workbook(),
        "xl/_rels/workbook.xml.rels",
        lambda b: b.replace(b'Target="worksheets/sheet1.xml"', replacement),
    )
    with pytest.raises(ProfitabilityPreviewError, match="UNSUPPORTED_WORKSHEET_RELATIONSHIP"):
        preview_connectbooks_xlsx(raw, profile())


@pytest.mark.parametrize(
    "path,old,new,code",
    [
        ("xl/worksheets/sheet1.xml", b'r="A2"', b'r="A3"', "INVALID_CELL_REFERENCE"),
        ("xl/worksheets/sheet1.xml", b'r="B2"', b'r="A2"', "DUPLICATE_CELL_REFERENCE"),
        (
            "xl/worksheets/sheet1.xml",
            b'<ns0:row r="2">',
            b'<ns0:row r="1">',
            "INVALID_ROW_SEQUENCE",
        ),
    ],
)
def test_duplicate_or_misnumbered_cells_are_rejected(
    path: str, old: bytes, new: bytes, code: str
) -> None:
    raw = rewrite_package(workbook(), path, lambda b: b.replace(old, new))
    with pytest.raises(ProfitabilityPreviewError, match=code):
        preview_connectbooks_xlsx(raw, profile())


def test_unknown_amount_in_summary_is_incomplete_not_zero() -> None:
    result = preview_connectbooks_xlsx(workbook(totals={"Cost": None}), profile())
    control = next(c for c in result.controls if c.field == "cost")
    assert control.reported_total is None and control.status == "INCOMPLETE"
    assert result.status == "REVIEW_REQUIRED"


def test_blank_rows_preserve_source_numbers_and_do_not_become_unallocated() -> None:
    report = preview_connectbooks_xlsx(workbook([BASE, {}, changed(Item="second")]), profile())
    assert report.blank_rows == 1 and [r.row_number for r in report.rows] == [2, 4]
    assert report.unallocated_rows == 0 and report.summary_row_number == 5


def test_one_cent_rounding_tolerance_does_not_hide_larger_discrepancies() -> None:
    accepted = preview_connectbooks_xlsx(workbook(totals={"Profit": "$50.01"}), profile())
    rejected = preview_connectbooks_xlsx(workbook(totals={"Profit": "$50.02"}), profile())
    assert next(c for c in accepted.controls if c.field == "profit").status == "MATCH"
    assert next(c for c in rejected.controls if c.field == "profit").status == "MISMATCH"
