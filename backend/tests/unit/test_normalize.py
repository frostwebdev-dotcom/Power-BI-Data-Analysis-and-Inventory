"""Identifier normalisation (AC-7.6).

The values below are standard published examples — 012345678905 is the
canonical UPC-A test value; 4006381333931 is a real EAN-13; 04252614 is the
textbook UPC-E that expands to 042100005264.
"""

from __future__ import annotations

import pytest

from app.matching.normalize import (
    NormalizedGtin,
    expand_upc_e,
    gtin_check_digit,
    normalize_gtin,
)


class TestCheckDigit:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [("01234567890", 5), ("400638133393", 1), ("04210000526", 4), ("0000000000000", 0)],
    )
    def test_known_values(self, body: str, expected: int) -> None:
        assert gtin_check_digit(body) == expected


class TestValidValues:
    def test_a_valid_upc_a(self) -> None:
        result = normalize_gtin("012345678905")

        assert result == NormalizedGtin(
            raw="012345678905",
            digits="012345678905",
            kind="UPC-A",
            canonical="00012345678905",
            valid_checksum=True,
        )
        assert result.usable

    def test_a_valid_ean_13(self) -> None:
        result = normalize_gtin("4006381333931")

        assert result.kind == "EAN-13"
        assert result.canonical == "04006381333931"
        assert result.usable

    def test_a_gtin_14_is_already_canonical(self) -> None:
        result = normalize_gtin("00012345678905")

        assert result.kind == "GTIN-14"
        assert result.canonical == "00012345678905"
        assert result.usable

    def test_an_ean_8(self) -> None:
        # 9638507 → check 4 (EAN-8 example from GS1).
        result = normalize_gtin("96385074")

        assert result.kind == "EAN-8"
        assert result.canonical == "00000096385074"
        assert result.usable

    def test_an_11_digit_upc_missing_its_leading_zero(self) -> None:
        """A spreadsheet stored it as a number and dropped the zero."""
        result = normalize_gtin("12345678905")

        assert result.kind == "UPC-A"
        assert result.digits == "012345678905"
        assert result.canonical == "00012345678905"
        assert result.usable

    @pytest.mark.parametrize(
        "raw",
        [
            " 012345678905 ",
            "0-12345-67890-5",
            "012345 678905",
            "012_345_678_905",
            "012.345.678.905",
        ],
    )
    def test_whitespace_hyphens_and_separators_are_stripped(self, raw: str) -> None:
        assert normalize_gtin(raw).canonical == "00012345678905"

    def test_values_differing_only_in_leading_zeros_compare_equal(self) -> None:
        forms = ("12345678905", "012345678905", "0012345678905", "00012345678905")

        assert len({normalize_gtin(form).canonical for form in forms}) == 1


class TestUpcE:
    def test_textbook_expansion(self) -> None:
        assert expand_upc_e("04252614") == "042100005264"

    def test_six_and_seven_digit_forms(self) -> None:
        assert expand_upc_e("425261") == "042100005264"
        assert expand_upc_e("0425261") == "042100005264"

    @pytest.mark.parametrize(
        ("upc_e", "upc_a"),
        [
            ("01234500", "012000003455"),  # last digit 0/1/2 pattern (computed check)
            ("01234530", "01230000045x"),
            ("01234540", "01234000005x"),
            ("01234550", "01234500005x"),
        ],
    )
    def test_every_pattern_expands_to_twelve_digits(self, upc_e: str, upc_a: str) -> None:
        expanded = expand_upc_e(upc_e[:7])
        assert expanded is not None
        assert expanded[:11] == upc_a[:11]
        assert len(expanded) == 12

    def test_number_system_other_than_zero_is_not_upc_e(self) -> None:
        assert expand_upc_e("1425261") is None

    def test_normalize_expands_upc_e_and_verifies_the_check_digit(self) -> None:
        result = normalize_gtin("04252614")

        assert result.kind == "UPC-E"
        assert result.digits == "042100005264"
        assert result.canonical == "00042100005264"
        assert result.usable

    @pytest.mark.parametrize("raw", ["425261", "0425261"])
    def test_a_upc_e_without_its_check_digit_is_not_usable(self, raw: str) -> None:
        """Expansion has to compute the check digit, so it verifies by
        construction and proves nothing: such a value must not auto-match."""
        result = normalize_gtin(raw)

        assert result.kind == "UPC-E"
        assert result.canonical == "00042100005264"  # kept for display and search
        assert result.valid_checksum is True
        assert result.check_digit_supplied is False
        assert result.problem is not None and "no check digit" in result.problem
        assert not result.usable

    def test_an_arbitrary_six_digit_code_cannot_become_a_usable_upc(self) -> None:
        # A vendor item number in a UPC column used to normalise to a "valid" GTIN.
        assert not normalize_gtin("123456").usable

    def test_a_wrong_upc_e_check_digit_is_reported_not_corrected(self) -> None:
        result = normalize_gtin("04252615")

        assert result.kind == "UPC-E"
        assert result.valid_checksum is False
        assert not result.usable


class TestInvalidValues:
    def test_a_bad_check_digit_is_not_usable(self) -> None:
        result = normalize_gtin("012345678906")

        assert result.canonical == "00012345678906"  # still comparable, for diagnostics
        assert result.valid_checksum is False
        assert result.problem == "check digit does not verify"
        assert not result.usable

    @pytest.mark.parametrize("raw", ["", "   ", None, "-"])
    def test_empty_is_unusable(self, raw: str | None) -> None:
        result = normalize_gtin(raw)

        assert result.canonical is None
        assert result.problem == "empty"

    def test_letters_are_unusable(self) -> None:
        result = normalize_gtin("B000TEST01")

        assert result.canonical is None
        assert result.problem == "contains non-digit characters"

    @pytest.mark.parametrize("raw", ["123", "1234567890", "123456789012345"])
    def test_unsupported_lengths_are_unusable(self, raw: str) -> None:
        result = normalize_gtin(raw)

        assert result.canonical is None
        assert result.problem is not None and "length" in result.problem

    def test_scientific_notation_from_a_spreadsheet_is_unusable(self) -> None:
        """1.23457E+11 is what a spreadsheet does to a UPC; it must not match."""
        result = normalize_gtin("1.23457E+11")

        assert result.canonical is None
