"""Apply a profile's rules to one source row (phase 6, AC-5, AC-9).

:func:`extract_row` turns the strings a reader produced into an
:class:`ExtractedRow`: typed, normalised values plus a list of
:class:`Issue` — each with a stable code, a severity, the field it is
about and a message (AC-9.1). The row's status is the worst severity.

What is validated here is what can be known from the row alone. Anything
that needs the whole file (a duplicate vendor SKU) or the database (a
match) is added by the caller. The same function serves the validate
endpoint's preview and the import runner, so what the operator previews is
what the import does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

from app.imports.profile_rules import (
    ColumnMapping,
    ColumnTarget,
    NormalizationRules,
    ProfileRules,
    normalize_header,
)
from app.matching.normalize import normalize_gtin
from app.models.enums import AvailabilityStatus, ImportRowStatus


class IssueCode(StrEnum):
    """Stable, machine-readable validation codes (AC-9.1)."""

    UPC_INVALID = "UPC_INVALID"
    QUANTITY_INVALID = "QUANTITY_INVALID"
    QUANTITY_NEGATIVE = "QUANTITY_NEGATIVE"
    PRICE_INVALID = "PRICE_INVALID"
    PACK_SIZE_INVALID = "PACK_SIZE_INVALID"
    IDENTIFIER_MISSING = "IDENTIFIER_MISSING"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"
    DUPLICATE_IN_FILE = "DUPLICATE_IN_FILE"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


SEVERITY_OF: Final[dict[IssueCode, Severity]] = {
    IssueCode.UPC_INVALID: Severity.WARNING,
    IssueCode.QUANTITY_INVALID: Severity.ERROR,
    IssueCode.QUANTITY_NEGATIVE: Severity.ERROR,
    IssueCode.PRICE_INVALID: Severity.WARNING,
    IssueCode.PACK_SIZE_INVALID: Severity.WARNING,
    IssueCode.IDENTIFIER_MISSING: Severity.ERROR,
    IssueCode.AVAILABILITY_UNKNOWN: Severity.WARNING,
    IssueCode.DUPLICATE_IN_FILE: Severity.WARNING,
}

_STATUS_OF: Final = {
    Severity.ERROR: ImportRowStatus.ERROR,
    Severity.WARNING: ImportRowStatus.WARNING,
    Severity.INFO: ImportRowStatus.OK,
}
_RANK: Final = {ImportRowStatus.OK: 0, ImportRowStatus.WARNING: 1, ImportRowStatus.ERROR: 2}


@dataclass(frozen=True, slots=True)
class Issue:
    code: IssueCode
    field: str
    message: str

    @property
    def severity(self) -> Severity:
        return SEVERITY_OF[self.code]

    def as_json(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "field": self.field,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ResolvedColumn:
    target: str
    source: str | int
    index: int | None
    header: str | None
    required: bool


@dataclass(slots=True)
class ColumnPlan:
    """Where each mapped target sits in this file's header row."""

    columns: list[ResolvedColumn]
    #: Header-level failures: a required column is missing.
    missing_required: list[ResolvedColumn]
    missing_optional: list[ResolvedColumn]
    status_index: int | None

    @property
    def by_target(self) -> dict[str, ResolvedColumn]:
        return {c.target: c for c in self.columns if c.index is not None}

    @property
    def header_ok(self) -> bool:
        return not self.missing_required


@dataclass(slots=True)
class ExtractedRow:
    row_number: int
    raw: dict[str, str]
    vendor_sku: str | None = None
    normalized_vendor_sku: str | None = None
    raw_upc: str | None = None
    normalized_upc: str | None = None
    has_valid_checksum: bool | None = None
    description: str | None = None
    quantity: int | None = None
    unit_cost: Decimal | None = None
    currency: str | None = None
    availability_status: AvailabilityStatus | None = None
    pack_size: int | None = None
    unit_of_measure: str | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> ImportRowStatus:
        worst = ImportRowStatus.OK
        for issue in self.issues:
            candidate = _STATUS_OF[issue.severity]
            if _RANK[candidate] > _RANK[worst]:
                worst = candidate
        return worst

    @property
    def primary_issue(self) -> Issue | None:
        """The issue that decided the status: worst severity, first raised."""
        if not self.issues:
            return None
        return max(
            self.issues, key=lambda i: (_RANK[_STATUS_OF[i.severity]], -self.issues.index(i))
        )

    def normalized_json(self) -> dict[str, Any]:
        """What ``import_job_rows.normalized_data`` stores: every derived value
        and every issue, so a row explains itself without re-parsing."""
        return {
            "vendor_sku": self.normalized_vendor_sku,
            "upc": self.normalized_upc,
            "upc_valid_checksum": self.has_valid_checksum,
            "description": self.description,
            "quantity": self.quantity,
            "unit_cost": str(self.unit_cost) if self.unit_cost is not None else None,
            "currency": self.currency,
            "availability": (self.availability_status.value if self.availability_status else None),
            "pack_size": self.pack_size,
            "unit_of_measure": self.unit_of_measure,
            "issues": [i.as_json() for i in self.issues],
        }


