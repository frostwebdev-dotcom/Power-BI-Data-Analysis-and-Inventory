"""Import-profile request and response schemas (AC-6).

The six rule columns are typed with the models in
:mod:`app.imports.profile_rules`, so a profile that does not validate never
reaches the database and the OpenAPI document carries the full rule shapes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.imports.profile_rules import (
    AvailabilityRules,
    ColumnMap,
    NormalizationRules,
    PackSizeHandling,
    PriceSemantics,
    ProfileRules,
    QuantitySemantics,
)
from app.models.enums import FileFormat


class _FileSettings(BaseModel):
    file_format: FileFormat = FileFormat.CSV
    encoding: str | None = Field(default=None, max_length=40)
    delimiter: str | None = Field(default=None, min_length=1, max_length=4)
    quote_char: str | None = Field(default=None, min_length=1, max_length=1)
    header_row_index: int = Field(default=0, ge=0, le=100)
    skip_rows: int = Field(default=0, ge=0, le=1000)
    sheet_name: str | None = Field(default=None, max_length=100)
    sheet_index: int | None = Field(default=None, ge=0, le=100)

    @field_validator("delimiter")
    @classmethod
    def _delimiter(cls, value: str | None) -> str | None:
        if value is not None and value in ("\\t", "tab", "TAB"):
            return "\t"
        return value

    @model_validator(mode="after")
    def _sheet_rules(self) -> _FileSettings:
        if self.file_format is FileFormat.CSV and (
            self.sheet_name is not None or self.sheet_index is not None
        ):
            raise ValueError("a sheet selector applies only to XLSX profiles")
        if self.sheet_name is not None and self.sheet_index is not None:
            raise ValueError("select a sheet by name or by index, not both")
        return self


class ImportProfileCreate(_FileSettings):
    name: str = Field(min_length=1, max_length=100)
    column_map: ColumnMap
    normalization_rules: NormalizationRules = Field(default_factory=NormalizationRules)
    availability_rules: AvailabilityRules = Field(default_factory=AvailabilityRules)
    quantity_semantics: QuantitySemantics = Field(default_factory=QuantitySemantics)
    price_semantics: PriceSemantics = Field(default_factory=PriceSemantics)
    pack_size_handling: PackSizeHandling = Field(default_factory=PackSizeHandling)
    #: Set from the validate endpoint's ``header_signature`` once a sample
    #: file has been checked; null means "not yet pinned".
    header_signature: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    def rules(self) -> ProfileRules:
        """The six rule sets validated together (cross-rule checks)."""
        return ProfileRules(
            column_map=self.column_map,
            normalization_rules=self.normalization_rules,
            availability_rules=self.availability_rules,
            quantity_semantics=self.quantity_semantics,
            price_semantics=self.price_semantics,
            pack_size_handling=self.pack_size_handling,
        )

    @model_validator(mode="after")
    def _cross_rules(self) -> ImportProfileCreate:
        self.rules()  # raises with the cross-rule message
        return self


class ImportProfileUpdate(BaseModel):
    """A new version. Every field is optional; unset fields carry over.

    ``name`` is not here: it identifies the profile line across versions.
    """

    file_format: FileFormat | None = None
    encoding: str | None = Field(default=None, max_length=40)
    delimiter: str | None = Field(default=None, min_length=1, max_length=4)
    quote_char: str | None = Field(default=None, min_length=1, max_length=1)
    header_row_index: int | None = Field(default=None, ge=0, le=100)
    skip_rows: int | None = Field(default=None, ge=0, le=1000)
    sheet_name: str | None = Field(default=None, max_length=100)
    sheet_index: int | None = Field(default=None, ge=0, le=100)
    column_map: ColumnMap | None = None
    normalization_rules: NormalizationRules | None = None
    availability_rules: AvailabilityRules | None = None
    quantity_semantics: QuantitySemantics | None = None
    price_semantics: PriceSemantics | None = None
    pack_size_handling: PackSizeHandling | None = None
    header_signature: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("delimiter")
    @classmethod
    def _delimiter(cls, value: str | None) -> str | None:
        if value is not None and value in ("\\t", "tab", "TAB"):
            return "\t"
        return value


class ImportProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    vendor_id: uuid.UUID
    name: str
    version: int
    is_active: bool
    file_format: FileFormat
    encoding: str | None
    delimiter: str | None
    quote_char: str | None
    header_row_index: int
    skip_rows: int
    sheet_name: str | None
    sheet_index: int | None
    column_map: dict[str, Any]
    normalization_rules: dict[str, Any]
    availability_rules: dict[str, Any]
    quantity_semantics: dict[str, Any]
    price_semantics: dict[str, Any]
    pack_size_handling: dict[str, Any]
    header_signature: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ImportProfileListResponse(BaseModel):
    items: list[ImportProfileResponse]
    total: int


class ValidationPreviewResponse(BaseModel):
    """What the profile would make of a sample file. Nothing is stored."""

    file_name: str | None
    encoding: str | None
    sheet: str | None
    truncated: bool
    headers: list[str]
    header_signature: str
    expected_signature: str | None
    signature_matches: bool | None
    header_ok: bool
    columns: list[dict[str, Any]]
    issues: list[str]
    rows: list[dict[str, Any]]
    notes: list[str]


class RuleSchemasResponse(BaseModel):
    """JSON Schema for each of the six rule columns."""

    schemas: dict[str, dict[str, Any]]
