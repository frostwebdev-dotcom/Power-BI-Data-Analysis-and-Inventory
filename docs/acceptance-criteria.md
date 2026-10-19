# Acceptance Criteria — Milestone 1

Status: **Proposed — awaiting sign-off.**
Last updated: 2026-09-07

Each criterion is verifiable by a named automated test, a manual check, or both.
`AC-n.m` identifiers are stable and are referenced from commit messages and from
[phase1-status.md](phase1-status.md).

Legend: **[A]** automated test · **[M]** manual verification · **[A+M]** both.

---

## AC-0 — Foundation and environment

| ID | Criterion | Verify |
|---|---|---|
| AC-0.1 | `docker compose up` from a clean checkout brings up PostgreSQL, api, worker, and web with no manual steps beyond copying `.env.example` to `.env`. | [M] |
| AC-0.2 | The application reads **every** secret and connection setting from environment variables. Grepping the repository finds no committed credential, token, or connection string. | [A+M] |
| AC-0.3 | `alembic upgrade head` on an empty database produces the full schema; `alembic downgrade base` reverses it without error. | [A] |
| AC-0.4 | CI runs ruff, mypy, pytest, eslint, `tsc --noEmit`, and the frontend build, and fails the pipeline on any failure. | [A] |
| AC-0.5 | `GET /api/v1/health` returns 200 with database connectivity confirmed. | [A] |
| AC-0.6 | Every route is served under `/api/v1`. No route exists outside a version prefix except `/health` infrastructure probes and OpenAPI docs. | [A] |

## AC-1 — PostgreSQL database foundation

| ID | Criterion | Verify |
|---|---|---|
| AC-1.1 | Every table has a UUID primary key that is generated server-side and never reused. | [A] |
| AC-1.2 | Every timestamp column is `TIMESTAMPTZ`; a test asserts no `TIMESTAMP WITHOUT TIME ZONE` column exists anywhere in the schema. | [A] |
| AC-1.3 | All timestamps are written in UTC. A test with a non-UTC session timezone confirms stored values are unaffected. | [A] |
| AC-1.4 | Foreign keys reference UUID primary keys only; no foreign key targets a business key such as Catalog Item Number or vendor SKU. | [A] |
| AC-1.5 | Uniqueness is enforced at the database level (not only in application code) for: `product.nineyard_catalog_item_number`, `vendor.code`, `import_file.sha256`, `vendor_product(vendor_id, vendor_sku)`, and one approved `vendor_product_mapping` per vendor product. | [A] |
| AC-1.6 | Constraint and index names follow a declared naming convention so Alembic autogenerate is stable. | [A] |

## AC-2 — Nineyard API integration

| ID | Criterion | Verify |
|---|---|---|
| AC-2.1 | Nineyard credentials are supplied only by environment variables and never logged, including at DEBUG level. | [A+M] |
| AC-2.2 | The client retries on 429 and 5xx with exponential backoff and jitter, honors `Retry-After`, and gives up after a configured maximum. | [A] |
| AC-2.3 | Pagination is followed to completion; a test with a 3-page fixture returns all items exactly once. | [A] |
| AC-2.4 | A network failure mid-sync marks the `nineyard_sync_run` as `failed` with an error summary and leaves previously written products intact and consistent. | [A] |
| AC-2.5 | No Nineyard-specific field name appears in `app/models/`. Translation happens in the anti-corruption layer. | [A+M] |

## AC-3 — Catalog synchronization

| ID | Criterion | Verify |
|---|---|---|
| AC-3.1 | A sync creates `product` rows keyed on Nineyard Catalog Item Number, each with an internal UUID. | [A] |
| AC-3.2 | Re-running a sync with unchanged upstream data creates no duplicate products and reports the items as `unchanged`. | [A] |
| AC-3.3 | A changed upstream field updates the product, preserves the UUID, and writes an audit entry with before/after values. | [A] |
| AC-3.4 | The raw payload for every item is retained in `nineyard_item_payload` with a checksum, and is byte-comparable to the API response. | [A] |
| AC-3.5 | An item that disappears upstream is soft-deactivated, never hard-deleted, and existing mappings that reference it are flagged for review rather than broken. | [A] |
| AC-3.6 | `nineyard_sync_run` records accurate counts for seen / created / updated / unchanged / removed. | [A] |
| AC-3.7 | Sync is runnable on demand from the admin interface and on a schedule. | [A+M] |

## AC-4 — Vendor database

| ID | Criterion | Verify |
|---|---|---|
| AC-4.1 | A vendor can be created, read, updated, and deactivated through `/api/v1/vendors`. | [A] |
| AC-4.2 | Vendor `code` uniqueness is enforced and a duplicate returns a 409 with a typed error body, not a 500. | [A] |
| AC-4.3 | Vendors are deactivated, never hard-deleted, while import history exists. | [A] |
| AC-4.4 | Every vendor mutation produces an audit entry naming the acting user. | [A] |

