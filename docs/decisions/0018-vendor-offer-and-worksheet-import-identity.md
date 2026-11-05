# ADR 0018 — Vendor offers and worksheet import identity

Date: 2026-11-05
Status: Accepted for implementation; staging verification pending.

## Context

The November supplier batch includes shared barcodes on clean, tester,
damaged-box and multipack offers, missing barcodes, section headings, and two
different inventory layouts in one Spectrum workbook. A UPC-only vendor key
silently supersedes distinct offers. File-only job idempotency prevents
importing a second worksheet without modifying the retained original.

## Decision

1. Add an opt-in `vendor_sku_from_offer` profile rule. With no supplier SKU,
   `OFFER:<SHA256>` is computed from normalized barcode, description, optional
   offer variant, pack size and unit fields. Valid barcodes use canonical GTIN;
   invalid/missing ones remain invalid/missing for catalog matching. The key
   excludes quantities, prices and row positions. Existing supplier SKUs take
   precedence. Normalization collapses whitespace/case and uses Unicode NFKC;
   meaning-changing punctuation is retained. Profile versions preserve these
   semantics. Changes to source descriptions/variants may create a new vendor
   offer; the generated key must never be described as a supplier-provided SKU.
2. `review_shared_upc_offers` flags distinct derived offers sharing a valid
   barcode. Their original barcode/checksum remain in the row and search; UPC
   priority is skipped for those rows. Approved vendor mappings can still
   decide a match, and all remaining uncertainty enters the existing queue.
   Descriptions never automatically identify canonical products.
3. `skip_non_product_rows` explicitly skips a row only when mapped description,
   quantity and price are all blank. Its raw mapped values, physical row
   number, skipped status and reason remain in the report. Legacy defaults do
   not enable any of these rules.
4. New Excel jobs record `source_sheet`, resolved against the actual workbook
   so index/name aliases have one identity. The active-job index is scoped by
   organization, raw file and resolved sheet. Independent sheet profiles create
   independent jobs over the same immutable raw file. Re-uploading a sheet
   returns its existing job. Historical NULL-sheet jobs retain whole-file
   deduplication in the receiving service; cross-vendor byte reuse is refused.
5. Run the synchronous upload pipeline as a synchronous FastAPI route, in the
   framework worker pool. Reading bounded upload bytes, SQLAlchemy, parsing,
   matching and snapshots must not monopolize the event loop or liveness probe.

## Consequences

- Spectrum is two pinned profiles/jobs, one original workbook, with each
  worksheet's physical row numbering unchanged and named on the report/list.
- Missing source identifiers remain reviewable offers without invented UPCs.
- The existing per-tenant raw checksum, immutable UUIDs, mapping approvals,
  prices, quantity semantics and audit services remain authoritative.
- Downgrade refuses to discard multi-sheet history merely to restore the old
  unique index. A rollback after multi-sheet imports needs an explicit data
  retention plan.
- This is bounded ingestion support, not a price-tier engine, catalog cleanup
  policy, purchasing calculation or supplier-specific executable importer.
