"""Read a vendor file into a header row and rows of strings.

Every cell comes out as ``str``. There is no type inference anywhere in this
module, because inference is how a UPC of ``012345678905`` becomes
``12345678905`` or ``1.23457E+11`` (ADR 0008, risk R2). For XLSX, openpyxl
hands back typed values; they are rendered here with the one rule that
matters — an integer-valued number is rendered without a trailing ``.0`` —
and nothing else is interpreted.

Readers are bounded: they read at most ``max_rows`` data rows, so the
validate endpoint can preview a 200 MB file without loading it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from app.models.enums import FileFormat

#: Encodings tried, in order, when the profile does not name one.
DEFAULT_ENCODINGS: Final = ("utf-8-sig", "cp1252", "latin-1")


class UnreadableFile(Exception):  # noqa: N818 — a user-facing outcome
    """The file could not be read as the profile describes."""


@dataclass(slots=True)
class ParsedTable:
    headers: list[str]
    rows: list[list[str]]
    #: Data rows actually present after the header, capped at ``max_rows``.
    truncated: bool = False
    encoding: str | None = None
    sheet: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReadOptions:
    file_format: FileFormat
    encoding: str | None = None
    delimiter: str | None = None
    quote_char: str | None = None
    header_row_index: int = 0
    skip_rows: int = 0
    sheet_name: str | None = None
    sheet_index: int | None = None


def read_table(content: bytes, options: ReadOptions, *, max_rows: int = 20) -> ParsedTable:
    if options.file_format is FileFormat.CSV:
        return _read_csv(content, options, max_rows=max_rows)
    return _read_xlsx(content, options, max_rows=max_rows)


# --- CSV -----------------------------------------------------------------------------


def _decode(content: bytes, encoding: str | None) -> tuple[str, str]:
    candidates = (encoding,) if encoding else DEFAULT_ENCODINGS
    for name in candidates:
        assert name is not None
        try:
            return content.decode(name), name
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnreadableFile(f"the file is not decodable as {', '.join(str(c) for c in candidates)}")


def _read_csv(content: bytes, options: ReadOptions, *, max_rows: int) -> ParsedTable:
    text, encoding = _decode(content, options.encoding)
    delimiter = options.delimiter or _sniff_delimiter(text)
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=delimiter,
        quotechar=options.quote_char or '"',
    )
    return _assemble(reader, options, max_rows=max_rows, encoding=encoding)


def _sniff_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# --- XLSX ----------------------------------------------------------------------------


def _read_xlsx(content: bytes, options: ReadOptions, *, max_rows: int) -> ParsedTable:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a mix of zipfile/xml/KeyError types
        raise UnreadableFile(f"not a readable XLSX workbook: {type(exc).__name__}") from exc

    try:
        if options.sheet_name is not None:
            if options.sheet_name not in workbook.sheetnames:
                raise UnreadableFile(
                    f"sheet {options.sheet_name!r} not found; sheets: {workbook.sheetnames}"
                )
            sheet = workbook[options.sheet_name]
        else:
            index = options.sheet_index or 0
            if index >= len(workbook.sheetnames):
                raise UnreadableFile(
                    f"sheet index {index} out of range; the workbook has "
                    f"{len(workbook.sheetnames)} sheet(s)"
                )
            sheet = workbook[workbook.sheetnames[index]]

        rows = ([cell_text(value) for value in row] for row in sheet.iter_rows(values_only=True))
        table = _assemble(rows, options, max_rows=max_rows, encoding=None)
        table.sheet = sheet.title
        return table
    finally:
        workbook.close()


def cell_text(value: Any) -> str:
    """Render one XLSX cell as text without inventing precision or losing it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # 12345678905.0 is an integer that happened to be stored as a float.
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


# --- shared assembly ------------------------------------------------------------------


def _assemble(
    rows: Any, options: ReadOptions, *, max_rows: int, encoding: str | None
) -> ParsedTable:
    iterator = iter(rows)
    notes: list[str] = []

    # Skip leading rows, then take the header at header_row_index (relative to
    # the rows that remain).
    for _ in range(options.skip_rows):
        next(iterator, None)
    header: list[str] | None = None
    for _ in range(options.header_row_index + 1):
        header = next(iterator, None)
    if header is None:
        raise UnreadableFile("the file has no header row where the profile expects one")
    headers = [str(cell).strip() for cell in header]
    while headers and headers[-1] == "":
        headers.pop()  # trailing empty header cells are padding, not columns
    if not any(headers):
        raise UnreadableFile("the header row is empty")

    data: list[list[str]] = []
    truncated = False
    for raw in iterator:
        cells = [str(cell) for cell in raw]
        if not any(cell.strip() for cell in cells):
            continue  # blank lines are not rows
        if len(data) >= max_rows:
            truncated = True
            break
        # Pad or trim to the header width so every row is addressable by column.
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        elif len(cells) > len(headers):
            notes.append(f"a row had {len(cells)} cells for {len(headers)} headers; extra ignored")
            cells = cells[: len(headers)]
        data.append(cells)

    return ParsedTable(
        headers=headers, rows=data, truncated=truncated, encoding=encoding, notes=notes[:5]
    )