# --- columns ---------------------------------------------------------------------------

_ALWAYS_REQUIRED: Final = frozenset({ColumnTarget.QUANTITY_AVAILABLE})


def plan_columns(rules: ProfileRules, headers: list[str]) -> ColumnPlan:
    """Find each mapped source in the header row (case- and space-insensitive)."""
    normalized = [normalize_header(h) for h in headers]
    columns: list[ResolvedColumn] = []
    missing_required: list[ResolvedColumn] = []
    missing_optional: list[ResolvedColumn] = []
    for mapping in rules.column_map.columns:
        index = _locate(mapping, normalized)
        required = mapping.required or mapping.target in _ALWAYS_REQUIRED
        column = ResolvedColumn(
            target=mapping.target.value,
            source=mapping.source,
            index=index,
            header=headers[index] if index is not None else None,
            required=required,
        )
        columns.append(column)
        if index is None:
            (missing_required if required else missing_optional).append(column)

    availability = rules.availability_rules
    status_index = None
    if availability.available_when == "status_column" and availability.status_column:
        key = normalize_header(availability.status_column)
        status_index = normalized.index(key) if key in normalized else None
    return ColumnPlan(
        columns=columns,
        missing_required=missing_required,
        missing_optional=missing_optional,
        status_index=status_index,
    )


def _locate(mapping: ColumnMapping, normalized_headers: list[str]) -> int | None:
    key = mapping.source_key
    if isinstance(key, int):
        return key if key < len(normalized_headers) else None
    try:
        return normalized_headers.index(key)
    except ValueError:
        return None


# --- one row ---------------------------------------------------------------------------


def extract_row(
    rules: ProfileRules, plan: ColumnPlan, row_number: int, cells: list[str]
) -> ExtractedRow:
    norm = rules.normalization_rules
    by_target = plan.by_target

    def raw(target: ColumnTarget) -> str | None:
        column = by_target.get(target.value)
        if column is None or column.index is None or column.index >= len(cells):
            return None
        text = cells[column.index]
        return text.strip() if norm.trim else text

    row = ExtractedRow(
        row_number=row_number,
        raw={
            target.value: value
            for target in ColumnTarget
            if target is not ColumnTarget.IGNORE
            and target.value in by_target
            and (value := raw(target)) is not None
        },
    )

    # Identity: vendor SKU and UPC. A row with neither can never match.
    sku = raw(ColumnTarget.VENDOR_SKU)
    if sku:
        row.vendor_sku = sku
        row.normalized_vendor_sku = normalize_vendor_sku(sku)
    upc = raw(ColumnTarget.UPC)
    if upc:
        row.raw_upc = upc
        canonical, valid, problem = _normalize_upc(upc, norm)
        row.normalized_upc = canonical
        row.has_valid_checksum = valid
        if problem is not None:
            row.issues.append(Issue(IssueCode.UPC_INVALID, "upc", f"{upc!r}: {problem}"))
    if not sku and not upc:
        row.issues.append(
            Issue(
                IssueCode.IDENTIFIER_MISSING,
                "vendor_sku",
                "the row has neither a vendor SKU nor a UPC; nothing could ever match it",
            )
        )

    description = raw(ColumnTarget.DESCRIPTION)
    row.description = description or None
    uom = raw(ColumnTarget.UNIT_OF_MEASURE)
    row.unit_of_measure = uom or None

    # Quantity — always required, always a whole number, never negative.
    quantity_raw = raw(ColumnTarget.QUANTITY_AVAILABLE)
    if not quantity_raw:
        row.issues.append(
            Issue(IssueCode.QUANTITY_INVALID, "quantity_available", "quantity is blank")
        )
    else:
        quantity = _parse_int(quantity_raw, norm)
        if quantity is None:
            row.issues.append(
                Issue(
                    IssueCode.QUANTITY_INVALID,
                    "quantity_available",
                    f"{quantity_raw!r} is not a whole number",
                )
            )
        elif quantity < 0:
            row.issues.append(
                Issue(IssueCode.QUANTITY_NEGATIVE, "quantity_available", f"{quantity} is negative")
            )
        else:
            row.quantity = quantity

    # Cost — a decimal if mapped; a bad value imports with no cost.
    if ColumnTarget.UNIT_COST.value in by_target:
        cost_raw = raw(ColumnTarget.UNIT_COST)
        if cost_raw:
            cost = _parse_decimal(cost_raw, norm)
            if cost is None or cost < 0:
                row.issues.append(
                    Issue(IssueCode.PRICE_INVALID, "unit_cost", f"{cost_raw!r} is not a price")
                )
            else:
                row.unit_cost = cost
                row.currency = rules.price_semantics.currency

    if ColumnTarget.PACK_SIZE.value in by_target:
        pack_raw = raw(ColumnTarget.PACK_SIZE)
        if pack_raw:
            pack = _parse_int(pack_raw, norm)
            if pack is None or pack <= 0:
                row.issues.append(
                    Issue(
                        IssueCode.PACK_SIZE_INVALID,
                        "pack_size",
                        f"{pack_raw!r} is not a positive whole number",
                    )
                )
            else:
                row.pack_size = pack
    semantics = rules.quantity_semantics
    if semantics.unit == "case" and semantics.case_pack_from == "fixed":
        row.pack_size = semantics.fixed_pack_size

    row.availability_status = _availability(rules, plan, row, cells)
    return row


