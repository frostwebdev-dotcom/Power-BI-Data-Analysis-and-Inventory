"""Public, read-only Amazon analytics response models (ADR 0011)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import MappingStatus, MatchMethod


class AmazonVelocityRowResponse(BaseModel):
    seller_sku: str
    asin: str | None
    product_id: uuid.UUID | None
    catalog_item_number: str | None
    upc: str | None
    units_7: int = Field(ge=0)
    units_14: int = Field(ge=0)
    units_30: int = Field(ge=0)
    avg_daily_14: float = Field(ge=0)
    fulfillable: int | None = Field(default=None, ge=0)
    fbm_quantity: int | None = Field(default=None, ge=0)
    inbound: int | None = Field(default=None, ge=0)
    on_hand: int | None = Field(default=None, ge=0)
    days_of_supply: float | None = Field(default=None, ge=0)
    mapping_status: MappingStatus | None
    mapping_method: MatchMethod | None


class AmazonVelocityResponse(BaseModel):
    level: Literal["sku", "product"]
    items: list[AmazonVelocityRowResponse]
    count: int = Field(ge=0)
