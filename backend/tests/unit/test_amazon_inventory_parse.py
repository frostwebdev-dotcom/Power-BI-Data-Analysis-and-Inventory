"""Parsing the merchant listings report and merging FBM quantities (ADR 0011).

The fixture follows Amazon's documented ``GET_MERCHANT_LISTINGS_ALL_DATA``
layout. It is a fixture, not an observation: the live report has not been
pulled yet (B8).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations.amazon import InventorySummary
from app.services.amazon_inventory import (
    LISTINGS_REQUIRED_COLUMNS,
    ListingsReportFormatError,
    ParsedListing,
    fbm_quantities,
    merge_snapshot_rows,
    parse_listings_report,
)

HEADER = (
    "item-name\titem-description\tlisting-id\tseller-sku\tprice\tquantity\topen-date"
    "\timage-url\titem-is-marketplace\tproduct-id-type\tzshop-shipping-fee\titem-note"
    "\titem-condition\tzshop-category1\tzshop-browse-path\tzshop-storefront-feature"
    "\tasin1\tasin2\tasin3\twill-ship-internationally\texpedited-shipping"
    "\tzshop-boldface\tproduct-id\tbid-for-featured-placement\tadd-delete"
    "\tpending-quantity\tfulfillment-channel\tmerchant-shipping-group\tstatus"
)


def row(
    sku: str,
    *,
    name: str = "Blue Widget, 12 pack",
    quantity: str = "5",
    id_type: str = "1",
    asin: str = "B000TEST01",
    product_id: str = "B000TEST01",
    channel: str = "AMAZON_NA",
    status: str = "Active",
) -> str:
    return "\t".join(
        [
            name, "", "0123456789ABC", sku, "19.99", quantity, "2026-01-01 00:00:00 PST",
            "", "y", id_type, "", "", "11", "", "", "", asin, "", "", "n", "n", "n",
            product_id, "n", "", "0", channel, "Migrated Template", status,
        ]
    )  # fmt: skip


FIXTURE_ROWS = [
    # an FBA row
    row("WIDGET-12", channel="AMAZON_NA", quantity="0"),
    # an FBM row
    row("GADGET-1", channel="DEFAULT", quantity="17", asin="B000TEST02", product_id="B000TEST02"),
    # an FBM row with a blank quantity
    row("BLANK-1", channel="DEFAULT", quantity="", asin="B000TEST03"),
    # an unknown fulfillment-channel value
    row("ODD-1", channel="SOMETHING_NEW", quantity="3", asin="B000TEST04"),
    # product-id-type 3 = UPC
    row("UPC-1", id_type="3", product_id="012345678905", asin="B000TEST05", channel="DEFAULT"),
    # a Latin-1 item name, FBM
    row("CAFE-1", name="Café Crème", channel="DEFAULT", quantity="2", asin="B000CAFE01"),
]
FIXTURE_TEXT = HEADER + "\n" + "\n".join(FIXTURE_ROWS) + "\n"
FIXTURE_UTF8 = FIXTURE_TEXT.encode("utf-8")
FIXTURE_LATIN1 = FIXTURE_TEXT.encode("latin-1")


def by_sku(listings: list[ParsedListing]) -> dict[str, ParsedListing]:
    return {listing.seller_sku: listing for listing in listings}


class TestListingsParsing:
    @pytest.fixture(params=["utf-8", "latin-1"])
    def content(self, request: pytest.FixtureRequest) -> bytes:
        return FIXTURE_UTF8 if request.param == "utf-8" else FIXTURE_LATIN1

    def test_every_row_is_seen_and_none_fails(self, content: bytes) -> None:
        result = parse_listings_report(content)

        assert (result.rows_seen, result.rows_failed) == (6, 0)
        assert len(result.listings) == 6

    def test_an_fba_row(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["WIDGET-12"]

        assert listing.fulfillment_channel == "AMAZON_NA"
        assert listing.is_fbm is False
        assert listing.quantity == 0
        assert listing.asin == "B000TEST01"
        assert listing.status == "Active"
        assert listing.item_name == "Blue Widget, 12 pack"

    def test_an_fbm_row(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["GADGET-1"]

        assert listing.fulfillment_channel == "DEFAULT"
        assert listing.is_fbm is True
        assert listing.quantity == 17

    def test_an_fbm_row_with_a_blank_quantity_is_none_not_zero(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["BLANK-1"]

        assert listing.is_fbm is True
        assert listing.quantity is None

    def test_an_unknown_channel_is_kept_verbatim_and_not_treated_as_fbm(
        self, content: bytes
    ) -> None:
        listing = by_sku(parse_listings_report(content).listings)["ODD-1"]

        assert listing.fulfillment_channel == "SOMETHING_NEW"
        assert listing.is_fbm is False

    def test_product_id_type_1_is_an_asin(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["WIDGET-12"]

        assert (listing.product_id_type, listing.product_id_kind) == (1, "ASIN")
        assert listing.product_id == "B000TEST01"

    def test_product_id_type_3_is_a_upc(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["UPC-1"]

        assert (listing.product_id_type, listing.product_id_kind) == (3, "UPC")
        assert listing.product_id == "012345678905"  # leading zero intact

    def test_a_latin1_item_name_loads(self, content: bytes) -> None:
        listing = by_sku(parse_listings_report(content).listings)["CAFE-1"]

        assert listing.item_name == "Café Crème"

    def test_raw_holds_exactly_the_required_columns(self, content: bytes) -> None:
        for listing in parse_listings_report(content).listings:
            assert set(listing.raw) == set(LISTINGS_REQUIRED_COLUMNS)


class TestListingsRowFailures:
    def test_an_unknown_product_id_type_is_kept_but_has_no_kind(self) -> None:
        result = parse_listings_report((HEADER + "\n" + row("X", id_type="9")).encode())

        [listing] = result.listings
        assert listing.product_id_type == 9
        assert listing.product_id_kind is None

    def test_a_blank_product_id_type_is_none(self) -> None:
        result = parse_listings_report((HEADER + "\n" + row("X", id_type="")).encode())

        assert result.listings[0].product_id_type is None

    def test_a_bad_quantity_fails_only_that_row(self) -> None:
        content = (HEADER + "\n" + row("A", quantity="lots") + "\n" + row("B")).encode()

        result = parse_listings_report(content)

        assert (result.rows_seen, result.rows_failed) == (2, 1)
        assert result.errors == [{"row": 1, "reason": "quantity is not an integer: 'lots'"}]
        assert [listing.seller_sku for listing in result.listings] == ["B"]

    def test_a_negative_quantity_is_rejected(self) -> None:
        result = parse_listings_report((HEADER + "\n" + row("A", quantity="-2")).encode())

        assert result.rows_failed == 1

    def test_a_blank_sku_is_rejected(self) -> None:
        result = parse_listings_report((HEADER + "\n" + row("  ")).encode())

        assert result.rows_failed == 1
        assert result.listings == []

    def test_a_missing_required_column_fails_the_report(self) -> None:
        header = HEADER.replace("\tfulfillment-channel", "")

        with pytest.raises(ListingsReportFormatError, match="fulfillment-channel"):
            parse_listings_report((header + "\n" + row("A")).encode())


# --- merging -------------------------------------------------------------------------


def summary(sku: str, **overrides: object) -> InventorySummary:
    values: dict[str, object] = {
        "seller_sku": sku,
        "asin": "B000TEST01",
        "fnsku": "X000TEST01",
        "condition": "NewItem",
        "last_updated": datetime(2026, 9, 15, 9, 30, tzinfo=UTC),
        "total": 205,
        "fulfillable": 40,
        "inbound_working": 100,
        "inbound_shipped": 50,
        "inbound_receiving": 10,
        "reserved_total": 2,
        "unfulfillable_total": 3,
        "researching_total": 0,
    }
    values.update(overrides)
    return InventorySummary(**values)  # type: ignore[arg-type]


class TestFbmQuantities:
    def test_only_default_channel_rows_count(self) -> None:
        listings = parse_listings_report(FIXTURE_UTF8).listings

        assert fbm_quantities(listings) == {
            "GADGET-1": 17,
            "BLANK-1": None,
            "UPC-1": 5,
            "CAFE-1": 2,
        }


class TestMerge:
    def test_fbm_quantity_is_merged_onto_the_fba_summary(self) -> None:
        listings = [
            ParsedListing(
                "WIDGET-12", "B000TEST01", 7, "DEFAULT", True, None, None, None, None, None, {}
            )
        ]

        merged = merge_snapshot_rows([summary("WIDGET-12")], listings)

        [row_] = merged.rows
        assert row_.fbm_quantity == 7
        assert row_.fulfillable == 40
        assert row_.inbound_working == 100
        assert merged.fbm_only == 0

    def test_a_summary_without_a_listing_has_no_fbm_quantity(self) -> None:
        merged = merge_snapshot_rows([summary("WIDGET-12")], [])

        assert merged.rows[0].fbm_quantity is None

    def test_an_fba_listing_does_not_set_fbm_quantity(self) -> None:
        listings = parse_listings_report(FIXTURE_UTF8).listings  # WIDGET-12 is AMAZON_NA

        merged = merge_snapshot_rows([summary("WIDGET-12")], listings)

        assert merged.rows[0].fbm_quantity is None

    def test_omitted_fba_quantities_become_zero_and_raw_keeps_none(self) -> None:
        merged = merge_snapshot_rows(
            [summary("S", fulfillable=None, inbound_working=None, reserved_total=None)], []
        )

        [row_] = merged.rows
        assert (row_.fulfillable, row_.inbound_working, row_.reserved_total) == (0, 0, 0)
        assert row_.raw["fulfillable"] is None
        assert row_.raw["source"] == "fba_inventory"

    def test_an_fbm_only_sku_gets_a_snapshot_with_zero_fba_quantities(self) -> None:
        listings = parse_listings_report(FIXTURE_UTF8).listings

        merged = merge_snapshot_rows([summary("WIDGET-12")], listings)

        rows = {row_.seller_sku: row_ for row_ in merged.rows}
        # GADGET-1, BLANK-1, UPC-1 and CAFE-1 are FBM; only WIDGET-12 came from FBA.
        assert set(rows) == {"WIDGET-12", "GADGET-1", "BLANK-1", "UPC-1", "CAFE-1"}
        assert merged.fbm_only == 4
        gadget = rows["GADGET-1"]
        assert gadget.fbm_quantity == 17
        assert gadget.asin == "B000TEST02"
        assert (gadget.fulfillable, gadget.inbound_working, gadget.inbound_shipped) == (0, 0, 0)
        assert gadget.raw["source"] == "listings_report"
        assert rows["BLANK-1"].fbm_quantity is None

    def test_an_fbm_only_listing_without_an_asin_is_a_failed_row(self) -> None:
        listings = [
            ParsedListing("NOASIN", None, 3, "DEFAULT", True, None, None, None, None, None, {})
        ]

        merged = merge_snapshot_rows([], listings)

        assert merged.rows == []
        assert merged.rows_failed == 1
        assert merged.errors == [{"seller_sku": "NOASIN", "reason": "FBM-only listing has no ASIN"}]

    def test_a_duplicate_fba_summary_is_a_failed_row_not_a_unique_violation(self) -> None:
        merged = merge_snapshot_rows([summary("S"), summary("S")], [])

        assert len(merged.rows) == 1
        assert merged.rows_failed == 1
        assert merged.errors[0]["reason"] == "duplicate FBA summary"

    def test_a_summary_without_an_asin_is_a_failed_row(self) -> None:
        merged = merge_snapshot_rows([summary("S", asin=None)], [])

        assert merged.rows == []
        assert merged.errors[0]["reason"] == "FBA summary has no ASIN"


def test_merged_fba_fbm_snapshot_retains_merchant_quantity_evidence() -> None:
    listings = parse_listings_report(FIXTURE_UTF8).listings
    merged = merge_snapshot_rows([summary("GADGET-1", asin="B000TEST02")], listings)
    result = next(value for value in merged.rows if value.seller_sku == "GADGET-1")
    assert result.raw["source"] == "fba_inventory"
    assert result.raw["listing"]["fulfillment-channel"] == "DEFAULT"
    assert result.raw["listing"]["quantity"] == "17"
    assert result.fbm_quantity == 17
