"""The six rule shapes of a vendor import profile (AC-6, ADR 0013).

``vendor_import_profiles`` has six JSONB columns. This module says exactly
what may go in each: a Pydantic model per column, validated on every write,
with the JSON Schema exported for the admin UI to build its forms from. A
profile is data, not code (ADR 0005) — but data with a shape, or the parser
would be guessing.

Cross-rule checks live on :class:`ProfileRules`: a rule that references a
column the map does not provide is refused before anything is stored.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")
_WHITESPACE: Final = re.compile(r"\s+")


# --- column map --------------------------------------------------------------------


class ColumnTarget(StrEnum):
    """The fields a vendor column may feed. Nothing else is ever read."""

    VENDOR_SKU = "vendor_sku"
    UPC = "upc"
    DESCRIPTION = "description"
    QUANTITY_AVAILABLE = "quantity_available"
    UNIT_COST = "unit_cost"
    PACK_SIZE = "pack_size"
    UNIT_OF_MEASURE = "unit_of_measure"
    IGNORE = "ignore"


class ColumnMapping(BaseModel):
    """One source column → one target field."""

    model_config = ConfigDict(extra="forbid")

    target: ColumnTarget
    #: A header cell's text (matched after normalisation) or a 0-based index.
    source: str | int = Field(union_mode="left_to_right")
    required: bool = False

    @field_validator("source", mode="before")
    @classmethod
    def _not_a_bool(cls, value: object) -> object:
        # Lax int coercion would turn ``true`` into column 1; refuse it first.
        if isinstance(value, bool):
            raise ValueError("source must be a header text or a 0-based column index")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: str | int) -> str | int:
        if isinstance(value, int):
            if value < 0:
                raise ValueError("a column index must be 0 or greater")
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("a header text must not be blank")
        return stripped

    @property
    def source_key(self) -> str | int:
        """What the mapper compares against: a normalised header or an index."""
        return normalize_header(self.source) if isinstance(self.source, str) else self.source


class ColumnMap(BaseModel):
    """The whole mapping. Stored as ``{"columns": [...]}``."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnMapping] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ColumnMap:
        targets = [c.target for c in self.columns if c.target is not ColumnTarget.IGNORE]
        duplicates = sorted({t.value for t in targets if targets.count(t) > 1})
        if duplicates:
            raise ValueError(f"each target may be mapped once; duplicated: {duplicates}")
        if ColumnTarget.UPC not in targets and ColumnTarget.VENDOR_SKU not in targets:
            raise ValueError("at least one of upc or vendor_sku must be mapped")
        if ColumnTarget.QUANTITY_AVAILABLE not in targets:
            raise ValueError("quantity_available must be mapped")
        sources = [c.source_key for c in self.columns]
        repeated = sorted({str(s) for s in sources if sources.count(s) > 1})
        if repeated:
            raise ValueError(f"each source column may be mapped once; duplicated: {repeated}")
        return self

    def targets(self) -> set[ColumnTarget]:
        return {c.target for c in self.columns if c.target is not ColumnTarget.IGNORE}


# --- normalisation ---------------------------------------------------------------------


class NormalizationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim: bool = True
    upc_strip_non_digits: bool = True
    #: An 11-digit UPC-A that lost its leading zero in a spreadsheet gets it back.
    upc_pad_to_12: bool = True
    decimal_separator: Literal[".", ","] = "."
    thousands_separator: Literal["", ",", "."] = ""
    currency_symbols_to_strip: list[str] = Field(default_factory=lambda: ["$"])

    @model_validator(mode="after")
    def _separators_differ(self) -> NormalizationRules:
        if self.thousands_separator and self.thousands_separator == self.decimal_separator:
            raise ValueError("decimal_separator and thousands_separator must differ")
        return self

    @field_validator("currency_symbols_to_strip")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        cleaned = [s for s in (v.strip() for v in value) if s]
        if any(s.isdigit() or s in ".," for s in cleaned):
            raise ValueError("a currency symbol cannot be a digit or a separator")
        return cleaned


# --- availability ------------------------------------------------------------------------


class AvailabilityRules(BaseModel):
    """How a row says "in stock". Unrecognised status values map to UNKNOWN,
    never to available (risk R6, AC-11.5)."""

    model_config = ConfigDict(extra="forbid")

    available_when: Literal["quantity_gt_zero", "status_column"] = "quantity_gt_zero"
    status_column: str | None = None
    available_values: list[str] = Field(default_factory=list)
    unavailable_values: list[str] = Field(default_factory=list)

    @field_validator("available_values", "unavailable_values")
    @classmethod
    def _values(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v.strip()]
        lowered = [v.lower() for v in cleaned]
        if len(set(lowered)) != len(lowered):
            raise ValueError("values must be distinct (case-insensitive)")
        return cleaned

    @model_validator(mode="after")
    def _consistent(self) -> AvailabilityRules:
        if self.available_when == "status_column":
            if not (self.status_column and self.status_column.strip()):
                raise ValueError("status_column is required when available_when is status_column")
            if not self.available_values and not self.unavailable_values:
                raise ValueError("status_column mode needs available_values or unavailable_values")
            overlap = {v.lower() for v in self.available_values} & {
                v.lower() for v in self.unavailable_values
            }
            if overlap:
                raise ValueError(
                    f"a value cannot be both available and unavailable: {sorted(overlap)}"
                )
        elif self.status_column or self.available_values or self.unavailable_values:
            raise ValueError("status_column and value lists apply only to status_column mode")
        return self


