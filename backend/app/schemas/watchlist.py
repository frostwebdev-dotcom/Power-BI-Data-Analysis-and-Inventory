"""Watchlist and availability-feed schemas (AC-10, AC-11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AvailabilityEventType,
    AvailabilityStatus,
    OosStatus,
    WatchlistPriority,
)


class WatchlistCreate(BaseModel):
    product_id: uuid.UUID
    #: Null watches the product at every vendor.
    vendor_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)
    priority: WatchlistPriority = WatchlistPriority.NORMAL
    desired_quantity: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    max_unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    notes: str | None = Field(default=None, max_length=2000)


class WatchlistRemove(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class WatchedProduct(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catalog_item_number: str
    name: str


class WatchlistEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product: WatchedProduct
    vendor_id: uuid.UUID | None
    reason: str | None
    priority: WatchlistPriority
    desired_quantity: Decimal | None
    max_unit_cost: Decimal | None
    current_status: OosStatus
    current_status_changed_at: datetime | None
    is_active: bool
    added_by_user_id: uuid.UUID | None
    added_at: datetime
    deactivated_by_user_id: uuid.UUID | None
    deactivated_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class WatchlistListResponse(BaseModel):
    items: list[WatchlistEntryResponse]
    page: int
    page_size: int
    total: int


class StatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    oos_watchlist_id: uuid.UUID
    product_id: uuid.UUID
    vendor_id: uuid.UUID | None
    previous_status: OosStatus | None
    new_status: OosStatus
    vendor_inventory_snapshot_id: uuid.UUID | None
    changed_at: datetime


class WatchlistHistoryResponse(BaseModel):
    entry: WatchlistEntryResponse
    history: list[StatusHistoryResponse]


class AvailabilityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_product_id: uuid.UUID
    product_id: uuid.UUID | None
    oos_watchlist_id: uuid.UUID | None
    event_type: AvailabilityEventType
    previous_snapshot_id: uuid.UUID | None
    current_snapshot_id: uuid.UUID
    previous_status: AvailabilityStatus | None
    new_status: AvailabilityStatus
    previous_quantity: Decimal | None
    new_quantity: Decimal | None
    over_max_unit_cost: bool | None
    detected_at: datetime
    acknowledged_by_user_id: uuid.UUID | None
    acknowledged_at: datetime | None
    #: Denormalised for the feed.
    vendor_sku: str | None = None
    product_name: str | None = None
    catalog_item_number: str | None = None


class AvailabilityEventListResponse(BaseModel):
    items: list[AvailabilityEventResponse]
    page: int
    page_size: int
    total: int
