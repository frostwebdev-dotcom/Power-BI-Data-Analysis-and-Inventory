"""Apply a profile's rules to a parsed table — the preview the validate
endpoint returns, and later the first step of a real import.

Nothing here touches the database. Given headers, rows and a
:class:`ProfileRules`, it resolves each mapped source to a column, checks
the header signature (AC-6.4), and maps every row into the target fields
with per-row issues. Values stay strings except where a rule says how to
read them (quantity as an integer, cost as a decimal, UPC normalised).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.imports.profile_rules import (
    ColumnMapping,
    ColumnTarget,
    NormalizationRules,
    ProfileRules,
    compute_header_signature,
    normalize_header,
)
from app.matching.normalize import normalize_gtin
from app.models.enums import AvailabilityStatus


@dataclass(frozen=True, slots=True)
class ResolvedColumn:
    target: str
    source: str | int
    index: int | None
    header: str | None
    required: bool


@dataclass(slots=True)
class MappedRow:
    row_number: int
    values: dict[str, Any]
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MappingPreview:
    headers: list[str]
    header_signature: str
    expected_signature: str | None
    signature_matches: bool | None
    columns: list[ResolvedColumn]
    rows: list[MappedRow]
    issues: list[str]
    #: A header-level failure: a required column is missing. Rows are still
    #: mapped as far as possible, so the operator sees what *would* happen.
    header_ok: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "headers": self.headers,
            "header_signature": self.header_signature,
            "expected_signature": self.expected_signature,
            "signature_matches": self.signature_matches,
            "header_ok": self.header_ok,
            "columns": [
                {
                    "target": c.target,
                    "source": c.source,
                    "index": c.index,
                    "header": c.header,
                    "required": c.required,
                }
                for c in self.columns
            ],
            "issues": self.issues,
            "rows": [
                {"row_number": r.row_number, "values": r.values, "issues": r.issues}
                for r in self.rows
            ],
        }


def resolve_columns(
    rules: ProfileRules, headers: list[str]
) -> tuple[list[ResolvedColumn], list[str]]:
    """Find each mapped source in the header row. Missing required → issue."""
    normalized = [normalize_header(h) for h in headers]
    resolved: list[ResolvedColumn] = []
    issues: list[str] = []
    for mapping in rules.column_map.columns:
        index = _locate(mapping, normalized)
        resolved.append(
            ResolvedColumn(
                target=mapping.target.value,
                source=mapping.source,
                index=index,
                header=headers[index] if index is not None else None,
                required=mapping.required or mapping.target in _ALWAYS_REQUIRED,
            )
        )
        if index is None and (mapping.required or mapping.target in _ALWAYS_REQUIRED):
            issues.append(
                f"required column for {mapping.target.value} not found: {mapping.source!r}"
            )
        elif index is None:
            issues.append(
                f"optional column for {mapping.target.value} not found: {mapping.source!r}"
            )
    return resolved, issues


_ALWAYS_REQUIRED = frozenset({ColumnTarget.QUANTITY_AVAILABLE})


def _locate(mapping: ColumnMapping, normalized_headers: list[str]) -> int | None:
    key = mapping.source_key
    if isinstance(key, int):
        return key if key < len(normalized_headers) else None
    try:
        return normalized_headers.index(key)
    except ValueError:
        return None


def map_table(
    rules: ProfileRules,
    headers: list[str],
    rows: list[list[str]],
    *,
    expected_signature: str | None = None,
) -> MappingPreview:
    columns, issues = resolve_columns(rules, headers)
    signature = compute_header_signature(headers)
    matches = None if expected_signature is None else signature == expected_signature
    if matches is False:
        issues.append(
            "header signature does not match the profile; the file's columns differ "
            "from the ones the profile was built for"
        )
    header_ok = not any(i.startswith("required column") for i in issues)

    by_target = {c.target: c for c in columns if c.index is not None}
    status_index = _status_index(rules, headers)
    if rules.availability_rules.available_when == "status_column" and status_index is None:
        issues.append(
            f"status column not found: {rules.availability_rules.status_column!r}; "
            "availability will be UNKNOWN for every row"
        )
    mapped = [
        _map_row(rules, by_target, status_index, number, row)
        for number, row in enumerate(rows, start=1)
    ]
    return MappingPreview(
        headers=headers,
        header_signature=signature,
        expected_signature=expected_signature,
        signature_matches=matches,
        columns=columns,
        rows=mapped,
        issues=issues,
        header_ok=header_ok,
    )


def _status_index(rules: ProfileRules, headers: list[str]) -> int | None:
    """The status column is named by header text in the availability rules,
    independently of the column map, so a vendor's "Status" column need not
    be mapped to a target to be read."""
    availability = rules.availability_rules
    if availability.available_when != "status_column" or availability.status_column is None:
        return None
    key = normalize_header(availability.status_column)
    normalized = [normalize_header(h) for h in headers]
    return normalized.index(key) if key in normalized else None


def _map_row(
    rules: ProfileRules,
    by_target: dict[str, ResolvedColumn],
    status_index: int | None,
    number: int,
    row: list[str],
) -> MappedRow:
    norm = rules.normalization_rules
    values: dict[str, Any] = {}
    issues: list[str] = []

    def raw(target: ColumnTarget) -> str | None:
        column = by_target.get(target.value)
        if column is None or column.index is None or column.index >= len(row):
            return None
        text = row[column.index]
        return text.strip() if norm.trim else text

    for target in ColumnTarget:
        if target is ColumnTarget.IGNORE or target.value not in by_target:
            continue
        values[target.value] = raw(target)

    # UPC — normalised for matching, raw value kept alongside.
    upc_raw = raw(ColumnTarget.UPC)
    if upc_raw is not None and upc_raw != "":
        canonical, valid, problem = _normalize_upc(upc_raw, norm)
        values["upc_normalized"] = canonical
        values["upc_valid_checksum"] = valid
        if problem is not None:
            issues.append(f"upc {upc_raw!r}: {problem}")
    elif ColumnTarget.UPC.value in by_target:
        values["upc_normalized"] = None
        values["upc_valid_checksum"] = None

    # Quantity — an integer, always.
    quantity_raw = raw(ColumnTarget.QUANTITY_AVAILABLE)
    quantity = _parse_int(quantity_raw, norm)
    if quantity_raw is None or quantity_raw == "":
        issues.append("quantity_available is blank")
    elif quantity is None:
        issues.append(f"quantity_available is not a whole number: {quantity_raw!r}")
    elif quantity < 0:
        issues.append(f"quantity_available is negative: {quantity}")
    values["quantity"] = quantity

    # Cost — a decimal, if mapped.
    if ColumnTarget.UNIT_COST.value in by_target:
        cost_raw = raw(ColumnTarget.UNIT_COST)
        cost = _parse_decimal(cost_raw, norm)
        if cost_raw and cost is None:
            issues.append(f"unit_cost is not a number: {cost_raw!r}")
        values["unit_cost_parsed"] = str(cost) if cost is not None else None

    if ColumnTarget.PACK_SIZE.value in by_target:
        pack_raw = raw(ColumnTarget.PACK_SIZE)
        pack = _parse_int(pack_raw, norm)
        if pack_raw and (pack is None or pack <= 0):
            issues.append(f"pack_size is not a positive whole number: {pack_raw!r}")
        values["pack_size_parsed"] = pack

    values["availability"] = _availability(rules, quantity, row, status_index).value
    return MappedRow(row_number=number, values=values, issues=issues)


def _availability(
    rules: ProfileRules,
    quantity: int | None,
    row: list[str],
    status_index: int | None,
) -> AvailabilityStatus:
    availability = rules.availability_rules
    if availability.available_when == "quantity_gt_zero":
        if quantity is None:
            return AvailabilityStatus.UNKNOWN
        return AvailabilityStatus.AVAILABLE if quantity > 0 else AvailabilityStatus.OUT_OF_STOCK

    # status_column mode: the column was located by header text in map_table.
    if status_index is None:
        return AvailabilityStatus.UNKNOWN
    value = row[status_index].strip().lower() if status_index < len(row) else ""
    if value in {v.lower() for v in availability.available_values}:
        return AvailabilityStatus.AVAILABLE
    if value in {v.lower() for v in availability.unavailable_values}:
        return AvailabilityStatus.OUT_OF_STOCK
    # Unrecognised → UNKNOWN, never available (AC-11.5).
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


def _clean_number(text: str, norm: NormalizationRules) -> str:
    cleaned = text
    for symbol in norm.currency_symbols_to_strip:
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(" ", "")
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
    if value != value.to_integral_value():
        return None
    return int(value)


def _parse_decimal(text: str | None, norm: NormalizationRules) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        return Decimal(_clean_number(text, norm))
    except InvalidOperation:
        return None
