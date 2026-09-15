"""Every rule-shape validator (AC-6, ADR 0013), the header signature, and the
readers and mapper on in-memory files. No database."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from app.imports.extract import IssueCode
from app.imports.mapping import map_table
from app.imports.profile_rules import (
    RULE_MODELS,
    AvailabilityRules,
    ColumnMap,
    ColumnTarget,
    NormalizationRules,
    PackSizeHandling,
    PriceSemantics,
    ProfileRules,
    QuantitySemantics,
    compute_header_signature,
    normalize_header,
    rule_json_schemas,
)
from app.imports.readers import ReadOptions, UnreadableFile, cell_text, read_table
from app.models.enums import AvailabilityStatus, FileFormat, ImportRowStatus


def columns(*mappings: tuple[str, str | int]) -> ColumnMap:
    return ColumnMap(columns=[{"target": t, "source": s} for t, s in mappings])


MINIMAL = columns(("upc", "UPC"), ("quantity_available", "Quantity"))


# --- column map --------------------------------------------------------------------------


class TestColumnMap:
    def test_minimal_valid_map(self) -> None:
        assert MINIMAL.targets() == {ColumnTarget.UPC, ColumnTarget.QUANTITY_AVAILABLE}

    def test_index_sources_are_accepted(self) -> None:
        cm = columns(("vendor_sku", 0), ("quantity_available", 2))
        assert cm.columns[0].source == 0
        assert cm.columns[0].source_key == 0

    def test_header_sources_are_normalised_for_matching_but_kept_as_given(self) -> None:
        cm = columns(("upc", "  Qty   Available "), ("quantity_available", "x"))
        assert cm.columns[0].source == "Qty   Available"
        assert cm.columns[0].source_key == "qty available"

    def test_quantity_available_is_required(self) -> None:
        with pytest.raises(ValidationError, match="quantity_available must be mapped"):
            columns(("upc", "UPC"), ("description", "Desc"))

    def test_upc_or_vendor_sku_is_required(self) -> None:
        with pytest.raises(ValidationError, match="upc or vendor_sku"):
            columns(("description", "Desc"), ("quantity_available", "Qty"))

    def test_vendor_sku_alone_satisfies_the_identifier_rule(self) -> None:
        columns(("vendor_sku", "SKU"), ("quantity_available", "Qty"))

    def test_a_target_may_be_mapped_once(self) -> None:
        with pytest.raises(ValidationError, match="duplicated: \\['upc'\\]"):
            columns(("upc", "UPC"), ("upc", "Barcode"), ("quantity_available", "Qty"))

    def test_ignore_may_repeat(self) -> None:
        columns(("upc", "UPC"), ("quantity_available", "Qty"), ("ignore", "A"), ("ignore", "B"))

    def test_a_source_may_be_mapped_once(self) -> None:
        with pytest.raises(ValidationError, match="each source column may be mapped once"):
            columns(("upc", "Code"), ("vendor_sku", "code"), ("quantity_available", "Qty"))

    @pytest.mark.parametrize("source", ["", "   ", -1, True])
    def test_bad_sources_are_refused(self, source: Any) -> None:
        with pytest.raises(ValidationError):
            ColumnMap(
                columns=[
                    {"target": "upc", "source": source},
                    {"target": "quantity_available", "source": "q"},
                ]
            )

    def test_unknown_target_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ColumnMap(
                columns=[
                    {"target": "price", "source": "P"},
                    {"target": "quantity_available", "source": "q"},
                ]
            )

    def test_extra_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ColumnMap(
                columns=[
                    {"target": "upc", "source": "U", "regex": ".*"},
                    {"target": "quantity_available", "source": "q"},
                ]
            )

    def test_an_empty_map_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ColumnMap(columns=[])


# --- the other five --------------------------------------------------------------------


class TestNormalizationRules:
    def test_defaults(self) -> None:
        rules = NormalizationRules()
        assert (rules.trim, rules.upc_strip_non_digits, rules.upc_pad_to_12) == (True, True, True)
        assert (rules.decimal_separator, rules.thousands_separator) == (".", "")
        assert rules.currency_symbols_to_strip == ["$"]

    def test_separators_must_differ(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            NormalizationRules(decimal_separator=",", thousands_separator=",")

    def test_european_separators(self) -> None:
        rules = NormalizationRules(decimal_separator=",", thousands_separator=".")
        assert rules.thousands_separator == "."

    @pytest.mark.parametrize("bad", ["5", ".", ","])
    def test_a_currency_symbol_cannot_be_a_digit_or_separator(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            NormalizationRules(currency_symbols_to_strip=[bad])

    def test_unknown_separator_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            NormalizationRules(decimal_separator=";")


class TestAvailabilityRules:
    def test_default_is_quantity_gt_zero(self) -> None:
        assert AvailabilityRules().available_when == "quantity_gt_zero"

    def test_status_column_mode_needs_the_column(self) -> None:
        with pytest.raises(ValidationError, match="status_column is required"):
            AvailabilityRules(available_when="status_column", available_values=["Y"])

    def test_status_column_mode_needs_at_least_one_value_list(self) -> None:
        with pytest.raises(ValidationError, match="needs available_values or unavailable_values"):
            AvailabilityRules(available_when="status_column", status_column="Status")

    def test_a_value_cannot_be_both(self) -> None:
        with pytest.raises(ValidationError, match="both available and unavailable"):
            AvailabilityRules(
                available_when="status_column",
                status_column="Status",
                available_values=["In Stock"],
                unavailable_values=["in stock"],
            )

    def test_values_are_trimmed_and_distinct(self) -> None:
        rules = AvailabilityRules(
            available_when="status_column", status_column="S", available_values=[" Y ", "yes"]
        )
        assert rules.available_values == ["Y", "yes"]
        with pytest.raises(ValidationError, match="distinct"):
            AvailabilityRules(
                available_when="status_column", status_column="S", available_values=["Y", "y"]
            )

    def test_quantity_mode_rejects_status_fields(self) -> None:
        with pytest.raises(ValidationError, match="apply only to status_column mode"):
            AvailabilityRules(status_column="Status")


class TestQuantitySemantics:
    def test_defaults(self) -> None:
        q = QuantitySemantics()
        assert (q.unit, q.case_pack_from, q.fixed_pack_size) == ("each", "pack_size_column", None)

    def test_fixed_case_pack_needs_a_size(self) -> None:
        with pytest.raises(ValidationError, match="fixed_pack_size is required"):
            QuantitySemantics(unit="case", case_pack_from="fixed")

    def test_fixed_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            QuantitySemantics(unit="case", case_pack_from="fixed", fixed_pack_size=0)

    def test_fixed_size_is_refused_in_column_mode(self) -> None:
        with pytest.raises(ValidationError, match="applies only when case_pack_from is fixed"):
            QuantitySemantics(case_pack_from="pack_size_column", fixed_pack_size=6)


class TestPriceSemantics:
    def test_currency_is_normalised(self) -> None:
        assert PriceSemantics(currency=" eur ").currency == "EUR"

    def test_bad_currency_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ISO 4217"):
            PriceSemantics(currency="dollars")

    def test_per_is_each_or_case(self) -> None:
        assert PriceSemantics(per="case").per == "case"
        with pytest.raises(ValidationError):
            PriceSemantics(per="pallet")


class TestPackSizeHandling:
    def test_only_store_as_stated_exists(self) -> None:
        assert PackSizeHandling().mode == "store_as_stated"
        with pytest.raises(ValidationError):
            PackSizeHandling(mode="normalize_to_each")


# --- cross-rule checks and schemas ------------------------------------------------------


class TestProfileRules:
    def test_case_quantities_from_a_column_need_a_pack_size_mapping(self) -> None:
        with pytest.raises(ValidationError, match="maps no pack_size"):
            ProfileRules(
                column_map=MINIMAL,
                quantity_semantics=QuantitySemantics(
                    unit="case", case_pack_from="pack_size_column"
                ),
            )

    def test_case_quantities_with_a_fixed_pack_need_no_column(self) -> None:
        ProfileRules(
            column_map=MINIMAL,
            quantity_semantics=QuantitySemantics(
                unit="case", case_pack_from="fixed", fixed_pack_size=12
            ),
        )

    def test_price_per_case_needs_a_cost_column(self) -> None:
        with pytest.raises(ValidationError, match="maps no unit_cost"):
            ProfileRules(column_map=MINIMAL, price_semantics=PriceSemantics(per="case"))

    def test_round_trip_through_the_stored_columns(self) -> None:
        rules = ProfileRules(column_map=MINIMAL)
        stored = rules.as_columns()

        assert set(stored) == set(RULE_MODELS)
        assert stored["column_map"] == {
            "columns": [
                {"target": "upc", "source": "UPC", "required": False},
                {"target": "quantity_available", "source": "Quantity", "required": False},
            ]
        }
        assert ProfileRules.from_columns(**stored) == rules

    def test_json_schemas_exist_for_all_six(self) -> None:
        schemas = rule_json_schemas()

        assert set(schemas) == set(RULE_MODELS)
        assert schemas["column_map"]["properties"]["columns"]["type"] == "array"
        targets = schemas["column_map"]["$defs"]["ColumnTarget"]["enum"]
        assert targets == [t.value for t in ColumnTarget]
        assert schemas["pack_size_handling"]["properties"]["mode"]["const"] == "store_as_stated"


# --- header signature -------------------------------------------------------------------


class TestHeaderSignature:
    def test_case_and_whitespace_do_not_matter(self) -> None:
        assert normalize_header("  Qty   Available ") == "qty available"
        assert compute_header_signature(["UPC", "Description", "Quantity", "Cost"]) == (
            compute_header_signature(["upc", " description ", "QUANTITY", "cost"])
        )

    def test_order_renames_and_additions_all_change_it(self) -> None:
        base = compute_header_signature(["UPC", "Quantity"])

        assert compute_header_signature(["Quantity", "UPC"]) != base
        assert compute_header_signature(["UPC", "Qty"]) != base
        assert compute_header_signature(["UPC", "Quantity", "Cost"]) != base

    def test_it_is_a_sha256_hex(self) -> None:
        signature = compute_header_signature(["a"])
        assert len(signature) == 64 and int(signature, 16)


# --- readers ---------------------------------------------------------------------------


def csv_bytes(rows: list[list[str]], *, delimiter: str = ",", encoding: str = "utf-8") -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def xlsx_bytes(rows: list[list[Any]], *, sheet: str = "Sheet1") -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


CSV_OPTIONS = ReadOptions(file_format=FileFormat.CSV)
XLSX_OPTIONS = ReadOptions(file_format=FileFormat.XLSX)


class TestReaders:
    def test_csv_header_and_rows_as_strings(self) -> None:
        table = read_table(csv_bytes([["UPC", "Quantity"], ["012345678905", "7"]]), CSV_OPTIONS)

        assert table.headers == ["UPC", "Quantity"]
        assert table.rows == [["012345678905", "7"]]
        assert table.encoding == "utf-8"  # no BOM; with one it is utf-8-sig

    def test_csv_utf8_bom_and_cp1252_fallback(self) -> None:
        bom = b"\xef\xbb\xbf" + csv_bytes([["UPC", "Qty"], ["1", "2"]])
        assert read_table(bom, CSV_OPTIONS).headers == ["UPC", "Qty"]
        latin = csv_bytes([["Descripción", "Qty"], ["café", "2"]], encoding="cp1252")
        table = read_table(latin, CSV_OPTIONS)
        assert table.headers[0] == "Descripción" and table.encoding == "cp1252"

    def test_csv_delimiter_from_profile_or_sniffed(self) -> None:
        semi = csv_bytes([["UPC", "Qty"], ["1", "2"]], delimiter=";")
        assert read_table(semi, CSV_OPTIONS).headers == ["UPC", "Qty"]
        tab = csv_bytes([["UPC", "Qty"], ["1", "2"]], delimiter="\t")
        assert read_table(tab, ReadOptions(file_format=FileFormat.CSV, delimiter="\t")).rows == [
            ["1", "2"]
        ]

    def test_skip_rows_and_header_row_index(self) -> None:
        content = csv_bytes([["Report", ""], ["", ""], ["UPC", "Qty"], ["1", "2"]])
        table = read_table(content, ReadOptions(file_format=FileFormat.CSV, skip_rows=2))
        assert table.headers == ["UPC", "Qty"]
        table = read_table(content, ReadOptions(file_format=FileFormat.CSV, header_row_index=2))
        assert table.headers == ["UPC", "Qty"] and table.rows == [["1", "2"]]

    def test_max_rows_and_truncation_flag(self) -> None:
        content = csv_bytes([["UPC", "Qty"]] + [[str(i), "1"] for i in range(30)])
        table = read_table(content, CSV_OPTIONS, max_rows=20)
        assert len(table.rows) == 20 and table.truncated is True

    def test_blank_lines_are_skipped_and_short_rows_padded(self) -> None:
        content = csv_bytes([["UPC", "Qty", "Cost"], ["1"], [], ["2", "3", "4"]])
        assert read_table(content, CSV_OPTIONS).rows == [["1", "", ""], ["2", "3", "4"]]

    def test_missing_header_is_an_error(self) -> None:
        with pytest.raises(UnreadableFile, match="no header row"):
            read_table(b"", CSV_OPTIONS)

    def test_xlsx_numeric_upc_keeps_its_digits(self) -> None:
        """AC-5.5: a UPC stored as a number must not become 1.23457E+11 or lose zeros."""
        content = xlsx_bytes(
            [["UPC", "Quantity", "Cost"], [12345678905, 7, 19.99], ["012345678905", 0, 5]]
        )

        table = read_table(content, XLSX_OPTIONS)

        assert table.rows == [["12345678905", "7", "19.99"], ["012345678905", "0", "5"]]
        assert table.sheet == "Sheet1"

    def test_xlsx_sheet_selection(self) -> None:
        from openpyxl import Workbook

        workbook = Workbook()
        first = workbook.active
        assert first is not None
        first.title = "Summary"
        first.append(["nothing"])
        second = workbook.create_sheet("Inventory")
        second.append(["UPC", "Qty"])
        second.append(["1", "2"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        by_name = read_table(
            buffer.getvalue(), ReadOptions(file_format=FileFormat.XLSX, sheet_name="Inventory")
        )
        by_index = read_table(
            buffer.getvalue(), ReadOptions(file_format=FileFormat.XLSX, sheet_index=1)
        )
        assert by_name.headers == by_index.headers == ["UPC", "Qty"]
        with pytest.raises(UnreadableFile, match="not found"):
            read_table(
                buffer.getvalue(), ReadOptions(file_format=FileFormat.XLSX, sheet_name="Nope")
            )

    def test_garbage_is_not_a_workbook(self) -> None:
        with pytest.raises(UnreadableFile, match="not a readable XLSX"):
            read_table(b"not a zip", XLSX_OPTIONS)

    @pytest.mark.parametrize(
        ("value", "text"),
        [(None, ""), (True, "TRUE"), (7, "7"), (7.0, "7"), (19.99, "19.99"), ("x", "x")],
    )
    def test_cell_text(self, value: Any, text: str) -> None:
        assert cell_text(value) == text


# --- mapper ----------------------------------------------------------------------------


class TestMapper:
    """map_table runs the sample through app.imports.extract; rows come back
    as ExtractedRow with typed values and coded issues."""

    HEADERS: ClassVar[list[str]] = ["UPC", "Description", "Quantity", "Cost"]

    def rules(self) -> ProfileRules:
        return ProfileRules(
            column_map=columns(
                ("upc", "UPC"),
                ("description", "Description"),
                ("quantity_available", "Quantity"),
                ("unit_cost", "Cost"),
            )
        )

    def test_rows_are_mapped_and_normalised(self) -> None:
        preview = map_table(self.rules(), self.HEADERS, [["012345678905", "Widget", "7", "$19.99"]])

        assert preview.header_ok and preview.issues == []
        [row] = preview.rows
        assert row.raw_upc == "012345678905"
        assert row.normalized_upc == "00012345678905"
        assert row.has_valid_checksum is True
        assert row.quantity == 7
        assert row.unit_cost == Decimal("19.99") and row.currency == "USD"
        assert row.availability_status is AvailabilityStatus.AVAILABLE
        assert row.issues == [] and row.status is ImportRowStatus.OK
        assert row.raw == {
            "upc": "012345678905",
            "description": "Widget",
            "quantity_available": "7",
            "unit_cost": "$19.99",
        }

    def test_thousands_separator_needs_the_rule(self) -> None:
        rules = self.rules()
        preview = map_table(rules, self.HEADERS, [["012345678905", "W", "1", "$1,299.50"]])
        [issue] = preview.rows[0].issues
        assert issue.code is IssueCode.PRICE_INVALID and issue.field == "unit_cost"
        assert preview.rows[0].status is ImportRowStatus.WARNING
        assert preview.rows[0].unit_cost is None

        rules = ProfileRules(
            column_map=rules.column_map,
            normalization_rules=NormalizationRules(thousands_separator=","),
        )
        preview = map_table(rules, self.HEADERS, [["012345678905", "W", "1", "$1,299.50"]])
        assert preview.rows[0].unit_cost == Decimal("1299.50")

    def test_decimal_comma_and_space_grouping(self) -> None:
        rules = ProfileRules(
            column_map=self.rules().column_map,
            normalization_rules=NormalizationRules(
                decimal_separator=",", thousands_separator=".", currency_symbols_to_strip=["€"]
            ),
        )
        preview = map_table(rules, self.HEADERS, [["012345678905", "W", "1.250", "€1.299,50"]])
        assert preview.rows[0].quantity == 1250
        assert preview.rows[0].unit_cost == Decimal("1299.50")

    def test_header_matching_is_case_and_space_insensitive(self) -> None:
        preview = map_table(
            self.rules(), ["upc ", " DESCRIPTION", "quantity", "COST"], [["1", "x", "2", "3"]]
        )
        assert preview.header_ok

    def test_a_missing_required_column_is_reported_and_rows_still_map(self) -> None:
        preview = map_table(
            self.rules(), ["UPC", "Description", "Cost"], [["012345678905", "x", "3"]]
        )

        assert not preview.header_ok
        assert any("required column for quantity_available" in i for i in preview.issues)
        assert preview.rows[0].quantity is None
        [issue] = preview.rows[0].issues
        assert issue.code is IssueCode.QUANTITY_INVALID and "blank" in issue.message
        assert preview.rows[0].status is ImportRowStatus.ERROR

    def test_signature_mismatch_is_reported(self) -> None:
        expected = compute_header_signature(["UPC", "Quantity"])
        preview = map_table(self.rules(), self.HEADERS, [], expected_signature=expected)
        assert preview.signature_matches is False
        assert any("header signature does not match" in i for i in preview.issues)
        assert map_table(self.rules(), self.HEADERS, []).signature_matches is None

    def test_bad_values_become_coded_issues(self) -> None:
        preview = map_table(self.rules(), self.HEADERS, [["012345678906", "x", "two", "abc"]])
        [row] = preview.rows
        codes = [i.code for i in row.issues]
        assert codes == [IssueCode.UPC_INVALID, IssueCode.QUANTITY_INVALID, IssueCode.PRICE_INVALID]
        assert row.has_valid_checksum is False and row.normalized_upc == "00012345678906"
        assert row.status is ImportRowStatus.ERROR
        assert (
            row.primary_issue is not None and row.primary_issue.code is IssueCode.QUANTITY_INVALID
        )
        assert row.availability_status is AvailabilityStatus.UNKNOWN

    def test_negative_quantity_is_an_error(self) -> None:
        [row] = map_table(self.rules(), self.HEADERS, [["012345678905", "x", "-3", ""]]).rows
        assert [i.code for i in row.issues] == [IssueCode.QUANTITY_NEGATIVE]
        assert row.quantity is None and row.status is ImportRowStatus.ERROR

    def test_a_row_with_no_identifier_is_an_error(self) -> None:
        [row] = map_table(self.rules(), self.HEADERS, [["", "Mystery", "3", "1"]]).rows
        assert [i.code for i in row.issues] == [IssueCode.IDENTIFIER_MISSING]
        assert row.status is ImportRowStatus.ERROR

    def test_a_bad_upc_still_imports_on_the_vendor_sku(self) -> None:
        rules = ProfileRules(
            column_map=columns(("vendor_sku", "SKU"), ("upc", "UPC"), ("quantity_available", "Qty"))
        )
        [row] = map_table(rules, ["SKU", "UPC", "Qty"], [[" acm 001 ", "12345", "2"]]).rows
        assert row.vendor_sku == "acm 001" and row.normalized_vendor_sku == "ACM 001"
        assert [i.code for i in row.issues] == [IssueCode.UPC_INVALID]
        assert row.status is ImportRowStatus.WARNING

    def test_status_column_availability(self) -> None:
        """The status column is read by header text; it need not be in the map."""
        rules = ProfileRules(
            column_map=columns(("upc", "UPC"), ("quantity_available", "Qty")),
            availability_rules=AvailabilityRules(
                available_when="status_column",
                status_column="Status",
                available_values=["In Stock"],
                unavailable_values=["Discontinued"],
            ),
        )
        rows = [
            ["012345678905", "5", "in stock"],
            ["036000291452", "5", "Discontinued"],
            ["012345678905", "5", "Backorder"],
        ]
        preview = map_table(rules, ["UPC", "Qty", "Status"], rows)
        assert [r.availability_status for r in preview.rows] == [
            AvailabilityStatus.AVAILABLE,
            AvailabilityStatus.OUT_OF_STOCK,
            AvailabilityStatus.UNKNOWN,
        ]
        assert preview.issues == []
        assert [i.code for i in preview.rows[2].issues] == [IssueCode.AVAILABILITY_UNKNOWN]

        missing = map_table(rules, ["UPC", "Qty"], [["012345678905", "5"]])
        assert missing.rows[0].availability_status is AvailabilityStatus.UNKNOWN
        assert any("status column not found" in i for i in missing.issues)

    def test_upc_flags(self) -> None:
        rules = ProfileRules(
            column_map=MINIMAL,
            normalization_rules=NormalizationRules(upc_strip_non_digits=False, upc_pad_to_12=False),
        )
        preview = map_table(
            rules, ["UPC", "Quantity"], [["0-12345-67890-5", "1"], ["12345678905", "1"]]
        )
        assert "stripping is disabled" in preview.rows[0].issues[0].message
        assert "padding is disabled" in preview.rows[1].issues[0].message

    def test_fixed_case_pack_is_recorded(self) -> None:
        rules = ProfileRules(
            column_map=MINIMAL,
            quantity_semantics=QuantitySemantics(
                unit="case", case_pack_from="fixed", fixed_pack_size=12
            ),
        )
        [row] = map_table(rules, ["UPC", "Quantity"], [["012345678905", "4"]]).rows
        assert row.quantity == 4 and row.pack_size == 12  # stored as stated (B3)

    def test_index_sources(self) -> None:
        rules = ProfileRules(column_map=columns(("vendor_sku", 0), ("quantity_available", 1)))
        preview = map_table(rules, ["A", "B"], [["SKU-1", "3"]])
        assert preview.rows[0].raw == {"vendor_sku": "SKU-1", "quantity_available": "3"}
        assert preview.rows[0].quantity == 3

    def test_as_json_shape(self) -> None:
        body = map_table(self.rules(), self.HEADERS, [["012345678906", "x", "7", "1"]]).as_json()
        [row] = body["rows"]
        assert row["row_number"] == 1 and row["status"] == "WARNING"
        assert row["values"]["upc"] == "00012345678906"
        assert row["issues"] == [
            {
                "code": "UPC_INVALID",
                "severity": "WARNING",
                "field": "upc",
                "message": "'012345678906': check digit does not verify",
            }
        ]
