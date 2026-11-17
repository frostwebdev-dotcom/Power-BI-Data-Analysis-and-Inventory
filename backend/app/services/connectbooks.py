"""Bounded, exact-decimal, non-persistent ConnectBooks XLSX preview (ADR 0024)."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Final
from xml.etree import ElementTree as ET

from app.imports.profile_rules import normalize_header
from app.schemas.connectbooks import (
    ConnectBooksControl,
    ConnectBooksField,
    ConnectBooksPreviewRow,
    ConnectBooksXlsxPreview,
    ConnectBooksXlsxProfile,
)
from app.schemas.profitability import ProfitabilityIssue
from app.services.profitability import ProfitabilityPreviewError

MAX_XLSX_BYTES: Final = 8 * 1024 * 1024
MAX_EXPANDED_BYTES: Final = 64 * 1024 * 1024
MAX_ROWS: Final = 10_000
MAX_COLUMNS: Final = 256
MAX_CELLS: Final = 1_000_000
N: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
TOLERANCE: Final = Decimal("0.01")
HEADERS: Final[dict[ConnectBooksField, str]] = {
    "sales_qty": "Sales Qty",
    "refund_qty": "Refund Qty",
    "total_units": "Total Units",
    "sales": "Sales",
    "refunds": "Refunds",
    "adjustments": "Adjustments",
    "net_sales": "Net Sales",
    "fees": "Fees",
    "ad_spend": "Ad Spend",
    "cost": "Cost",
    "profit": "Profit",
}
IDENTIFIERS: Final = ("Item", "FNSKU", "Item ID", "Title", "Store")
RATIOS: Final = ("ROI", "Profit Margin %")
QUANTITIES: Final = frozenset(("sales_qty", "refund_qty", "total_units"))


@dataclass(frozen=True)
class _Cell:
    value: str = ""
    kind: str = "blank"


def _issue(code: str, field: str, severity: str = "REVIEW") -> ProfitabilityIssue:
    return ProfitabilityIssue.model_validate({"code": code, "field": field, "severity": severity})


def _xml(archive: zipfile.ZipFile, path: str) -> ET.Element:
    data = archive.read(path)
    if b"\x00" in data:
        raise ProfitabilityPreviewError("UNSUPPORTED_XML_ENCODING")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ProfitabilityPreviewError("UNSUPPORTED_XML_DECLARATION")
    return ET.fromstring(data)


def _text(node: ET.Element) -> str:
    # Only text/rich-text runs; phonetic annotations are not part of identifiers.
    return "".join(t.text or "" for t in [*node.findall(f"{N}t"), *node.findall(f"{N}r/{N}t")])


def _column(reference: str, row_number: int) -> int:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", reference)
    if match is None or int(match[2]) != row_number:
        raise ProfitabilityPreviewError("INVALID_CELL_REFERENCE")
    index = 0
    for char in match[1]:
        index = index * 26 + ord(char) - ord("A") + 1
    if index > MAX_COLUMNS:
        raise ProfitabilityPreviewError("COLUMN_LIMIT_EXCEEDED")
    return index


def _letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _read_sheet(content: bytes, sheet_name: str) -> list[tuple[int, dict[int, _Cell]]]:
    if len(content) > MAX_XLSX_BYTES:
        raise ProfitabilityPreviewError("FILE_SIZE_LIMIT_EXCEEDED")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1024 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise ProfitabilityPreviewError("ARCHIVE_EXPANSION_LIMIT_EXCEEDED")
            if len({e.filename for e in entries}) != len(entries):
                raise ProfitabilityPreviewError("DUPLICATE_ARCHIVE_ENTRY")
            if any(e.flag_bits & 1 for e in entries):
                raise ProfitabilityPreviewError("ENCRYPTED_WORKBOOK_NOT_SUPPORTED")
            workbook = _xml(archive, "xl/workbook.xml")
            sheets = [
                s for s in workbook.findall(f"{N}sheets/{N}sheet") if s.get("name") == sheet_name
            ]
            if len(sheets) != 1:
                raise ProfitabilityPreviewError("SHEET_NOT_FOUND_OR_AMBIGUOUS")
            relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
            links = [r for r in relationships if r.get("Id") == sheets[0].get(f"{REL}id")]
            if (
                len(links) != 1
                or links[0].get("TargetMode") == "External"
                or not links[0].get("Type", "").endswith("/worksheet")
            ):
                raise ProfitabilityPreviewError("UNSUPPORTED_WORKSHEET_RELATIONSHIP")
            path = posixpath.normpath(posixpath.join("xl", links[0].get("Target", "")))
            path = path.lstrip("/")
            if not path.startswith("xl/worksheets/") or not path.endswith(".xml"):
                raise ProfitabilityPreviewError("UNSUPPORTED_WORKSHEET_RELATIONSHIP")
            shared = (
                [_text(si) for si in _xml(archive, "xl/sharedStrings.xml").findall(f"{N}si")]
                if "xl/sharedStrings.xml" in archive.namelist()
                else []
            )
            sheet = _xml(archive, path)
            rows: list[tuple[int, dict[int, _Cell]]] = []
            previous, cell_count = 0, 0
            for row in sheet.findall(f"{N}sheetData/{N}row"):
                number = int(row.get("r", "0"))
                if number <= previous:
                    raise ProfitabilityPreviewError("INVALID_ROW_SEQUENCE")
                if number > MAX_ROWS + 2 or len(rows) >= MAX_ROWS + 2:
                    raise ProfitabilityPreviewError("ROW_LIMIT_EXCEEDED")
                previous = number
                cells: dict[int, _Cell] = {}
                for cell in row.findall(f"{N}c"):
                    cell_count += 1
                    if cell_count > MAX_CELLS:
                        raise ProfitabilityPreviewError("CELL_LIMIT_EXCEEDED")
                    index = _column(cell.get("r", ""), number)
                    if index in cells:
                        raise ProfitabilityPreviewError("DUPLICATE_CELL_REFERENCE")
                    if cell.find(f"{N}f") is not None:
                        raise ProfitabilityPreviewError("FORMULAS_NOT_SUPPORTED")
                    value = cell.findtext(f"{N}v", "")
                    kind = cell.get("t", "n")
                    if kind == "s":
                        shared_index = int(value)
                        if shared_index < 0 or shared_index >= len(shared):
                            raise ProfitabilityPreviewError("INVALID_SHARED_STRING")
                        value, kind = shared[shared_index], "text"
                    elif kind == "inlineStr":
                        inline = cell.find(f"{N}is")
                        value, kind = _text(inline) if inline is not None else "", "text"
                    elif kind == "str":
                        kind = "text"
                    cells[index] = _Cell(value, kind if value else "blank")
                rows.append((number, cells))
            return rows
    except ProfitabilityPreviewError:
        raise
    except (
        zipfile.BadZipFile,
        KeyError,
        ET.ParseError,
        ValueError,
        OSError,
        NotImplementedError,
        RuntimeError,
        zlib.error,
    ):
        raise ProfitabilityPreviewError("UNREADABLE_XLSX") from None


def _number(cell: _Cell, *, summary: bool = False) -> Decimal:
    value = cell.value.strip()
    if summary:
        if cell.kind not in {"n", "text"}:
            raise ValueError("invalid control-total cell type")
        value = re.sub(r"^(-?)\$", r"\1", value)
        if not re.fullmatch(r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,16})?", value):
            raise ValueError("invalid control total")
        value = value.replace(",", "")
    elif cell.kind != "n" or not re.fullmatch(
        r"-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,2})?", value
    ):
        raise ValueError("financial detail must be a numeric Excel cell")
    if len(value) > 64:
        raise ValueError("numeric value exceeds precision limit")
    amount = Decimal(value)
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount.adjusted() >= 18
        or not isinstance(exponent, int)
        or exponent < -16
    ):
        raise ValueError("numeric value exceeds precision limit")
    return amount


def _parse_amounts(
    cells: dict[int, _Cell], indexes: dict[str, int], *, summary: bool = False
) -> tuple[dict[ConnectBooksField, Decimal | None], list[ProfitabilityIssue]]:
    amounts: dict[ConnectBooksField, Decimal | None] = {}
    issues: list[ProfitabilityIssue] = []
    for field, header in HEADERS.items():
        cell = cells.get(indexes[header], _Cell())
        if not cell.value.strip():
            amounts[field] = None
            issues.append(_issue("MISSING_AMOUNT", field))
            continue
        try:
            amount = _number(cell, summary=summary)
            if field in QUANTITIES and amount != amount.to_integral_value():
                raise ValueError("quantities must be whole seller-SKU units")
            if (field == "sales_qty" and amount < 0) or (field == "refund_qty" and amount > 0):
                raise ValueError("unexpected source quantity sign")
            amounts[field] = amount
        except (ValueError, InvalidOperation):
            amounts[field] = None
            issues.append(_issue("INVALID_AMOUNT", field, "ERROR"))
    return amounts, issues


def _parse_row(
    number: int, cells: dict[int, _Cell], indexes: dict[str, int], profile: ConnectBooksXlsxProfile
) -> ConnectBooksPreviewRow:
    def text(header: str) -> str | None:
        value = cells.get(indexes[header], _Cell()).value
        return value if value.strip() else None

    item, store = text("Item"), text("Store")
    unallocated = item is not None and item.casefold() == "unitemized"
    amounts, issues = _parse_amounts(cells, indexes)
    if item is None:
        issues.append(_issue("MISSING_SELLER_SKU", "Item", "ERROR"))
    elif cells[indexes["Item"]].kind != "text":
        issues.append(_issue("NON_TEXT_SELLER_SKU", "Item", "ERROR"))
    elif item != item.strip():
        issues.append(_issue("SELLER_SKU_SURROUNDING_WHITESPACE", "Item", "ERROR"))
    if store != profile.source_store_name:
        issues.append(_issue("STORE_MISMATCH", "Store", "ERROR"))
    if unallocated:
        issues.append(_issue("UNALLOCATED_FINANCIAL_ROW", "Item"))
    if amounts["net_sales"] is not None and amounts["net_sales"] > 0 and amounts["cost"] == 0:
        issues.append(_issue("ZERO_COST_WITH_POSITIVE_SALES", "cost"))
    if not unallocated and text("Item ID") is None:
        issues.append(_issue("MISSING_ASIN", "Item ID"))
    profit, margin, roi = None, None, None
    needed: tuple[ConnectBooksField, ...] = ("net_sales", "fees", "cost", "ad_spend")
    if all(amounts[f] is not None for f in needed):
        revenue, fees, cost, ads = (amounts[f] for f in needed)
        assert revenue is not None and fees is not None and cost is not None and ads is not None
        profit = revenue + fees - cost - ads
        margin = profit / revenue * 100 if revenue > 0 else None
        roi = profit / cost * 100 if cost > 0 else None
        if amounts["profit"] is not None and abs(profit - amounts["profit"]) > TOLERANCE:
            issues.append(_issue("PROFIT_RECONCILIATION_MISMATCH", "profit", "ERROR"))
    net_fields: tuple[ConnectBooksField, ...] = ("sales", "refunds", "adjustments", "net_sales")
    if all(amounts[f] is not None for f in net_fields):
        sales, refunds, adjustments, net = (amounts[f] for f in net_fields)
        assert (
            sales is not None
            and refunds is not None
            and adjustments is not None
            and net is not None
        )
        if abs(sales + refunds + adjustments - net) > TOLERANCE:
            issues.append(_issue("NET_SALES_RECONCILIATION_MISMATCH", "net_sales", "ERROR"))
    ratios: list[Decimal | None] = []
    for header in RATIOS:
        try:
            ratios.append(_number(cells.get(indexes[header], _Cell())) * 100)
        except (ValueError, InvalidOperation):
            ratios.append(None)
            issues.append(_issue("MISSING_OR_INVALID_REPORTED_RATIO", header))
    invalid = any(issue.severity == "ERROR" for issue in issues)
    if invalid:
        profit, margin, roi = None, None, None
    return ConnectBooksPreviewRow(
        row_number=number,
        source_item=item,
        seller_sku=None if unallocated else item,
        asin=text("Item ID"),
        source_store=store,
        row_kind="UNALLOCATED" if unallocated else "SKU",
        identity_status="UNALLOCATED" if unallocated else "UNRESOLVED",
        status="INVALID" if invalid else "REVIEW_REQUIRED" if issues else "RECONCILED",
        amounts=amounts,
        computed_profit=profit,
        computed_margin_percent=margin,
        computed_roi_percent=roi,
        reported_roi_percent=ratios[0],
        reported_margin_percent=ratios[1],
        issues=tuple(issues),
    )


def preview_connectbooks_xlsx(
    content: bytes, profile: ConnectBooksXlsxProfile
) -> ConnectBooksXlsxPreview:
    """Reconcile signed source data, without resolving identities or making purchases."""
    with localcontext(Context(prec=38, rounding=ROUND_HALF_EVEN)):
        return _preview(content, profile)


def _preview(content: bytes, profile: ConnectBooksXlsxProfile) -> ConnectBooksXlsxPreview:
    source = _read_sheet(content, profile.sheet_name)
    if not source or source[0][0] != 1:
        raise ProfitabilityPreviewError("HEADER_ROW_REQUIRED")
    headers = source[0][1]
    indexes: dict[str, int] = {}
    for header in (*IDENTIFIERS, *HEADERS.values(), *RATIOS):
        found = [
            i
            for i, cell in headers.items()
            if cell.kind == "text" and normalize_header(cell.value) == normalize_header(header)
        ]
        if len(found) != 1:
            raise ProfitabilityPreviewError("MAPPED_HEADER_MISSING_OR_AMBIGUOUS")
        indexes[header] = found[0]
    rows: list[ConnectBooksPreviewRow] = []
    summary: dict[ConnectBooksField, Decimal | None] | None = None
    summary_number, blanks = 0, 0
    issues: list[ProfitabilityIssue] = []
    for number, cells in source[1:]:
        if not any(cell.value.strip() for cell in cells.values()):
            blanks += 1
            continue
        if summary is not None:
            raise ProfitabilityPreviewError("SUMMARY_MUST_BE_LAST_NONBLANK_ROW")
        if any(i not in headers and cell.value.strip() for i, cell in cells.items()):
            raise ProfitabilityPreviewError("DATA_OUTSIDE_HEADERS")
        if not any(cells.get(indexes[h], _Cell()).value.strip() for h in IDENTIFIERS):
            summary, summary_issues = _parse_amounts(cells, indexes, summary=True)
            issues.extend(_issue(f"SUMMARY_{i.code}", i.field, i.severity) for i in summary_issues)
            summary_number = number
        else:
            rows.append(_parse_row(number, cells, indexes, profile))
    if not rows:
        raise ProfitabilityPreviewError("NO_DATA_ROWS")
    if summary is None:
        raise ProfitabilityPreviewError("SUMMARY_ROW_REQUIRED")
    counts = Counter(row.seller_sku for row in rows if row.seller_sku is not None)
    results: list[ConnectBooksPreviewRow] = []
    for row in rows:
        if row.seller_sku is not None and counts[row.seller_sku] > 1:
            row = row.model_copy(
                update={
                    "status": "INVALID",
                    "computed_profit": None,
                    "computed_margin_percent": None,
                    "computed_roi_percent": None,
                    "issues": (*row.issues, _issue("DUPLICATE_SELLER_SKU", "Item", "ERROR")),
                }
            )
        results.append(row)
    controls: list[ConnectBooksControl] = []
    for field in HEADERS:
        values = [row.amounts[field] for row in results]
        total = (
            sum((v for v in values if v is not None), Decimal(0))
            if all(v is not None for v in values)
            else None
        )
        reported = summary[field]
        difference = total - reported if total is not None and reported is not None else None
        status = (
            "INCOMPLETE"
            if difference is None
            else ("MATCH" if abs(difference) <= TOLERANCE else "MISMATCH")
        )
        controls.append(
            ConnectBooksControl.model_validate(
                {
                    "field": field,
                    "detail_total": total,
                    "reported_total": reported,
                    "difference": difference,
                    "status": status,
                }
            )
        )
        if status != "MATCH":
            issues.append(_issue(f"CONTROL_TOTAL_{status}", field, "ERROR"))
    if profile.reporting_year is None:
        issues.append(_issue("REPORTING_YEAR_UNCONFIRMED", "reporting_year"))
    if profile.reporting_timezone is None:
        issues.append(_issue("REPORTING_TIMEZONE_UNCONFIRMED", "reporting_timezone"))
    if not profile.advertising_coverage_confirmed:
        issues.append(_issue("ADVERTISING_COVERAGE_UNCONFIRMED", "ad_spend"))
    return ConnectBooksXlsxPreview(
        status="REVIEW_REQUIRED"
        if issues or any(r.status != "RECONCILED" for r in results)
        else "RECONCILED_PREVIEW",
        profile=profile,
        raw_sha256=hashlib.sha256(content).hexdigest(),
        source_columns={h: _letter(i) for h, i in indexes.items()},
        summary_row_number=summary_number,
        blank_rows=blanks,
        sku_rows=sum(r.row_kind == "SKU" for r in results),
        unallocated_rows=sum(r.row_kind == "UNALLOCATED" for r in results),
        rows=tuple(results),
        controls=tuple(controls),
        issues=tuple(issues),
    )
