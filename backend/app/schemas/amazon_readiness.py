"""Explicit, non-purchasing assumptions and evidence for Amazon input readiness."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.replenishment import PreviewModel

Factor = Annotated[int, Field(strict=True, ge=1, le=1000000)]
Reference = Annotated[str, Field(min_length=10, max_length=500)]


class ListingUnitChoice(PreviewModel):
    listing_id: uuid.UUID
    base_units_per_listing_unit: Factor | None
    confirmation_reference: Reference | None


class StockPoolChoice(PreviewModel):
    pool_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")]
    channel: Literal["FBA", "FBM"]
    listing_ids: Annotated[tuple[uuid.UUID, ...], Field(min_length=1, max_length=200)]
    stock_source_listing_id: uuid.UUID
    distinct_stock_reference: Reference | None

    @model_validator(mode="after")
    def unique_members(self) -> StockPoolChoice:
        if (
            len(set(self.listing_ids)) != len(self.listing_ids)
            or self.stock_source_listing_id not in self.listing_ids
        ):
            raise ValueError("Pool members must be unique and include its stock source.")
        return self


class AmazonReadinessRequest(PreviewModel):
    product_id: uuid.UUID
    marketplace_id: Literal["ATVPDKIKX0DER"]
    sales_channel: Literal["Amazon.com"]
    demand_basis: Literal["FULLY_SHIPPED_ONLY"]
    velocity_window_days: Literal[7, 14, 30]
    orders_max_age_seconds: Annotated[int, Field(strict=True, ge=1, le=2678400)]
    inventory_max_age_seconds: Annotated[int, Field(strict=True, ge=1, le=2678400)]
    listing_units: Annotated[tuple[ListingUnitChoice, ...], Field(max_length=200)]
    stock_pools: Annotated[tuple[StockPoolChoice, ...], Field(max_length=200)]

    @field_validator("velocity_window_days", mode="before")
    @classmethod
    def strict_window(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("The demand window must be an integer.")
        return value

    @model_validator(mode="after")
    def unique_definitions(self) -> AmazonReadinessRequest:
        if len({item.listing_id for item in self.listing_units}) != len(self.listing_units) or len(
            {pool.pool_id for pool in self.stock_pools}
        ) != len(self.stock_pools):
            raise ValueError("Listing and pool definitions must be unique.")
        members = [
            (pool.channel, identifier)
            for pool in self.stock_pools
            for identifier in pool.listing_ids
        ]
        if len(set(members)) != len(members):
            raise ValueError("A listing can belong to one pool per fulfillment channel.")
        return self


class ReadinessIssue(PreviewModel):
    code: str
    listing_id: uuid.UUID | None = None
    pool_id: str | None = None


class ReadinessRun(PreviewModel):
    id: uuid.UUID
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None


class ReadinessListing(PreviewModel):
    listing_id: uuid.UUID
    seller_sku: str
    asin: str | None
    mapping_status: str
    mapping_method: str | None
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    base_units_per_listing_unit: int | None
    snapshot_id: uuid.UUID | None
    captured_at: datetime | None
    fba_sold_listing_units: int | None
    fbm_sold_listing_units: int | None


class ReadinessPool(PreviewModel):
    pool_id: str
    channel: Literal["FBA", "FBM"]
    listing_ids: tuple[uuid.UUID, ...]
    stock_source_listing_id: uuid.UUID
    sales_base_units: int | None
    on_hand_base_units: int | None
    observed_inbound_base_units: int | None
    usable_inbound_base_units: int | None
    quantity_input_ready: bool


class AmazonReadinessResponse(PreviewModel):
    rule_version: Literal["amazon-readiness-v1"] = "amazon-readiness-v1"
    data_source: Literal["ingested_postgresql"] = "ingested_postgresql"
    scope: Literal["known_product_listings"] = "known_product_listings"
    readiness_only: Literal[True] = True
    purchasing_eligible: Literal[False] = False
    confirmations_persisted: Literal[False] = False
    request: AmazonReadinessRequest
    evaluated_at: datetime
    demand_window_start: datetime
    demand_window_end: datetime
    status: Literal["DATA_AVAILABLE", "BLOCKED"]
    issues: tuple[ReadinessIssue, ...]
    latest_orders_run: ReadinessRun | None
    order_coverage_run_ids: tuple[uuid.UUID, ...]
    inventory_run: ReadinessRun | None
    listings: tuple[ReadinessListing, ...]
    pools: tuple[ReadinessPool, ...]