## AC-5 — Vendor inventory imports (CSV and XLSX)

| ID | Criterion | Verify |
|---|---|---|
| AC-5.1 | A CSV file imports end to end and produces one `import_row` per source data row, with `source_row_number` matching the file's own line numbering. | [A] |
| AC-5.2 | An XLSX file imports end to end with the same guarantees, honoring the profile's sheet selection. | [A] |
| AC-5.3 | The stored raw file is **byte-identical** to the upload: SHA-256 of the retrieved object equals SHA-256 of the source file. | [A] |
| AC-5.4 | The raw file is written before parsing begins, so a parse crash still leaves the original retained. | [A] |
| AC-5.5 | A UPC of `012345678905` in a CSV, and the same value stored as a number in an XLSX cell, both round-trip without loss of leading zeros and without scientific notation. | [A] |
| AC-5.6 | Re-uploading a byte-identical file is detected by checksum and requires explicit confirmation rather than silently creating a second batch. | [A] |
| AC-5.7 | A batch interrupted by a worker restart resumes and completes without duplicating rows. | [A] |
| AC-5.8 | Files in UTF-8, UTF-8-with-BOM, and Windows-1252 all import correctly, with the detected encoding recorded on `import_file`. | [A] |

## AC-6 — Vendor-specific import profiles

| ID | Criterion | Verify |
|---|---|---|
| AC-6.1 | A new vendor's file can be onboarded by creating a profile through the admin UI, with **no code change and no deployment**. | [A+M] |
| AC-6.2 | Profiles are versioned; each `import_batch` records the exact profile version used. | [A] |
| AC-6.3 | Editing a profile creates a new version and leaves prior batches interpretable under the version they ran with. | [A] |
| AC-6.4 | An incoming file whose header does not match the profile's `header_signature` fails the batch immediately with a clear diff of expected vs. observed columns, rather than mapping columns positionally. | [A] |
| AC-6.5 | Profile changes are audited. | [A] |

## AC-7 — Deterministic product matching

| ID | Criterion | Verify |
|---|---|---|
| AC-7.1 | Matching evaluates the priority chain in the order UPC → Catalog Item Number → approved vendor SKU mapping → approved Amazon SKU mapping → controlled suggestion, and stops at the first rule returning exactly one product. | [A] |
| AC-7.2 | Every matched row records which rule fired (`matched_by`, `rule_priority`) and the full evaluation trail in `match_attempt`. | [A] |
| AC-7.3 | Running the same import twice against unchanged mapping state produces identical outcomes and identical rule attribution for every row. | [A] |
| AC-7.4 | A rule that returns more than one product yields `ambiguous` and an exception. It does **not** fall through to a lower-priority rule and does **not** select a candidate. | [A] |
| AC-7.5 | **No code path auto-matches on product description alone.** A row whose only signal is a description similarity always ends as `suggestion_only` and enters the exception queue. Enforced by a dedicated test. | [A] |
| AC-7.6 | UPC normalization strips non-digits, expands UPC-E, validates the check digit, and compares in canonical GTIN-14 form. Values differing only in leading zeros match; a value with an invalid check digit does not match at priority 1 and produces a warning. | [A] |
| AC-7.7 | A row with no usable signal ends as `unmatched` and enters the exception queue. Nothing is silently dropped. | [A] |
| AC-7.8 | Matching never writes a mapping automatically. Mappings originate only from human approval. | [A] |

## AC-8 — Product-mapping exception workflow

| ID | Criterion | Verify |
|---|---|---|
| AC-8.1 | Every `ambiguous`, `unmatched`, and `suggestion_only` row produces exactly one `match_exception` with a reason code. | [A] |
| AC-8.2 | A reviewer sees the source row as imported, the vendor's values, and each candidate with the reason it was suggested. | [A+M] |
| AC-8.3 | Approving an exception creates a permanent `vendor_product_mapping` (or approved `amazon_sku`) recording approver and UTC timestamp. | [A] |
| AC-8.4 | On the **next** import of the same vendor SKU, the row matches automatically at priority 3 (or 4) and creates no new exception. | [A] |
| AC-8.5 | Rejecting an exception records the decision and does not create a mapping; the row is not re-queued unchanged on the next identical import without an explicit re-review policy. | [A] |
| AC-8.6 | An approved mapping is not overwritten, expired, or recomputed by any later automated run. It changes only through an explicit, audited human action that records supersession. | [A] |
| AC-8.7 | Every approve / reject / defer / supersede action is audited with actor and before/after state. | [A] |

## AC-9 — Import validation and reporting

