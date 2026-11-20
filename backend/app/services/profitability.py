"""Strict, non-persistent CSV period-total previews (ADR 0023)."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final

from app.imports.profile_rules import normalize_header
from app.imports.readers import ReadOptions, UnreadableFile, open_rows
from app.models.enums import FileFormat
from app.schemas.profitability import (
    CsvField,
    ExpenseField,
    MoneyField,
    ProfitabilityCsvPreview,
    ProfitabilityCsvProfile,
    ProfitabilityIssue,
    ProfitabilityMetrics,
    ProfitabilityPreviewRow,
)

MAX_CSV_BYTES: Final = 8 * 1024 * 1024
MAX_CSV_ROWS: Final = 10_000
EXPENSE_FIELDS: Final[tuple[ExpenseField, ...]] = (
    "cost_of_goods",
    "amazon_fees",
    "shipping",
    "advertising",
    "other_costs",
)
MONEY_FIELDS: Final[tuple[MoneyField, ...]] = ("net_revenue", *EXPENSE_FIELDS)


class ProfitabilityPreviewError(ValueError):
    """A safe batch-level error code; never includes raw financial cells."""


@dataclass
class _FinancialRow:
    row_number: int
    seller_sku: str | None
    units_sold: int | None
    amounts: dict[MoneyField, Decimal | None]
    issues: list[ProfitabilityIssue]


def _issue(code: str, field: str, severity: str) -> ProfitabilityIssue:
    return ProfitabilityIssue.model_validate({"code": code, "field": field, "severity": severity})


def _number(raw: str, profile: ProfitabilityCsvProfile) -> Decimal:
    value = raw.strip()
    group = re.escape(profile.thousands_separator)
    integer = rf"(?:[0-9]+|[0-9]{{1,3}}(?:{group}[0-9]{{3}})+)" if group else r"[0-9]+"
    decimal = re.escape(profile.decimal_separator)
    if not re.fullmatch(rf"-?{integer}(?:{decimal}[0-9]{{1,6}})?", value):
        raise ValueError("invalid numeric format")
    if profile.thousands_separator:
        value = value.replace(profile.thousands_separator, "")
    value = value.replace(profile.decimal_separator, ".")
    if len(value.lstrip("-").split(".")[0]) > 18:
        raise ValueError("numeric value exceeds precision limit")
    return Decimal(value)


def _parse_row(
    row_number: int,
    cells: list[str],
    column_indexes: dict[CsvField, int],
    profile: ProfitabilityCsvProfile,
) -> _FinancialRow:
    def cell(field: CsvField) -> str:
        index = column_indexes.get(field)
        return cells[index].strip() if index is not None else ""

    issues: list[ProfitabilityIssue] = []
    sku = cell("seller_sku") or None
    if sku is None:
        issues.append(_issue("MISSING_SELLER_SKU", "seller_sku", "ERROR"))
    units: int | None = None
    raw_units = cell("units_sold")
    if not raw_units:
        issues.append(_issue("MISSING_UNITS", "units_sold", "REVIEW"))
    else:
        try:
            parsed = _number(raw_units, profile)
            if parsed < 0 or parsed != parsed.to_integral_value():
                raise ValueError("units must be nonnegative whole seller-SKU units")
            units = int(parsed)
        except ValueError:
            issues.append(_issue("INVALID_UNITS", "units_sold", "ERROR"))
    amounts: dict[MoneyField, Decimal | None] = {}
    for field in MONEY_FIELDS:
        raw = cell(field)
        if field in profile.confirmed_zero_costs:
            amounts[field] = Decimal(0)
        elif not raw:
            amounts[field] = None
            issues.append(_issue("MISSING_AMOUNT", field, "REVIEW"))
        else:
            try:
                amount = _number(raw, profile)
                if field != "net_revenue" and amount < 0:
                    raise ValueError("expenses use positive totals")
                amounts[field] = amount
            except ValueError:
                amounts[field] = None
                issues.append(_issue("INVALID_AMOUNT", field, "ERROR"))
    if "currency" in column_indexes and cell("currency") != profile.currency:
        issues.append(_issue("CURRENCY_MISMATCH", "currency", "ERROR"))
    return _FinancialRow(row_number, sku, units, amounts, issues)


def _metrics(row: _FinancialRow) -> ProfitabilityMetrics:
    revenue, cogs = row.amounts["net_revenue"], row.amounts["cost_of_goods"]
    costs = [row.amounts[field] for field in EXPENSE_FIELDS]
    with localcontext(Context(prec=38, rounding=ROUND_HALF_EVEN)):
        profit = (
            revenue - sum((cost for cost in costs if cost is not None), Decimal(0))
            if revenue is not None and all(cost is not None for cost in costs)
            else None
        )
        margin = (
            profit / revenue * 100
            if profit is not None and revenue is not None and revenue > 0
            else None
        )
        roi = profit / cogs * 100 if profit is not None and cogs is not None and cogs > 0 else None
        if profit is not None and margin is None:
            row.issues.append(_issue("MARGIN_UNDEFINED", "margin_percent", "NOTICE"))
        if profit is not None and roi is None:
            row.issues.append(_issue("ROI_UNDEFINED", "roi_percent", "NOTICE"))
        units = row.units_sold
        if units is None or units == 0:
            row.issues.append(_issue("PER_UNIT_UNDEFINED", "units_sold", "NOTICE"))
        return ProfitabilityMetrics(
            net_profit=profit,
            margin_percent=margin,
            roi_percent=roi,
            net_revenue_per_sku_unit=revenue / units if revenue is not None and units else None,
            cost_per_sku_unit=cogs / units if cogs is not None and units else None,
            profit_per_sku_unit=profit / units if profit is not None and units else None,
        )


def preview_profitability_csv(
    content: bytes, profile: ProfitabilityCsvProfile
) -> ProfitabilityCsvPreview:
    """Validate an explicitly mapped single-period CSV without binding SKU identities.

    No source column, currency, sign convention, zero expense or reporting period
    is inferred. Duplicate seller SKUs are all invalidated, never aggregated.
    """
    if len(content) > MAX_CSV_BYTES:
        raise ProfitabilityPreviewError("FILE_SIZE_LIMIT_EXCEEDED")
    options = ReadOptions(
        file_format=FileFormat.CSV,
        encoding=profile.encoding,
        delimiter=profile.delimiter,
        header_row_index=profile.header_row_index,
        strict_row_width=True,
        strict_csv_quoting=True,
    )
    parsed: list[_FinancialRow] = []
    blanks = 0
    try:
        stream = open_rows(content, options)
        headers = stream.normalized_headers
        if len(headers) > 256:
            raise ProfitabilityPreviewError("HEADER_LIMIT_EXCEEDED")
        if any(not header for header in headers) or len(set(headers)) != len(headers):
            raise ProfitabilityPreviewError("AMBIGUOUS_HEADERS")
        if any(normalize_header(column) not in headers for column in profile.columns.values()):
            raise ProfitabilityPreviewError("MAPPED_COLUMN_NOT_FOUND")
        indexes = {
            field: headers.index(normalize_header(column))
            for field, column in profile.columns.items()
        }
        for seen, source_row in enumerate(stream.rows, start=1):
            if seen > MAX_CSV_ROWS:
                raise ProfitabilityPreviewError("ROW_LIMIT_EXCEEDED")
            if source_row.is_blank:
                blanks += 1
                continue
            parsed.append(_parse_row(source_row.row_number, source_row.cells, indexes, profile))
    except UnreadableFile:
        raise ProfitabilityPreviewError("UNREADABLE_CSV") from None
    if not parsed:
        raise ProfitabilityPreviewError("NO_DATA_ROWS")
    counts = Counter(row.seller_sku for row in parsed if row.seller_sku is not None)
    results: list[ProfitabilityPreviewRow] = []
    for row in parsed:
        if row.seller_sku is not None and counts[row.seller_sku] > 1:
            row.issues.append(_issue("DUPLICATE_SELLER_SKU", "seller_sku", "ERROR"))
        invalid = any(issue.severity == "ERROR" for issue in row.issues)
        metrics = ProfitabilityMetrics() if invalid else _metrics(row)
        incomplete = any(issue.severity == "REVIEW" for issue in row.issues)
        results.append(
            ProfitabilityPreviewRow(
                row_number=row.row_number,
                seller_sku=row.seller_sku,
                units_sold=row.units_sold,
                amounts=row.amounts,
                status="INVALID" if invalid else "INCOMPLETE" if incomplete else "CALCULATED",
                metrics=metrics,
                issues=tuple(row.issues),
            )
        )
    return ProfitabilityCsvPreview(
        status="REVIEW_REQUIRED"
        if any(row.status != "CALCULATED" for row in results)
        else "VALIDATED_PREVIEW",
        profile=profile,
        raw_sha256=hashlib.sha256(content).hexdigest(),
        headers=tuple(stream.headers),
        blank_rows=blanks,
        rows=tuple(results),
    )
