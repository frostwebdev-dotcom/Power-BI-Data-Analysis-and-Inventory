# Vendor onboarding — November 5, 2026

Status: implementation in progress; the eight new vendors are not yet imported.

The client assigned the new files to MB, OWD, QFG, Spectrum, Premier,
Elegance, Krisco and HAZ. Original workbooks must be retained unchanged.

## Required behavior

- Profiles may explicitly derive a vendor offer key from the source barcode,
  description and offer variant when no supplier SKU exists. Price, quantity
  and row position must not affect this key. This identifies a vendor offer,
  never a canonical catalog product. Description-only automatic matching stays
  prohibited. Missing or invalid barcodes remain visible for review.
- Reused barcodes on distinct derived offers must be flagged for human review;
  they must not collapse vendor lines or silently assign the wrong pack/tester.
  Existing approved vendor mappings may still resolve those offers.
- An opt-in profile rule skips section headings only when description,
  quantity and price are all blank. Skips retain source row numbers and raw
  mapped values. Default profiles retain their current behavior.
- Independent named worksheets use separate profiles/jobs but share one
  immutable retained workbook. A repeated upload of the same worksheet returns
  its existing job, including when selected by index instead of name. Existing
  historical file-level jobs continue to block duplicate processing.
- The synchronous database/import pipeline runs in FastAPI's worker pool,
  allowing health checks and other requests while an upload is processed.

## File rules

| Vendor | Products before cleanup | Profile requirements |
|---|---:|---|
| MB | 721 | UPC/description offer keys; comma thousands; skip section headings |
| OWD | 630 | Item ID; UPC/SKU; Unit Price; Units Per Case; no stock count. Never use order quantity as stock. |
| QFG | 456 | QK#; UPC; DESCRIPTION; QTY; base PRICE. Preserve bulk tiers and casepack in original source. |
| Spectrum | 149 + 120 | Designer Brands and Perfumora Niche Brands use independent profiles. Exclude Images_URl. |
| Premier | 417 | Offer keys, including non-barcode accessory codes; lower bounds remain unknown exact counts |
| Elegance | 1080 | Offer keys; preserve missing/invalid and shared UPC exceptions |
| Krisco | 176 | Offer keys; lower bounds remain unknown exact counts |
| HAZ | 931 | Offer keys include condition column; skip section headings; review shared barcodes |

Quantities/prices are kept as stated. No case conversion or purchasing decision
is introduced. Use listed availability under the client's existing instruction;
absent and lower-bound counts remain unknown. No QKD 12% discount applies.

## Verification and rollout

1. Unit tests: stable offer identity, shared-barcode review, heading skips,
   malformed identifiers, price/quantity changes and legacy defaults.
2. PostgreSQL tests: separate worksheets, same-sheet duplicate detection,
   legacy jobs, tenant/vendor boundaries and migration consistency.
3. API concurrency regression: a blocked upload must not block health requests.
4. Existing lint, typing, unit/integration and frontend/build checks.
5. Deploy through the existing staging pipeline; onboard with versioned pinned
   profiles; reconcile every import, snapshot and source hash.
6. Document source-data exceptions without inventing identifiers or approving
   uncertain catalog mappings. Client acceptance remains outstanding.