def normalize_vendor_sku(sku: str) -> str:
    """Trimmed, inner whitespace collapsed, upper-cased: the comparison key for
    priority 3. The raw SKU is kept alongside for display."""
    return " ".join(sku.split()).upper()


def _availability(
    rules: ProfileRules, plan: ColumnPlan, row: ExtractedRow, cells: list[str]
) -> AvailabilityStatus:
    availability = rules.availability_rules
    if availability.available_when == "quantity_gt_zero":
        if row.quantity is None:
            return AvailabilityStatus.UNKNOWN
        return AvailabilityStatus.AVAILABLE if row.quantity > 0 else AvailabilityStatus.OUT_OF_STOCK

    # status_column mode: the column was located by header text in the plan.
    if plan.status_index is None or plan.status_index >= len(cells):
        return AvailabilityStatus.UNKNOWN
    value = cells[plan.status_index].strip()
    lowered = value.lower()
    if lowered in {v.lower() for v in availability.available_values}:
        return AvailabilityStatus.AVAILABLE
    if lowered in {v.lower() for v in availability.unavailable_values}:
        return AvailabilityStatus.OUT_OF_STOCK
    # Unrecognised → UNKNOWN with a warning, never available (AC-11.5, risk R6).
    row.issues.append(
        Issue(
            IssueCode.AVAILABILITY_UNKNOWN,
            "availability",
            f"status {value!r} is not in the profile's available or unavailable values",
        )
    )
    return AvailabilityStatus.UNKNOWN


def _normalize_upc(
    text: str, norm: NormalizationRules
) -> tuple[str | None, bool | None, str | None]:
    """Apply the profile's UPC flags on top of the shared normaliser.

    ``upc_strip_non_digits`` off: a value with anything but digits is
    refused as-is instead of cleaned. ``upc_pad_to_12`` off: an 11-digit
    value is refused instead of having its leading zero restored.
    """
    candidate = text.strip()
    if not norm.upc_strip_non_digits and not candidate.isdigit():
        return None, None, "contains non-digit characters (stripping is disabled)"
    if not norm.upc_pad_to_12 and len(candidate) == 11 and candidate.isdigit():
        return None, None, "11 digits and leading-zero padding is disabled"
    normalized = normalize_gtin(candidate)
    return (
        normalized.canonical,
        normalized.valid_checksum,
        None if normalized.usable else normalized.problem,
    )


#: Digit-group separators some locales print: a plain space and the two
#: non-breaking spaces spreadsheets emit for "1 299,50".
_SPACES: Final = (" ", chr(0x00A0), chr(0x202F))


def _clean_number(text: str, norm: NormalizationRules) -> str:
    cleaned = text
    for symbol in norm.currency_symbols_to_strip:
        cleaned = cleaned.replace(symbol, "")
    for space in _SPACES:
        cleaned = cleaned.replace(space, "")
    if norm.thousands_separator:
        cleaned = cleaned.replace(norm.thousands_separator, "")
    if norm.decimal_separator == ",":
        cleaned = cleaned.replace(",", ".")
    return cleaned.strip()


def _parse_int(text: str | None, norm: NormalizationRules) -> int | None:
    if text is None or text == "":
        return None
    cleaned = _clean_number(text, norm)
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or value != value.to_integral_value():
        return None
    return int(value)


def _parse_decimal(text: str | None, norm: NormalizationRules) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        value = Decimal(_clean_number(text, norm))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None
