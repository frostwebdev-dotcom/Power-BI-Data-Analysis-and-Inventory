# Vendor onboarding — November 5, 2026

Status: deployed to Azure staging; all eight new vendors are imported and
reconciled. Client acceptance and catalog exception review remain outstanding.

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

## Verified staging results — November 5, 2026

The eight vendor records and nine active, header-pinned version-1 profiles were
created through the audited application services for `global-supplies`. The
original client workbooks were uploaded through the signed-in staging UI.

| Vendor / worksheet | Product rows | Unique offers / snapshots | Matched | Matching review | Errors | Import ID |
|---|---:|---:|---:|---:|---:|---|
| MB / Price List | 721 | 720 | 169 | 551 | 0 | `1d7387d2-2957-43b5-b729-1d5b3010db26` |
| OWD / Sheet1 | 630 | 630 | 52 | 578 | 0 | `ca564da9-ad4e-4d8b-91d0-c35d5b4cc925` |
| QFG / QUANTITY PRICING | 456 | 456 | 55 | 401 | 0 | `cff0fe4f-e295-4646-a707-6461d3282966` |
| Spectrum / Designer Brands | 149 | 149 | 33 | 116 | 0 | `03926e3f-5846-48a6-b045-5cd9bd9aa893` |
| Spectrum / Perfumora Niche Brands | 120 | 120 | 0 | 120 | 0 | `496f475d-6e6c-407d-91e9-7eeba34dfe2e` |
| Premier / Items | 417 | 417 | 110 | 307 | 0 | `bbe0134c-be91-499b-8b40-209c6d4cb5c9` |
| Elegance / Sheet1 | 1,080 | 1,068 | 111 | 957 | 0 | `765f062d-0dc7-499d-a755-ee8c2a9a56a6` |
| Krisco / OFFER | 176 | 176 | 48 | 128 | 0 | `59b16ddb-b75c-46d2-97ef-a6ec7a886e49` |
| HAZ / PRICE LIST | 931 | 931 | 128 | 803 | 0 | `be0378f1-6fa7-41b1-9349-a5b60f9b8ca4` |
| **Total** | **4,680** | **4,667** | **706** | **3,961** | **0** | **9 completed imports** |

Verification used fresh database sessions and reopened the retained raw objects:

- All eight stored workbooks reproduce their original byte counts and SHA-256
  digests. Spectrum's two jobs reference one retained file and distinct actual
  worksheet titles; the image-link worksheet was not imported.
- All 4,667 persisted snapshots match their effective staged row's price,
  quantity, currency and availability, with zero mismatches. All are AVAILABLE
  under the client's listed-stock instruction. Prices remain per each, USD,
  without a QKD discount, case conversion or bulk-tier substitution.
- 1,515 unique offers have an unknown exact quantity: OWD 630, Spectrum niche
  120, Premier 284, Elegance 341 and Krisco 140. Missing counts and lower bounds
  were not changed into zero or a fabricated exact quantity.
- The one repeated MB offer and twelve repeated Elegance offers retain both
  source rows with the existing last-row-wins duplicate warning; they produce
  thirteen fewer snapshots than product rows. No repeated stock records were
  created.
- The UI's 6,421 total source rows include 1,741 skipped section/formatting rows;
  product counts above exclude these skips. All physical worksheet row numbers
  and mapped raw values remain available in the import reports.
- No flagged shared-UPC offer was automatically matched by UPC. The 3,961
  unresolved offers remain in the catalog exception queue with stock recorded;
  no uncertain mapping was manually approved or invented.

Backend, PostgreSQL integration/migration, frontend, Windows storage and Compose
browser walkthrough checks passed. Staging deployment run `42093911544` (#103)
deployed commit `e38018ff1e136a70fcc5a5bb7c2be5b49b357cd4`, including migration
`a61105202601`. Applying the migration preserved all existing file, job, staged
row and snapshot counts. PRs #14 and #15 contain the importer changes; PR #16
also clears the native upload input after success so the same original workbook
can be selected for another worksheet without reloading. PR #16's full CI run
`42096114027` passed.

The earlier Universal import remains completed with 4,282 snapshots, 443 matched
rows, 3,839 matching exceptions and zero errors. This batch does not resolve the
older ET, French or MTZ source-data errors or establish formal Milestone 1
acceptance; Pinchas still needs to review the workflow and remaining exceptions.
