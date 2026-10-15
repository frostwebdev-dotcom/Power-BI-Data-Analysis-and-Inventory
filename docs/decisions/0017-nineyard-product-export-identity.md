# 0017. Nineyard Products export identity and shared barcodes

- Status: Accepted
- Date: 2026-10-15

## Context

The client confirmed that Products export `Product ID` (column F) is the unique
business catalogue number. `ItemId` (A) is the numeric API identity, and `Upc`
(U) is the product barcode field. The API's `ItemMapping.vendorUPC` is a different,
vendor-level field with insufficient coverage. Cells can contain several comma-
separated barcodes, and the same valid barcode can belong to several products.

## Decision

Keep product UUIDs and all existing foreign keys. Add a tenant-unique nullable
`nineyard_item_id` and backfill only unambiguous retained API source links.
The CSV importer links by that identity (or an exact, unbound business catalogue
number), repairs the business number, adds checksum-valid barcode identifiers,
and never changes approved vendor or Amazon mappings. It blocks identity
collisions and refuses to infer an external identity from an unexplained numeric
catalogue number. Existing status is preserved; CSV-only products are inactive
until the API supplies lifecycle information. Omitted products are not removed.

Barcodes are unique per product, type and normalized value, rather than globally
per tenant. Non-barcode global identifiers remain unique. The matching index
retains all active product owners, and priority 1 reports ambiguity without
falling through to another rule. Inactive products cannot drive UPC matching.

The CLI defaults to a database dry run. Apply requires the reviewed file hash
and an operator label, retains exact original bytes and per-row source payloads,
and writes product/identifier audits in the same transaction as the changes.
Both API synchronization and CSV apply take the same tenant advisory lock.
Future API runs identify products by external ID and preserve business numbers,
master barcodes and export provenance. No file is sent to Nineyard.

## Consequences

The raw Products export is needed for initial barcode enrichment until a supported
product-level barcode API is confirmed. Existing valid barcodes are retained,
not deleted merely because an export omits them. Scientific notation, zero
placeholders, invalid checksums and incomplete codes remain diagnostic warnings.
The downgrade refuses to discard shared barcode ownership; resolve those rows
explicitly before downgrading to the former uniqueness rule.