| ID | Criterion | Verify |
|---|---|---|
| AC-9.1 | Validation issues carry a severity (error / warning / info), a stable machine-readable code, the offending field, and the source row number. | [A] |
| AC-9.2 | A row-level error does not abort the batch; the row is marked and the batch continues. A file-level error (unreadable file, header mismatch) fails the batch immediately. | [A] |
| AC-9.3 | Each completed batch produces an immutable `import_report` with totals, outcome breakdown, matched-by-rule counts, issue counts by code, exception count, and availability-event count. | [A] |
| AC-9.4 | Report figures reconcile exactly: matched + exception + unmatched + skipped equals the parsed row count. | [A] |
| AC-9.5 | An error listing is downloadable and every entry traces back to its source row number in the retained raw file. | [A+M] |
| AC-9.6 | Batch status and the report are visible in the admin UI while the import is running and after it completes. | [M] |

## AC-10 — OOS watchlist

| ID | Criterion | Verify |
|---|---|---|
| AC-10.1 | A user can add a product or a specific vendor line to the watchlist with a reason, and the entry records who added it and when. | [A] |
| AC-10.2 | Watch entries are deactivated rather than deleted, preserving history. | [A] |
| AC-10.3 | The watchlist is filterable by vendor, product, and active state in the admin UI. | [M] |
| AC-10.4 | Watchlist add and remove actions are audited. | [A] |

## AC-11 — Availability transition detection

| ID | Criterion | Verify |
|---|---|---|
| AC-11.1 | Each import writes a `vendor_inventory_snapshot` per vendor product with quantity, cost, and canonical availability status. | [A] |
| AC-11.2 | Importing a file where a previously out-of-stock item now has stock produces exactly one `became_available` event referencing both snapshots. | [A] |
| AC-11.3 | The reverse transition produces exactly one `became_unavailable` event. | [A] |
| AC-11.4 | No event is emitted when the status is unchanged, and re-processing the same batch produces no duplicate events. | [A] |
| AC-11.5 | A vendor value not covered by the profile's `availability_rule` maps to `unknown` with a warning — never to `available`. | [A] |
| AC-11.6 | An availability event on a watched item creates a `watchlist_hit` and is surfaced prominently in the admin UI. | [A+M] |
| AC-11.7 | A transition on an unmatched vendor product is still recorded, with a null `product_id`, so signal is not lost while the mapping is pending. | [A] |
| AC-11.8 | The availability feed can be searched by catalog number, product name, vendor SKU, or exact UPC/GTIN. Search is tenant-scoped, applies before pagination, composes with vendor/watch/date filters, and resets the UI to page 1. The empty state explains that products without a recorded availability change are not listed. | [A+M] |

## AC-12 — Audit logging

| ID | Criterion | Verify |
|---|---|---|
| AC-12.1 | An audit entry exists for every mutation listed in [CLAUDE.md §6](../CLAUDE.md), asserted by an integration test that exercises each mutating endpoint. | [A] |
| AC-12.2 | Each entry records actor (or `system` / `worker`), action, entity type, entity UUID, before and after state, UTC timestamp, and request correlation id. | [A] |
| AC-12.3 | Audit rows are append-only: the API exposes no update or delete path, and a test asserts attempts fail. | [A] |
| AC-12.4 | An audit write and the change it describes commit or roll back together — a rolled-back transaction leaves no audit row. | [A] |
| AC-12.5 | The audit log is browsable and filterable by entity, actor, action, and date range in the admin UI. | [M] |
| AC-12.6 | No secret, password hash, or credential appears in any audit payload. | [A] |

## AC-13 — Minimal administration interface

| ID | Criterion | Verify |
|---|---|---|
| AC-13.1 | Screens exist for vendors, import profiles, import upload and batch status, batch reports, the exception queue, product lookup, watchlist, availability events, and the audit log. | [M] |
| AC-13.2 | The app is TypeScript with no `any` in application code; `tsc --noEmit` and `eslint` pass clean. | [A] |
| AC-13.3 | API types are generated from the FastAPI OpenAPI schema, and CI fails if the committed types are stale. | [A] |
| AC-13.4 | A Playwright e2e test completes the full path: create vendor → create profile → upload file → view report → resolve an exception → re-import → row now auto-matches. | [A] |
| AC-13.5 | Approval actions are available only to users with an authorized role. | [A] |
| AC-13.6 | All timestamps shown in the UI are rendered from stored UTC with the timezone made explicit. | [A+M] |

---

## Milestone-level exit gate

Milestone 1 is complete when:

1. All **[A]** criteria pass in CI on a clean checkout.
2. All **[M]** criteria are demonstrated in a walkthrough against real data from
   at least two vendors with materially different file formats — one CSV, one
   XLSX.
3. [phase1-status.md](phase1-status.md) shows every phase complete with no open
   blocking questions.
4. No item from [milestone-1-scope.md §3](milestone-1-scope.md) has been built.
