"""Catalogue presentation helpers; raw listing names are display-only."""

from __future__ import annotations

from app.models.catalog import MarketplaceListing
from app.schemas.products import ListingResponse


def listing_response(listing: MarketplaceListing) -> ListingResponse:
    response = ListingResponse.model_validate(listing)
    name = listing.raw.get("item-name")
    response.name = name.strip() if isinstance(name, str) and name.strip() else None
    return response
