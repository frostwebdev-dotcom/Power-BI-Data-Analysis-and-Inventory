"""Read a vendor file into a header row and a stream of rows of strings.

Every cell comes out as ``str``. There is no type inference anywhere in this
module, because inference is how a UPC of ``012345678905`` becomes
``12345678905`` or ``1.23457E+11`` (ADR 0008, risk R2). For XLSX, openpyxl
hands back typed values; they are rendered here with the one rule that
matters — an integer-valued number is rendered without a trailing ``.0`` —
and nothing else is interpreted.

Rows are streamed. :func:`open_rows` yields one :class:`SourceRow` at a
time — the file's own row number, the cells, and whether the row is blank —
so a 200 MB workbook is read in read-only mode and a 20,000-row import
holds one chunk in memory, never the file. :func:`read_table` is the bounded
convenience the validate endpoint uses for its 20-row preview.

**Row numbers are the file's own** (AC-5.1): for CSV the physical line the
record starts on, for XLSX the sheet row. An error listing that says
"row 1,204" points at row 1,204 in the retained file.

**Encoding.** The profile's encoding, if named, is used strictly — the
operator said so. Otherwise: a byte-order mark decides; then strict UTF-8;
then cp1252, which is what nearly every non-UTF-8 vendor file from a Windows
export is; then charset-normalizer for the rest (UTF-16 without a BOM,
Mac Roman, anything with the five bytes cp1252 leaves undefined); then
latin-1, which decodes anything. charset-normalizer is not consulted first
because on a short Western sample it will happily answer ``cp1250``, and a
wrong label on ``import_files.detected_encoding`` is worse than none.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from app.imports.profile_rules import normalize_header
from app.models.enums import FileFormat

#: Encodings tried in order, after the BOM sniff and before charset-normalizer.
PREFERRED_ENCODINGS: Final = ("utf-8", "cp1252")
LAST_RESORT_ENCODING: Final = "latin-1"

_BOMS: Final = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


class UnreadableFile(Exception):  # noqa: N818 — a user-facing outcome
    """The file could not be read as the profile describes."""


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


@dataclass(slots=True)
class SourceRow:
    """One data row as the file has it."""

    #: The file's own numbering: CSV physical line, XLSX sheet row (1-based).
    row_number: int
    #: Padded or trimmed to the header width; every value is a string.
    cells: list[str]
    #: Every cell empty or whitespace. Yielded so the importer can record it
    #: as SKIPPED with its row number, rather than silently dropping it.
    is_blank: bool = False

    def as_mapping(self, normalized_headers: list[str]) -> dict[str, str]:
        """``{normalised header: cell}`` — the shape the extractor reads."""
        return dict(zip(normalized_headers, self.cells, strict=True))


@dataclass(slots=True)
class RowStream:
    headers: list[str]
    normalized_headers: list[str]
    rows: Iterator[SourceRow]
    #: The header row's own number, so the first data row is at least this + 1.
    header_row_number: int
    encoding: str | None = None
    sheet: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedTable:
    headers: list[str]
    rows: list[list[str]]
    #: Data rows actually present after the header, capped at ``max_rows``.
    truncated: bool = False
    encoding: str | None = None
    sheet: str | None = None
    notes: list[str] = field(default_factory=list)


# --- public entry points -----------------------------------------------------------------


def open_rows(content: bytes, options: ReadOptions) -> RowStream:
    """Headers plus a lazy iterator of data rows. Blank rows are yielded flagged."""
    if options.file_format is FileFormat.CSV:
        return _open_csv(content, options)
    return _open_xlsx(content, options)


def read_table(content: bytes, options: ReadOptions, *, max_rows: int = 20) -> ParsedTable:
    """The first ``max_rows`` non-blank rows, for a preview."""
    stream = open_rows(content, options)
    data: list[list[str]] = []
    truncated = False
    for row in stream.rows:
        if row.is_blank:
            continue
        if len(data) >= max_rows:
            truncated = True
            break
        data.append(row.cells)
    return ParsedTable(
        headers=stream.headers,
        rows=data,
        truncated=truncated,
        encoding=stream.encoding,
        sheet=stream.sheet,
        notes=stream.notes,
    )


def detect_encoding(content: bytes, declared: str | None = None) -> str | None:
    """The encoding a CSV upload decodes as, for ``import_files.detected_encoding``
    (AC-5.8). ``None`` when nothing decodes it."""
    try:
        return _decode(content, declared)[1]
    except UnreadableFile:
        return None


# --- CSV -----------------------------------------------------------------------------


def _decode(content: bytes, encoding: str | None) -> tuple[str, str]:
    if encoding:
        try:
            return content.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError) as exc:
            raise UnreadableFile(f"the file is not decodable as {encoding!r}: {exc}") from exc

    for bom, name in _BOMS:
        if content.startswith(bom):
            try:
                return content.decode(name), name
            except UnicodeDecodeError:
                break
    for name in PREFERRED_ENCODINGS:
        try:
            return content.decode(name), name
        except UnicodeDecodeError:
            continue
    guessed = _guess_encoding(content)
    if guessed is not None:
        try:
            return content.decode(guessed), guessed
        except (UnicodeDecodeError, LookupError):
            pass
    return content.decode(LAST_RESORT_ENCODING), LAST_RESORT_ENCODING


def _guess_encoding(content: bytes) -> str | None:
    from charset_normalizer import from_bytes

    best = from_bytes(content).best()
    return best.encoding if best is not None else None


def _open_csv(content: bytes, options: ReadOptions) -> RowStream:
    text, encoding = _decode(content, options.encoding)
    delimiter = options.delimiter or _sniff_delimiter(text)
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=delimiter,
        quotechar=options.quote_char or '"',
    )

    def numbered() -> Iterator[tuple[int, list[str]]]:
        # A record may span lines when quoted; it is numbered by the line it
        # starts on, which is the line a person would look for.
        last_line = 0
        for record in reader:
            yield last_line + 1, record
            last_line = reader.line_num

    return _assemble(numbered(), options, encoding=encoding)


def _sniff_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# --- XLSX ----------------------------------------------------------------------------


def _open_xlsx(content: bytes, options: ReadOptions) -> RowStream:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a mix of zipfile/xml/KeyError types
        raise UnreadableFile(f"not a readable XLSX workbook: {type(exc).__name__}") from exc

    if options.sheet_name is not None:
        if options.sheet_name not in workbook.sheetnames:
            workbook.close()
            raise UnreadableFile(
                f"sheet {options.sheet_name!r} not found; sheets: {workbook.sheetnames}"
            )
        sheet = workbook[options.sheet_name]
    else:
        index = options.sheet_index or 0
        if index >= len(workbook.sheetnames):
            workbook.close()
            raise UnreadableFile(
                f"sheet index {index} out of range; the workbook has "
                f"{len(workbook.sheetnames)} sheet(s)"
            )
        sheet = workbook[workbook.sheetnames[index]]

    def numbered() -> Iterator[tuple[int, list[str]]]:
        try:
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                yield number, [cell_text(value) for value in row]
        finally:
            workbook.close()

    try:
        stream = _assemble(numbered(), options, encoding=None)
    except UnreadableFile:
        workbook.close()
        raise
    stream.sheet = sheet.title
    return stream


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
    numbered: Iterator[tuple[int, list[str]]], options: ReadOptions, *, encoding: str | None
) -> RowStream:
    # Skip leading rows, then take the header at header_row_index (relative to
    # the rows that remain).
    for _ in range(options.skip_rows):
        next(numbered, None)
    header: tuple[int, list[str]] | None = None
    for _ in range(options.header_row_index + 1):
        header = next(numbered, None)
    if header is None:
        raise UnreadableFile("the file has no header row where the profile expects one")
    header_number, header_cells = header
    headers = [str(cell).strip() for cell in header_cells]
    while headers and headers[-1] == "":
        headers.pop()  # trailing empty header cells are padding, not columns
    if not any(headers):
        raise UnreadableFile("the header row is empty")

    notes: list[str] = []
    width = len(headers)

    def rows() -> Iterator[SourceRow]:
        for number, raw in numbered:
            cells = [str(cell) for cell in raw]
            if not any(cell.strip() for cell in cells):
                yield SourceRow(row_number=number, cells=[""] * width, is_blank=True)
                continue
            # Pad or trim to the header width so every row is addressable by column.
            if len(cells) < width:
                cells.extend([""] * (width - len(cells)))
            elif len(cells) > width:
                if len(notes) < 5:
                    notes.append(
                        f"row {number} had {len(cells)} cells for {width} headers; extra ignored"
                    )
                cells = cells[:width]
            yield SourceRow(row_number=number, cells=cells)

    return RowStream(
        headers=headers,
        normalized_headers=[normalize_header(h) for h in headers],
        rows=rows(),
        header_row_number=header_number,
        encoding=encoding,
        notes=notes,
    )
