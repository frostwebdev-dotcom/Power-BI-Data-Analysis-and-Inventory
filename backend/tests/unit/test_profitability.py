"""Strict financial CSV preview, unknown costs and reporting context (ADR 0023)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cli.profitability_preview import main
from app.imports.readers import ReadOptions, UnreadableFile, open_rows
from app.models.enums import FileFormat
from app.schemas.profitability import ExpenseField, ProfitabilityCsvProfile
from app.services import profitability
from app.services.profitability import ProfitabilityPreviewError, preview_profitability_csv

COLUMNS = {
    "seller_sku": "Seller SKU",
    "units_sold": "Units",
    "net_revenue": "Net Revenue",
    "cost_of_goods": "COGS",
    "amazon_fees": "Amazon Fees",
    "shipping": "Shipping",
    "advertising": "Advertising",
    "other_costs": "Other Costs",
    "currency": "Currency",
}
VALUES = ["0000123", "10", "1000.00", "400.00", "150.00", "50.00", "20.00", "0.00", "USD"]


def profile(**changes: object) -> ProfitabilityCsvProfile:
    values: dict[str, object] = {
        "name": "Synthetic layout",
        "version": 1,
        "source_name": "Test fixture",
        "marketplace_id": "ATVPDKIKX0DER",
        "currency": "USD",
        "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z",
        "values_basis": "period_totals",
        "revenue_basis": "net_after_refunds_excluding_tax",
        "expense_basis": "positive_expense_totals",
        "quantity_basis": "seller_sku_units",
        "columns": COLUMNS.copy(),
        "confirmed_zero_costs": [],
        "encoding": "utf-8-sig",
        "delimiter": ",",
        "decimal_separator": ".",
        "thousands_separator": ",",
    }
    values.update(changes)
    return ProfitabilityCsvProfile.model_validate(values)


def content(
    rows: list[list[str]] | None = None,
    *,
    headers: list[str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(headers if headers is not None else list(COLUMNS.values()))
    writer.writerows(rows if rows is not None else [VALUES])
    return buffer.getvalue().encode(encoding)


def changed_cell(field: str, value: str) -> bytes:
    row = VALUES.copy()
    row[list(COLUMNS).index(field)] = value
    return content([row])


def test_complete_period_totals_compute_profit_margin_roi_and_per_sku_unit_values() -> None:
    raw = content()
    report = preview_profitability_csv(raw, profile())
    row = report.rows[0]
    assert report.status == "VALIDATED_PREVIEW"
    assert report.preview_only and not report.persistence_performed
    assert report.identity_resolution_performed is False
    assert report.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert row.seller_sku == "0000123" and row.identity_status == "UNRESOLVED"
    assert row.row_number == 2 and row.units_sold == 10
    assert row.status == "CALCULATED" and row.issues == ()
    assert row.metrics.net_profit == Decimal("380.00")
    assert row.metrics.margin_percent == Decimal(38)
    assert row.metrics.roi_percent == Decimal(95)
    assert row.metrics.net_revenue_per_sku_unit == Decimal(100)
    assert row.metrics.cost_per_sku_unit == Decimal(40)
    assert row.metrics.profit_per_sku_unit == Decimal(38)


def test_raw_bytes_and_profile_are_unchanged_by_repeatable_preview() -> None:
    raw = content()
    original = bytes(raw)
    mapping = profile()
    before = mapping.model_dump_json()
    expected = preview_profitability_csv(raw, mapping).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        assert preview_profitability_csv(raw, mapping).model_dump_json() == expected
    assert raw == original and mapping.model_dump_json() == before


@pytest.mark.parametrize(
    "field", ["cost_of_goods", "amazon_fees", "shipping", "advertising", "other_costs"]
)
def test_blank_costs_never_become_zero_or_produce_profit(field: ExpenseField) -> None:
    report = preview_profitability_csv(changed_cell(field, ""), profile())
    row = report.rows[0]
    assert report.status == "REVIEW_REQUIRED" and row.status == "INCOMPLETE"
    assert row.amounts[field] is None
    assert row.metrics.net_profit is None
    assert row.metrics.margin_percent is None and row.metrics.roi_percent is None
    assert any(issue.code == "MISSING_AMOUNT" and issue.field == field for issue in row.issues)


def test_unmapped_costs_are_unknown_unless_explicitly_confirmed_zero() -> None:
    columns = {key: value for key, value in COLUMNS.items() if key != "other_costs"}
    missing = preview_profitability_csv(content(), profile(columns=columns)).rows[0]
    assert missing.amounts["other_costs"] is None and missing.metrics.net_profit is None
    confirmed = preview_profitability_csv(
        content(), profile(columns=columns, confirmed_zero_costs=["other_costs"])
    ).rows[0]
    assert confirmed.amounts["other_costs"] == 0
    assert confirmed.metrics.net_profit == Decimal(380)


def test_missing_revenue_is_not_zero() -> None:
    row = preview_profitability_csv(changed_cell("net_revenue", ""), profile()).rows[0]
    assert row.amounts["net_revenue"] is None and row.metrics.net_profit is None


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", "1e3", "$100", "12,34.00", "(100)", "1.1234567", "9" * 19]
)
def test_invalid_money_formats_do_not_produce_metrics(raw: str) -> None:
    row = preview_profitability_csv(changed_cell("net_revenue", raw), profile()).rows[0]
    assert row.status == "INVALID" and row.metrics.net_profit is None
    assert any(issue.code == "INVALID_AMOUNT" for issue in row.issues)


def test_negative_expenses_require_a_different_explicit_source_convention() -> None:
    row = preview_profitability_csv(changed_cell("amazon_fees", "-150.00"), profile()).rows[0]
    assert row.status == "INVALID" and row.metrics.net_profit is None


@pytest.mark.parametrize("revenue", ["0", "-50"])
def test_nonpositive_revenue_preserves_loss_without_inventing_margin(revenue: str) -> None:
    row = preview_profitability_csv(changed_cell("net_revenue", revenue), profile()).rows[0]
    assert row.metrics.net_profit == Decimal(revenue) - Decimal(620)
    assert row.metrics.margin_percent is None
    assert any(issue.code == "MARGIN_UNDEFINED" for issue in row.issues)


def test_zero_cogs_has_undefined_roi_instead_of_infinity() -> None:
    row = preview_profitability_csv(changed_cell("cost_of_goods", "0"), profile()).rows[0]
    assert row.metrics.net_profit == 780 and row.metrics.roi_percent is None
    assert any(issue.code == "ROI_UNDEFINED" for issue in row.issues)


@pytest.mark.parametrize("units", ["0", ""])
def test_missing_or_zero_units_do_not_produce_per_unit_values(units: str) -> None:
    row = preview_profitability_csv(changed_cell("units_sold", units), profile()).rows[0]
    assert row.metrics.net_profit == 380
    assert row.metrics.net_revenue_per_sku_unit is None
    assert row.metrics.cost_per_sku_unit is None and row.metrics.profit_per_sku_unit is None
    if not units:
        assert row.status == "INCOMPLETE" and row.units_sold is None


@pytest.mark.parametrize("units", ["-1", "1.5", "TRUE"])
def test_invalid_units_block_financial_metrics(units: str) -> None:
    row = preview_profitability_csv(changed_cell("units_sold", units), profile()).rows[0]
    assert row.status == "INVALID" and row.metrics.net_profit is None


def test_duplicate_skus_all_require_resolution_with_no_aggregation() -> None:
    report = preview_profitability_csv(content([VALUES, VALUES]), profile())
    assert report.status == "REVIEW_REQUIRED" and len(report.rows) == 2
    for row in report.rows:
        assert row.status == "INVALID" and row.metrics.net_profit is None
        assert any(issue.code == "DUPLICATE_SELLER_SKU" for issue in row.issues)


@pytest.mark.parametrize("currency", ["EUR", "", "usd"])
def test_mapped_currency_must_match_the_explicit_file_currency(currency: str) -> None:
    row = preview_profitability_csv(changed_cell("currency", currency), profile()).rows[0]
    assert row.status == "INVALID" and row.metrics.net_profit is None
    assert any(issue.code == "CURRENCY_MISMATCH" for issue in row.issues)


def test_missing_sku_is_not_assigned_a_guessed_identity() -> None:
    row = preview_profitability_csv(changed_cell("seller_sku", " "), profile()).rows[0]
    assert row.seller_sku is None and row.status == "INVALID"
    assert row.identity_status == "UNRESOLVED"


def test_explicit_decimal_comma_and_grouping() -> None:
    values = ["0000123", "10", "1.000,00", "400,00", "150,00", "50,00", "20,00", "0,00", "EUR"]
    row = preview_profitability_csv(
        content([values], delimiter=";"),
        profile(currency="EUR", delimiter=";", decimal_separator=",", thousands_separator="."),
    ).rows[0]
    assert row.metrics.net_profit == Decimal(380)


def test_grouped_decimal_dot_is_parsed_without_float_conversion() -> None:
    row = preview_profitability_csv(changed_cell("net_revenue", "1,000.01"), profile()).rows[0]
    assert row.metrics.net_profit == Decimal("380.01")


def test_declared_encoding_and_bom_are_respected() -> None:
    values = VALUES.copy()
    values[0] = "001-caf\u00e9"
    raw = content([values], encoding="cp1252")
    assert (
        preview_profitability_csv(raw, profile(encoding="cp1252")).rows[0].seller_sku
        == "001-caf\u00e9"
    )
    assert (
        preview_profitability_csv(b"\xef\xbb\xbf" + content(), profile()).rows[0].seller_sku
        == "0000123"
    )


def test_multiline_cells_keep_physical_row_numbers_and_blank_rows() -> None:
    second = VALUES.copy()
    second[0] = "second-sku"
    raw = content(
        [[*VALUES, "two\nlines"], [], [*second, "note"]], headers=[*COLUMNS.values(), "Notes"]
    )
    report = preview_profitability_csv(raw, profile())
    assert [row.row_number for row in report.rows] == [2, 5]
    assert report.blank_rows == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"Seller SKU,Units\nabc,1,extra\n",
        b"Seller SKU,Units\nabc\n",
        b'Seller SKU,Units\n"unterminated,1\n',
    ],
)
def test_financial_reader_rejects_ragged_or_malformed_rows(raw: bytes) -> None:
    with pytest.raises(ProfitabilityPreviewError, match="UNREADABLE_CSV"):
        preview_profitability_csv(
            raw, profile(columns={"seller_sku": "Seller SKU", "units_sold": "Units"})
        )


def test_default_vendor_reader_still_trims_wide_rows_but_opt_in_is_strict() -> None:
    raw = b"SKU,Qty\na,2,extra\n"
    assert next(open_rows(raw, ReadOptions(file_format=FileFormat.CSV)).rows).cells == ["a", "2"]
    strict = open_rows(raw, ReadOptions(file_format=FileFormat.CSV, strict_row_width=True))
    with pytest.raises(UnreadableFile, match="row 2"):
        list(strict.rows)


@pytest.mark.parametrize("headers", [["SKU", "sku"], ["SKU", "", "Units"]])
def test_ambiguous_headers_are_rejected_even_if_a_field_is_not_mapped(headers: list[str]) -> None:
    with pytest.raises(ProfitabilityPreviewError, match="AMBIGUOUS_HEADERS"):
        preview_profitability_csv(content(headers=headers), profile(columns={"seller_sku": "SKU"}))


def test_mapped_missing_column_is_not_replaced_by_a_similar_header() -> None:
    with pytest.raises(ProfitabilityPreviewError, match="MAPPED_COLUMN_NOT_FOUND"):
        preview_profitability_csv(content(), profile(columns={"seller_sku": "Similar SKU Name"}))


@pytest.mark.parametrize("raw", [b"", b"SKU\n", b"SKU\n\n"])
def test_empty_financial_files_are_not_accepted(raw: bytes) -> None:
    with pytest.raises(ProfitabilityPreviewError):
        preview_profitability_csv(raw, profile(columns={"seller_sku": "SKU"}))


def test_file_and_row_limits_do_not_return_a_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profitability, "MAX_CSV_ROWS", 1)
    with pytest.raises(ProfitabilityPreviewError, match="ROW_LIMIT_EXCEEDED"):
        preview_profitability_csv(content([VALUES, VALUES]), profile())
    monkeypatch.setattr(profitability, "MAX_CSV_BYTES", 1)
    with pytest.raises(ProfitabilityPreviewError, match="FILE_SIZE_LIMIT_EXCEEDED"):
        preview_profitability_csv(content(), profile())


@pytest.mark.parametrize(
    "changes",
    [
        {"period_start": "2026-10-01"},
        {"period_end": "2026-09-01T00:00:00Z"},
        {"values_basis": "per_unit"},
        {"revenue_basis": "gross_sales"},
        {"expense_basis": "signed_expenses"},
        {"quantity_basis": "individual_pieces"},
        {"decimal_separator": ","},
        {"columns": {}},
        {"columns": {"seller_sku": "SKU", "net_revenue": "sku"}},
        {"confirmed_zero_costs": ["shipping"]},
        {"confirmed_zero_costs": ["net_revenue"]},
        {"confirmed_zero_costs": ["shipping", "shipping"]},
        {"currency": "usd"},
        {"name": " "},
        {"version": True},
    ],
)
def test_profile_rejects_ambiguous_financial_semantics(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        profile(**changes)


def test_reporting_bounds_are_normalized_to_utc() -> None:
    mapping = profile(period_start="2026-10-01T04:00:00+04:00")
    assert mapping.period_start.hour == 0 and mapping.period_start.tzinfo is UTC


def test_explicit_header_offset_keeps_source_row_numbers() -> None:
    raw = b"Financial report\n" + content()
    report = preview_profitability_csv(raw, profile(header_row_index=1))
    assert report.rows[0].row_number == 3
    assert report.rows[0].metrics.net_profit == 380


def test_fractional_cents_are_retained_without_early_rounding() -> None:
    row = preview_profitability_csv(changed_cell("shipping", "50.123456"), profile()).rows[0]
    assert row.amounts["shipping"] == Decimal("50.123456")
    assert row.metrics.net_profit == Decimal("379.876544")


def test_cli_preview_and_bad_profile_do_not_write_or_echo_input(tmp_path: Path) -> None:
    csv_path, profile_path = tmp_path / "financial.csv", tmp_path / "profile.json"
    csv_path.write_bytes(content())
    profile_path.write_text(profile().model_dump_json())
    before_csv = csv_path.read_bytes()
    output, errors = io.StringIO(), io.StringIO()
    args = ["--csv", str(csv_path), "--profile", str(profile_path)]
    assert main(args, stdout=output, stderr=errors) == 0
    parsed = json.loads(output.getvalue())
    assert parsed["preview_only"] is True
    assert parsed["rows"][0]["metrics"]["net_profit"] == "380.00"
    assert csv_path.read_bytes() == before_csv and errors.getvalue() == ""
    profile_path.write_text('{"private_input":"do-not-echo"}')
    output, errors = io.StringIO(), io.StringIO()
    assert main(args, stdout=output, stderr=errors) == 2
    assert output.getvalue() == "" and "do-not-echo" not in errors.getvalue()


def test_cli_safe_error_for_missing_or_invalid_csv_file(tmp_path: Path) -> None:
    mapping = tmp_path / "profile.json"
    mapping.write_text(profile().model_dump_json())
    output, errors = io.StringIO(), io.StringIO()
    assert (
        main(
            ["--profile", str(mapping), "--csv", str(tmp_path / "absent")],
            stdout=output,
            stderr=errors,
        )
        == 2
    )
    assert output.getvalue() == "" and "could not be read" in errors.getvalue()
    raw = tmp_path / "bad.csv"
    raw.write_bytes(b"not,a,valid,export\nprivate-business-data\n")
    errors = io.StringIO()
    assert main(["--profile", str(mapping), "--csv", str(raw)], stdout=output, stderr=errors) == 2
    assert "private-business-data" not in errors.getvalue()


def test_cli_rejects_duplicate_profile_keys_instead_of_last_value_winning(tmp_path: Path) -> None:
    mapping = tmp_path / "profile.json"
    mapping.write_text('{"columns":{"seller_sku":"one","seller_sku":"two"}}')
    output, errors = io.StringIO(), io.StringIO()
    assert (
        main(
            ["--profile", str(mapping), "--csv", str(tmp_path / "not-read.csv")],
            stdout=output,
            stderr=errors,
        )
        == 2
    )
    assert output.getvalue() == "" and "DUPLICATE_PROFILE_JSON_KEY" in errors.getvalue()
