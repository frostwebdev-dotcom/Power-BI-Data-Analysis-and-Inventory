"""The streaming readers: row numbers, blank rows, encodings (AC-5.1, 5.8)."""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.imports.readers import ReadOptions, SourceRow, UnreadableFile, detect_encoding, open_rows
from app.models.enums import FileFormat

CSV = ReadOptions(file_format=FileFormat.CSV)
XLSX = ReadOptions(file_format=FileFormat.XLSX)


def xlsx(rows: list[list[Any]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestStreaming:
    def test_rows_carry_the_files_own_line_numbers(self) -> None:
        content = b"UPC,Qty\r\n1,2\r\n\r\n3,4\r\n"

        stream = open_rows(content, CSV)
        rows = list(stream.rows)

        assert stream.headers == ["UPC", "Qty"]
        assert stream.normalized_headers == ["upc", "qty"]
        assert stream.header_row_number == 1
        assert [(r.row_number, r.cells, r.is_blank) for r in rows] == [
            (2, ["1", "2"], False),
            (3, ["", ""], True),
            (4, ["3", "4"], False),
        ]
        assert rows[0].as_mapping(stream.normalized_headers) == {"upc": "1", "qty": "2"}

    def test_a_quoted_record_spanning_lines_is_numbered_by_its_first_line(self) -> None:
        content = b'UPC,Desc\n1,"two\nlines"\n2,x\n'

        rows = list(open_rows(content, CSV).rows)

        assert [(r.row_number, r.cells) for r in rows] == [
            (2, ["1", "two\nlines"]),
            (4, ["2", "x"]),
        ]

    def test_skip_rows_and_header_index_shift_the_numbering(self) -> None:
        content = b"Report\n\nUPC,Qty\n1,2\n"

        stream = open_rows(content, ReadOptions(file_format=FileFormat.CSV, skip_rows=2))

        assert stream.header_row_number == 3
        assert [r.row_number for r in stream.rows] == [4]

    def test_xlsx_rows_are_numbered_by_sheet_row(self) -> None:
        content = xlsx([["UPC", "Qty"], [12345678905, 7], [None, None], ["x", 1]])

        stream = open_rows(content, XLSX)
        rows = list(stream.rows)

        assert stream.sheet == "Sheet"
        assert [(r.row_number, r.cells, r.is_blank) for r in rows] == [
            (2, ["12345678905", "7"], False),
            (3, ["", ""], True),
            (4, ["x", "1"], False),
        ]

    def test_the_stream_is_lazy(self) -> None:
        """Rows are produced on demand; a caller that stops early reads no further."""
        content = b"UPC,Qty\n" + b"".join(f"{i},1\n".encode() for i in range(10_000))

        stream = open_rows(content, CSV)
        first = next(stream.rows)

        assert isinstance(first, SourceRow) and first.row_number == 2

    def test_wide_rows_are_trimmed_and_noted(self) -> None:
        stream = open_rows(b"UPC,Qty\n1,2,3\n", CSV)
        rows = list(stream.rows)
        assert rows[0].cells == ["1", "2"]
        assert stream.notes == ["row 2 had 3 cells for 2 headers; extra ignored"]


class TestEncodings:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"UPC,Qty\n1,2\n", "utf-8"),
            (b"\xef\xbb\xbfUPC,Qty\n1,2\n", "utf-8-sig"),
            ("UPC,Desc\n1,Caf\xe9\n".encode("cp1252"), "cp1252"),
            ("UPC,Desc\n1,€9\n".encode(), "utf-8"),
            ("UPC,Desc\n1,Caf\xe9\n".encode("utf-16"), "utf-16"),
        ],
    )
    def test_detection_order(self, content: bytes, expected: str) -> None:
        assert detect_encoding(content) == expected
        assert open_rows(content, CSV).encoding == expected

    def test_a_declared_encoding_is_used_strictly(self) -> None:
        content = "UPC,Desc\n1,Caf\xe9\n".encode("cp1252")

        assert (
            open_rows(content, ReadOptions(file_format=FileFormat.CSV, encoding="latin-1")).encoding
            == "latin-1"
        )
        with pytest.raises(UnreadableFile, match="not decodable as 'utf-8'"):
            open_rows(content, ReadOptions(file_format=FileFormat.CSV, encoding="utf-8"))
        assert detect_encoding(content, "utf-8") is None

    def test_charset_normalizer_catches_what_the_preferred_list_cannot(self) -> None:
        """A byte cp1252 leaves undefined (0x81) forces the guess; whatever it
        answers must decode, and the last resort is latin-1."""
        content = b"UPC,Desc\n1,\x81odd\n"

        encoding = detect_encoding(content)

        assert encoding is not None and encoding not in ("utf-8", "cp1252")
        assert open_rows(content, CSV).headers == ["UPC", "Desc"]
