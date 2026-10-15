"""Business IDs, multi-barcode cells and hostile/malformed catalogue input."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from app.cli.nineyard_catalogue_import import main
from app.integrations.nineyard.catalogue_csv import read_catalogue_export


def csv_bytes(*rows: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["ItemId", "Product ID", "Title", "Upc"])
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def test_business_id_is_not_internal_id_and_multiple_barcodes_keep_leading_zeros() -> None:
    content = csv_bytes(["60046", "ABC-001", "A product", "012345678905, 036000291452"])
    export = read_catalogue_export(content)
    row = export.rows[0]
    assert (row.item_id, row.catalog_item_number) == ("60046", "ABC-001")
    assert [(b.raw, b.canonical) for b in row.barcodes] == [
        ("012345678905", "00012345678905"),
        ("036000291452", "00036000291452"),
    ]
    assert row.raw["Upc"] == "012345678905, 036000291452"
    assert export.counts["rows_multiple_barcodes"] == 1
    assert export.errors == ()


@pytest.mark.parametrize("value", ["0", "000000000000", "8.4667E+11", "012345678904", "031971"])
def test_unusable_barcodes_are_reported_without_inventing_digits(value: str) -> None:
    export = read_catalogue_export(csv_bytes(["1", "ABC", "Product", value]))
    assert not export.rows[0].barcodes
    assert export.issues[0]["raw"] == value
    assert export.issues[0]["code"] == "UPC_INVALID"
    assert export.counts["invalid_barcode_values"] == 1


def test_invalid_barcode_does_not_discard_other_valid_barcodes() -> None:
    export = read_catalogue_export(csv_bytes(["1", "ABC", "Product", "0,012345678905"]))
    assert len(export.rows[0].barcodes) == 1
    assert len(export.issues) == 1


def test_shared_barcode_is_retained_for_both_products() -> None:
    export = read_catalogue_export(
        csv_bytes(
            ["1", "ABC", "First", "012345678905"],
            ["2", "DEF", "Second", "012345678905"],
        )
    )
    assert export.counts["shared_barcodes"] == 1
    assert all(row.barcodes for row in export.rows)


@pytest.mark.parametrize(
    "second",
    [
        ["1", "DEF", "Duplicate internal ID", ""],
        ["2", " abc ", "Duplicate business ID", ""],
        ["", "DEF", "Missing ID", ""],
        ["2", "", "Missing business ID", ""],
    ],
)
def test_duplicate_or_missing_identity_blocks_apply(second: list[str]) -> None:
    export = read_catalogue_export(csv_bytes(["1", "ABC", "First", ""], second))
    assert export.errors


def test_column_order_is_not_assumed() -> None:
    export = read_catalogue_export(b"Title,Upc,Product ID,ItemId\nProduct,012345678905,ABC,1\n")
    assert export.rows[0].catalog_item_number == "ABC"
    assert export.rows[0].barcodes[0].raw == "012345678905"


def test_wrong_width_and_duplicate_headers_are_not_silently_accepted() -> None:
    assert read_catalogue_export(b"ItemId,Product ID,Title,Upc\n1,ABC\n").errors
    with pytest.raises(ValueError, match="unique headers"):
        read_catalogue_export(b"ItemId,Product ID,Title,Upc,Upc\n")


def test_cli_file_only_needs_no_database_and_apply_requires_review_hash(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(csv_bytes(["1", "ABC", "First", "012345678905"]))
    assert main(["--file", str(path), "--file-only"]) == 0
    assert main(["--file", str(path), "--file-only", "--expected-sha256", "wrong"]) == 1
    with pytest.raises(SystemExit):
        main(["--file", str(path), "--org-slug", "tenant", "--apply"])