# --- quantity, price, pack size ----------------------------------------------------------


class QuantitySemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: Literal["each", "case"] = "each"
    case_pack_from: Literal["pack_size_column", "fixed"] = "pack_size_column"
    fixed_pack_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _consistent(self) -> QuantitySemantics:
        if self.unit == "case" and self.case_pack_from == "fixed" and self.fixed_pack_size is None:
            raise ValueError(
                "fixed_pack_size is required when unit is case and case_pack_from is fixed"
            )
        if self.case_pack_from == "pack_size_column" and self.fixed_pack_size is not None:
            raise ValueError("fixed_pack_size applies only when case_pack_from is fixed")
        return self


class PriceSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    includes_tax: bool = False
    per: Literal["each", "case"] = "each"

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        code = value.strip().upper()
        if not _CURRENCY.match(code):
            raise ValueError("currency must be a three-letter ISO 4217 code")
        return code


class PackSizeHandling(BaseModel):
    """Only ``store_as_stated`` today (phase1-status B3): quantities are kept
    exactly as the vendor states them and pack size alongside. A
    ``normalize_to_each`` mode is the obvious later addition and would need
    a conversion policy decided first."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["store_as_stated"] = "store_as_stated"


# --- the aggregate and cross-rule checks ----------------------------------------------------


class ProfileRules(BaseModel):
    """All six rule sets together, so rules that refer to each other are checked."""

    model_config = ConfigDict(extra="forbid")

    column_map: ColumnMap
    normalization_rules: NormalizationRules = Field(default_factory=NormalizationRules)
    availability_rules: AvailabilityRules = Field(default_factory=AvailabilityRules)
    quantity_semantics: QuantitySemantics = Field(default_factory=QuantitySemantics)
    price_semantics: PriceSemantics = Field(default_factory=PriceSemantics)
    pack_size_handling: PackSizeHandling = Field(default_factory=PackSizeHandling)

    @model_validator(mode="after")
    def _cross_rules(self) -> ProfileRules:
        targets = self.column_map.targets()
        if (
            self.quantity_semantics.unit == "case"
            and self.quantity_semantics.case_pack_from == "pack_size_column"
            and ColumnTarget.PACK_SIZE not in targets
        ):
            raise ValueError(
                "quantity_semantics reads the pack size from a column, "
                "but column_map maps no pack_size"
            )
        if self.price_semantics.per == "case" and ColumnTarget.UNIT_COST not in targets:
            raise ValueError("price_semantics is per case but column_map maps no unit_cost")
        return self

    def as_columns(self) -> dict[str, dict[str, Any]]:
        """The six JSONB column values, ready to store."""
        return {
            "column_map": self.column_map.model_dump(mode="json"),
            "normalization_rules": self.normalization_rules.model_dump(mode="json"),
            "availability_rules": self.availability_rules.model_dump(mode="json"),
            "quantity_semantics": self.quantity_semantics.model_dump(mode="json"),
            "price_semantics": self.price_semantics.model_dump(mode="json"),
            "pack_size_handling": self.pack_size_handling.model_dump(mode="json"),
        }

    @classmethod
    def from_columns(cls, **columns: dict[str, Any]) -> ProfileRules:
        """Rebuild from stored JSONB. A stored profile that no longer validates
        is a defect worth surfacing, so this is strict too."""
        return cls.model_validate(columns)


RULE_MODELS: Final[dict[str, type[BaseModel]]] = {
    "column_map": ColumnMap,
    "normalization_rules": NormalizationRules,
    "availability_rules": AvailabilityRules,
    "quantity_semantics": QuantitySemantics,
    "price_semantics": PriceSemantics,
    "pack_size_handling": PackSizeHandling,
}


def rule_json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema per rule column, for the admin UI to build its forms from."""
    return {name: model.model_json_schema() for name, model in RULE_MODELS.items()}


# --- header signature ----------------------------------------------------------------------


def normalize_header(text: str) -> str:
    """Case-folded, whitespace-collapsed, trimmed; what two headers are compared on."""
    return _WHITESPACE.sub(" ", text.strip()).casefold()


def compute_header_signature(headers: Iterable[str]) -> str:
    """A stable fingerprint of a header row (AC-6.4).

    The normalised cells are joined with a separator that cannot appear in a
    normalised header and hashed, so a file with a renamed, added or
    reordered column produces a different signature — and mapping by
    position, which would silently put a cost in the quantity field, never
    happens.
    """
    joined = "\x1f".join(normalize_header(h) for h in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
