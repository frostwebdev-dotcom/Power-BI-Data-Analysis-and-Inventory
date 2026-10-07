"""Read-only catalogue responses. Related rows are paginated separately."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    IdentifierType,
    ListingStatus,
    MappingStatus,
    Marketplace,
    MatchMethod,
    ProductStatus,
    SourceSystem,
)

T = TypeVar("T")


# Keep the form supported by the project's Pydantic 2.9 minimum.
class CataloguePage(BaseModel, Generic[T]):  # noqa: UP046
    items: list[T]
    page: int
    page_size: int
    total: int


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catalog_item_number: str
    name: str
    brand: str | None
    manufacturer: str | None
    description: str | None
    pack_size: int | None
    unit_of_measure: str | None
    status: ProductStatus
    is_active: bool
    nineyard_last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IdentifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    identifier_type: IdentifierType
    raw_value: str
    normalized_value: str
    source_system: SourceSystem
    vendor_id: uuid.UUID | None
    marketplace_listing_id: uuid.UUID | None
    has_valid_checksum: bool | None
    is_primary: bool
    is_active: bool


class ListingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None
    marketplace: Marketplace
    marketplace_id: str
    seller_sku: str
    asin: str | None
    name: str | None = None
    listing_status: ListingStatus
    mapping_status: MappingStatus
    mapping_method: MatchMethod | None
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    is_active: bool
