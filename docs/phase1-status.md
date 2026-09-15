# Phase 1 Status

Living document. It reflects **what is true**, not what is intended.
Last updated: 2026-09-15 (phase 6 — parsing, row validation and the import report)

---

## 1. Overall

| | |
|---|---|
| Milestone | 1 — Data foundation, ingestion, matching |
| Stage | **Phase 1 complete; phase 3 diagnostic built.** Schema, audit service, configuration/security foundation, and a read-only Nineyard probe exist. Backend is deployed to Railway. |
| Application code | Schema, audit service, auth foundation, error handling, redaction, read-only Nineyard client + CLI probe, tenant scoping helper for repositories (ADR 0012), Amazon SP-API configuration, read-only SP-API client, the three ingestion services, the listings→product mapping, the shared identifier normaliser, the sales-velocity service, an APScheduler runner behind a `JobRunner` protocol, the `amazon_poc` CLI, the vendor database API, versioned import profiles with typed rule shapes, CSV/XLSX readers and a validate-against-sample preview (ADR 0013), file upload with byte-identical raw retention behind a `StorageBackend` (ADR 0004), and profile-driven parsing of CSV/XLSX into `import_job_rows` with coded per-row validation and the import report. Parsed jobs wait at stage MATCHING; nothing yet matches a row to a product. |
| Database schema | 25 tables, 23 enum types, 135 indexes, 82 check constraints, 83 foreign keys, 1 append-only trigger |
| Migrations | 5 revisions (`506fd0ecc33a`, `3767ee979011`, `3de5c4e5def0`, `44c932e601b0`, `fadb756b1b85`), applied and reversed against PostgreSQL 16.15 |
| Backend tests | **1033 passed** (`pytest`: 681 unit + 352 integration). `grep -c "def test_"` over `tests/` finds 776 functions (468 unit, 308 integration — one of which is the `test_database_url` fixture helper in `conftest.py`); the difference is parametrisation. |
| Quality gates | 8 of 8 passing locally (§4, 2026-09-15); the same gates were green in GitHub Actions on 2026-09-14 and `main` has not been pushed since |
| Docker stack | **Verified in CI** — full `docker compose up --build` from `.env.example`, API healthy against PostgreSQL, migration applied and checked, web answering (§4, §6 S1). Backend also live on Railway (§6 S2). |
| Blocking questions open | 8 (see §7); B1 partially answered, B2 narrowed, B7 partially answered by ADR 0011, B8 new |

The schema, the audit writer, the security foundation, and a read-only Nineyard
diagnostic exist and are verified. A vendor file can be uploaded, retained byte for
byte, parsed through its profile into validated rows, and reported on;
nothing yet matches a vendor row to a product or synchronises Nineyard data.

---

## 2. Phase tracker

Phases are defined in [architecture.md §6](architecture.md#6-implementation-order).

| # | Phase | Status | Notes |
|---|---|---|---|
| — | Planning and documentation | ✅ Complete | Scope, architecture, criteria, 13 ADRs |
| 0 | Scaffolding | ✅ Complete | Backend, frontend, infra, quality gates, GitHub Actions CI (2026-09-14) |
| 1 | DB foundation + audit | ✅ Complete | Schema, migration, transactional audit writer, transaction utilities, config/security foundation |
| A | Amazon SP-API read-only ingestion ([ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)) | 🟨 Complete against fakes | Precedes phase 2 by client request (§10). **Exists:** settings, redaction, the read-only client, the tables, the three ingestions, listings→product mapping, the velocity service, scheduled jobs with stale-run recovery, and the `amazon_poc` CLI ([amazon-integration.md](amazon-integration.md)). **Does not exist:** the read-only velocity HTTP endpoint named in the ADR. **Not yet done:** a single run against the real seller account (B8) — the one thing the client asked to see. |
| 2 | Vendor database | ✅ Complete | `GET/POST /api/v1/vendors`, `GET/PATCH /vendors/{id}`, `POST /vendors/{id}/deactivate`, and the same shape under `/vendors/{id}/contacts`. Reads for every role, writes for `DATA_OPERATOR`; every mutation audited in its own transaction; a vendor with active import profiles cannot be deactivated. AC-4.1–4.4 covered by 35 route tests. |
| 3 | Nineyard integration + sync | 🟨 Diagnostic only | **Exists:** read-only client (`app/integrations/nineyard/client.py`, `errors.py`, `sanitize.py`), probe (`app/integrations/nineyard/probe.py`), and CLI (`app/cli/nineyard_probe.py`), tested by `tests/unit/test_nineyard_client.py`, `test_nineyard_probe.py`, `test_nineyard_cli.py` (mocked; no live calls). The public OpenAPI spec has been analysed ([nineyard-field-mapping.md](nineyard-field-mapping.md)). **Does not exist:** any sync service — nothing writes Nineyard data to `products`, `product_identifiers`, `marketplace_listings`, `nineyard_sync_runs`, or `nineyard_item_payloads`. The probe has not been run against the live API. See [nineyard-integration.md](nineyard-integration.md) and B1. |
| 4 | Import profiles | ✅ Complete | The six JSONB rule columns have fixed Pydantic shapes with JSON Schema export ([ADR 0013](decisions/0013-import-profile-rule-shapes.md)); `GET/POST /vendors/{id}/import-profiles`, `GET/PATCH …/{profile_id}`, `POST …/deactivate`, `POST …/validate` (stored and draft) and `GET /import-profiles/rule-schemas`. Editing creates version n+1 and retires n, audited. AC-6.1–6.4 covered by 46 route tests and 70 rule/reader/mapper tests. Real vendor files (B4) would still sharpen the defaults. |
| 5 | File ingestion + raw retention | 🟨 Upload, retention and parsing done; no worker | `StorageBackend` protocol with a local backend; `POST /api/v1/imports` retains the bytes before recording anything, dedupes by SHA-256, creates a `PENDING` job and — when a profile is named and `IMPORT_PROCESS_ON_UPLOAD` is on — parses it in the same request; `GET /imports`, `GET /imports/{id}`, `GET /imports/{id}/raw`. AC-5.1–5.6 and 5.8 covered. **Not built:** the worker claim loop and restart-safe resumption (AC-5.7); the `JobRunner` from the Amazon work is the intended host. |
| 6 | Parsing + validation + reporting | ✅ Complete (to the matching boundary) | Streaming CSV/XLSX readers with the file's own row numbers; `app/imports/extract.py` applies the profile and raises coded issues (`UPC_INVALID`, `QUANTITY_INVALID`, `QUANTITY_NEGATIVE`, `PRICE_INVALID`, `IDENTIFIER_MISSING`, `AVAILABILITY_UNKNOWN`, `DUPLICATE_IN_FILE`); `process_import_job` writes `import_job_rows` in 1,000-row transactions, fails the job before any row on a missing column or signature mismatch, reconciles counters from the rows, and leaves the job RUNNING at stage MATCHING; `GET /imports/{id}/report` and `GET /imports/{id}/rows`. AC-9.1, 9.2, 9.4, 9.5 covered by 31 tests including 20,000-row CSV and XLSX runs. **Not built:** the immutable `import_report` row (AC-9.3) and the matched-by-rule counts it needs — after phase 7. |
| 7 | Matching engine | 🟨 Normaliser and listing resolver exist | `app/matching/normalize.py` (AC-7.6) is built and shared; the priority-chain resolver for *listings* is in `amazon_listings.py`. The vendor-row engine and `match_attempt` recording are unwritten. |
| 8 | Exception workflow | ⬜ Not started | `product_mapping_exceptions` exists; `Principal` now supplies actor identity, so no longer blocked by B2 |
| 9 | Inventory, availability, watchlist | ⬜ Not started | All four tables exist; no diffing logic |
| 10 | Admin interface | ⬜ Not started | Shell exists; screens are placeholders. Needs a sign-in flow once B2 settles. |
| 11 | Hardening | ⬜ Not started | |

Legend: ✅ complete · 🟨 in progress · ⬜ not started · 🚫 blocked

---

## 3. What phase 1 delivered

### Schema

21 tables covering tenancy and identity, catalog and identifiers, vendors and
import profiles, ingestion, inventory history, matching exceptions, the OOS
watchlist, source-system retention, and audit.

Full documentation, including the Mermaid ER diagram, the relationship and
delete-behaviour reference, and index coverage, is in
[docs/database-schema.md](database-schema.md).

The parts that carry the most weight:

* **Identity.** `products.id` is an immutable UUID and the only foreign-key
  target; `catalog_item_number` is a unique business attribute. Every FK in the
  database points at a UUID — asserted by a test, not by review.
* **Identifiers.** `product_identifiers` is one lookup surface for all identifier
  types, with vendor and marketplace context enforced by a check constraint, and
  three partial unique indexes giving each kind its correct scope. A UPC resolves
  to exactly one product per tenant, which is what makes match priority 1
  unambiguous rather than "pick the first row".
* **Multiple Amazon SKUs.** One row per SKU in `marketplace_listings`. Never a
  delimited column.
* **Approved mappings.** Priorities 3 and 4 read `mapping_status = 'APPROVED'` on
  `vendor_products` and `marketplace_listings`, each requiring an approver and a
  timestamp by check constraint ([ADR 0010](decisions/0010-identifier-model-and-mapping-placement.md)).
* **Import idempotency.** `import_files.sha256` is unique per tenant; a partial
  unique index permits only one non-failed `import_jobs` row per file, so a
  successful import is never silently repeated while a failed one can be retried.
* **Inventory history is append-only.** Snapshots are keyed
  `(import_job_id, vendor_product_id)` and every outbound FK is `RESTRICT`, so
  history cannot be deleted from underneath.
* **No repeated alerts.** `availability_events` is unique on
  (`current_snapshot_id`, `event_type`) — one snapshot raises a given transition
  exactly once.
* **The watchlist is buying intent.** `desired_quantity`, `max_unit_cost`,
  `priority`, and `reason` describe what the company wants to purchase, not
  everything that happens to be at zero stock.
* **Audit is append-only in the database.** A trigger rejects `UPDATE` and
  `DELETE` on `audit_events`, including from a direct psql session.

### Configuration and security foundation

Full detail in [docs/security.md](security.md). In brief:

* **Secrets are typed.** `DATABASE_URL`, `AUTH_JWT_SECRET` and
  `NINEYARD_PASSWORD` are `SecretStr`, so they cannot be printed by accident.
  Reading a real value takes an explicit `.get_secret_value()`, which keeps
  every such use greppable and at a genuine boundary.
* **Production refuses development defaults.** The shipped signing key, the dev
  token endpoint, and error-detail exposure each prevent start-up when
  `APP_ENV=production`. A signing key under 32 characters is refused everywhere.
* **Log redaction at the sink.** Key-based masking plus pattern matching for
  bearer tokens, bare JWTs, DSN passwords, and inline `api_key=` assignments —
  applied on both the structlog path and the stdlib path used by third-party
  libraries. The same functions clean audit payloads.
* **Correlation ids** flow from middleware through a context variable into log
  lines, audit rows, and error responses.
* **One error envelope** for application errors, Starlette's own errors,
  validation failures, and unhandled exceptions. **No stack trace ever reaches a
  client in production.**
* **Audit writer** (ADR 0006): adds the row to the caller's session and does not
  commit, so a change and its audit entry share one transaction.
* **Transaction utilities**: `transaction()`, `savepoint()`, `session_scope()`.
* **Replaceable authentication.** A development JWT backend behind an
  `AuthenticationBackend` protocol, with four roles — `ADMIN`,
  `PURCHASING_MANAGER`, `DATA_OPERATOR`, `VIEWER`. Authorization always reads
  the database, never token claims, so a revocation applies on the next request.
* **`.gitignore` hardened** against token caches, credential files, and imported
  client data — verified by running `git check-ignore`, not by inspection.

### Migration

One revision, `506fd0ecc33a`. Beyond the autogenerated tables it enables
`pg_trgm`, creates the trigram search indexes, installs the audit trigger, and —
critically — drops the 21 enum types on downgrade. PostgreSQL leaves enum types
behind when their tables are dropped, so a downgrade that forgets them makes the
*next* upgrade fail with "type already exists", a defect that would only appear
in a real deployment. A test asserts the round trip.

### Tenant scoping helper (2026-09-14, ADR 0012)

`app/repositories/scoping.py` is the interim rule for tenant isolation until
row-level security is introduced. `TenantScope(organization_id)` builds
`select`/`update`/`delete` statements with
`WHERE Model.organization_id = :id` already applied — the clause is written in
one method and nowhere else — and `ScopedRepository` is the base class every
future repository inherits. It refuses a model without
`OrganizationScopedMixin` and refuses a non-UUID tenant id, because `None`
would compile to `IS NULL` and match nothing silently.

`tests/unit/test_tenant_scoping.py` enumerates every scoped model from the ORM
registry (20 today — every table except `organizations`, asserted), and proves
from compiled SQL that all three statement kinds carry the filter for each.
`tests/integration/test_tenant_scoping.py` creates two organizations with a
vendor of the **same code** in each — which the per-organization unique index
permits, and which is exactly the shape of a leak — and proves select, update
and delete stay inside the caller's tenant, including a lookup by the other
tenant's primary key. Assumption A19 is amended accordingly.

### Phase 6 — parsing, row validation and the import report (2026-09-15)

One pipeline for preview and import. Prompt 17's `mapping.py` and the new
runner both call `app/imports/extract.py`, so what the validate endpoint
shows an operator is exactly what the import does.

* **`app/imports/readers.py`** — now streams: `open_rows()` yields one
  `SourceRow(row_number, cells, is_blank)` at a time; `read_table()` is the
  20-row wrapper the preview uses. Row numbers are the file's own — the
  physical line a CSV record starts on (a quoted record spanning lines is
  numbered by its first line), the sheet row for XLSX — so an error listing
  points at the retained file (AC-5.1). Blank rows are yielded flagged, so
  the import records them as SKIPPED with their number instead of losing
  them. Encoding: the profile's, strictly, if named; else BOM → UTF-8 →
  cp1252 → **charset-normalizer** (new dependency) → latin-1. Not
  charset-normalizer first: on a short Western sample it answers `cp1250`,
  and a wrong label on `detected_encoding` is worse than none.
* **`app/imports/extract.py`** — `plan_columns()` resolves the map against
  the header (by text or index) and locates the status column by header
  text; `extract_row()` produces an `ExtractedRow` — normalised vendor SKU
  (trimmed, whitespace collapsed, upper-cased), canonical UPC with
  `has_valid_checksum` via `app/matching/normalize.py`, description,
  integer quantity, decimal cost with the profile's currency, availability,
  pack size (fixed case pack recorded, never converted — B3) — and a list
  of `Issue(code, severity, field, message)` (AC-9.1). Status is the worst
  severity; `primary_issue` becomes the row's `error_code`. Codes and
  severities: `UPC_INVALID` W (imports on the SKU), `QUANTITY_INVALID` E,
  `QUANTITY_NEGATIVE` E, `PRICE_INVALID` W (null cost), `PACK_SIZE_INVALID`
  W, `IDENTIFIER_MISSING` E (neither SKU nor UPC — nothing could ever match
  it), `AVAILABILITY_UNKNOWN` W (status-column value not in either list →
  UNKNOWN, never available), `DUPLICATE_IN_FILE` W (set by the runner).
* **Migration `fadb756b1b85`** — `import_jobs.current_stage`, a nullable
  `import_job_stage` enum (PARSING, MATCHING, SNAPSHOTTING). Autogenerate
  rendered a bare `sa.Enum`, which does not create the type on
  `add_column`; hand-written to create and drop it. One-step round trip
  tested; enum count in the round-trip test is now 23.
* **`app/services/import_processing.py`** — `process_import_job`: PENDING
  → RUNNING/PARSING (audited, `import_job.started`); bytes re-hashed
  against `import_files.sha256`; the profile is required (a job uploaded
  without one stays PENDING and fails with `PROFILE_MISSING` if run);
  header checked **before any row is written** — a missing required column
  fails with `REQUIRED_COLUMN_MISSING` naming the columns, a pinned
  signature that differs fails with `HEADER_SIGNATURE_MISMATCH` carrying
  expected and observed headers (AC-6.4, AC-9.2). Rows stream through
  `extract_row` and are inserted with Core bulk inserts in chunks of 1,000,
  each chunk its own `transaction()`, counters updated per chunk so
  progress is visible. Repeated vendor SKUs: the *last* occurrence counts;
  earlier ones are marked `DUPLICATE_IN_FILE` in one bulk update afterwards
  (an ERROR row stays ERROR), with `superseded_by_row` written into the
  row's own JSON. Counters are then reconciled from the rows (AC-9.4) and
  the issue breakdown stored under `error_details.parsing`; the job stays
  RUNNING and moves to stage MATCHING, audited `import_job.parsed`. Any
  exception fails the job (`INTERNAL_ERROR`, audited, traceback in the
  log) rather than leaving it RUNNING forever.
* **Report** — `GET /imports/{id}/report`: counters (total, processed, ok,
  warning, error, skipped, matched, exception), `by_error_code` (rows by
  deciding issue), `issues_by_code` (every issue raised), and the sentence
  the brief asks for, e.g. *"6 rows imported, 5 rejected, 3 with invalid
  quantities, 1 with negative quantities, 2 with invalid UPCs, 1 with
  invalid prices, 1 without a UPC or vendor SKU, 1 with unrecognised
  availability, 1 superseded by a later duplicate vendor SKU, 1 empty rows
  skipped. Import is still running (matching)."* `GET /imports/{id}/rows`
  pages the rows in file order with `status` and `error_code` filters
  (AC-9.5).
* **Hook** — `IMPORT_PROCESS_ON_UPLOAD` (default true): an upload that
  names a profile is parsed inside the request and the response carries
  the parsed (or FAILED) job. The `JobRunner` can take this over.

**Two defects found by the tests.** (1) psycopg could not type the bound
parameters inside `jsonb_build_object(...)` in the duplicate-marking
update (`IndeterminateDatatype`); the crash path did what it should — the
job went FAILED with `INTERNAL_ERROR` and an audit row — and explicit
casts fixed the statement. (2) The Prompt 17 preview returned issues as
free-text strings; it now returns the same coded issues the import stores.

**Where memory goes.** The 20,000-row tests run the parser under
`tracemalloc`: peak traced memory 9.7 MB (CSV) and 8.3 MB (XLSX), asserted
under 48 MB. XLSX is read in openpyxl read-only mode row by row. CSV is
decoded to text in one piece before the `csv` reader streams it — bounded
by `IMPORT_MAX_UPLOAD_MB`, and honest to say so.

**Tests.** `tests/unit/test_readers.py` (13): row numbering incl. quoted
multi-line records and `skip_rows`, blank rows flagged, laziness, wide
rows, the encoding order, strict declared encoding, the charset-normalizer
fallback. `tests/unit/test_profile_rules.py` TestMapper (rewritten, 15):
every code above from the extractor, decimal-comma and space grouping,
fixed case pack. `tests/integration/test_import_processing.py` (18): the
brief's two layouts as CSV **and** XLSX (numeric UPC cell restored, sheet
row numbers), latin-1 CSV, a strictly-declared wrong encoding, `;` with
decimal commas and `€`, missing required column → FAILED before any row
with the columns named, signature mismatch → FAILED with expected vs
observed, unpinned profile accepts drift, no profile → PENDING then
`PROFILE_MISSING`, not-PENDING refused, unreadable workbook, tampered
retained file → `FILE_INTEGRITY`, **the every-problem file** with exact
per-row status and code for all 12 rows and exact counters, breakdowns and
summary sentence, row filters and paging, viewer access and tenancy, and
20,000 rows as CSV and as XLSX (20 chunks, 40 blank, 20 bad, every row
number unique, memory bounded).

### Phase 5 part 1 — file upload with raw retention (2026-09-15, ADR 0004)

The retention half of AC-5. The order of operations is the deliverable:
refuse → store → record, so a file that reaches the database is already
on disk exactly as sent.

* **`app/imports/storage.py`** — `StorageBackend` protocol (`put`, `open`,
  `exists`) and `LocalStorageBackend`. URIs are `<scheme>://<key>` with the
  key *relative to the backend* — `local://<organization_id>/<yyyy>/<mm>/
  <sha256>.<ext>` — so the root can move and an S3/Azure backend is a new
  scheme, not a schema change. The key is content-addressed: the same bytes
  land at the same key, a second `put` verifies and returns, and an object
  that does not hash to its key is refused rather than overwritten. Writes
  go to a temporary sibling, are fsynced, and are hard-linked into place
  (falling back to a guarded rename on filesystems without links); a key
  cannot escape the root. Nothing is created on disk until the first put.
  Chosen in `build_storage_backend`, the one place B5's answer will land.
* **`app/services/imports.py`** — `receive_upload`: extension (`.csv`,
  `.xlsx` → 415), size (413) and emptiness (422) are checked before anything
  touches disk; the vendor and an optional *active* profile are resolved;
  the SHA-256 is looked up. Known bytes with a live job (anything but
  FAILED/CANCELLED, exactly what `uq_import_jobs_active_import_file`
  permits) → the existing job, `duplicate: true`, HTTP 200, no audit row.
  Known bytes whose jobs all failed or were cancelled → a new `PENDING` job
  on the *same* file row (stored once). Known bytes received for a different
  vendor → 409. New bytes → `storage.put` first, then one transaction
  writing `import_files` (with `detected_encoding` for CSV, AC-5.8), the
  `PENDING` job with the profile id and version denormalised, and audit
  rows `import_file.received` and `import_job.created`. The raw download
  re-hashes the bytes on the way out and refuses a mismatch.
* **`app/api/v1/routes/imports.py`** — `POST /imports` (multipart:
  `file`, `vendor_id`, optional `profile_id`; `DATA_OPERATOR`; the body is
  read in 1 MB chunks and abandoned the moment it passes the limit),
  `GET /imports` (newest first, filter by vendor and status, paged),
  `GET /imports/{id}`, `GET /imports/{id}/raw` (every role; streams the
  bytes under the original filename with an RFC 6266 UTF-8 name, the
  right media type, and `X-Content-SHA256`).
* **Settings** — `import_max_upload_mb` (default 50, in `.env.example`).
  The backend is built once in `create_app` and published on `app.state`
  next to the settings.

**A note on `receive_upload`'s signature.** The brief lists
`organization_id` and `uploaded_by` as separate parameters; both come from
the caller's `Principal`, so the function takes the principal. Same
information, one fewer way to pass a user from the wrong organization.

**Tests.** `tests/unit/test_storage.py` (14): the key layout, `.bin` for
foreign extensions, URI parsing, relative URIs, same bytes stored once,
**an existing object is never overwritten** (tampered bytes stay tampered
and the put fails), organisations do not share keys, binary round trip,
missing object, another backend's scheme refused, root escape refused.
`tests/integration/test_import_upload_api.py` (34): 401/403/tenant;
**CSV round trip byte-identical** — SHA-256 of the upload == recorded ==
on disk under `<org>/<yyyy>/<mm>/<sha>.csv` == downloaded, BOM and CRLF
intact; XLSX likewise; cp1252 detected; non-ASCII filename survives; two
audit rows naming the uploader; duplicate returns the existing job with no
new row, file, object or audit entry; FAILED and CANCELLED each allow the
same bytes again without a second copy on disk, RUNNING/COMPLETED/
COMPLETED_WITH_ERRORS do not; same bytes for another vendor → 409; four
bad extensions → 415 with nothing on disk; one byte over the limit → 413
with nothing on disk, exactly the limit accepted; empty → 422; missing or
unknown vendor; inactive profile → 409; another vendor's profile → 404;
profile id and version pinned on the job; list filters and paging.

**Observed once, not reproduced.** In one of four full-suite runs the
pre-existing `test_migrations.py::test_upgrade_then_downgrade_then_upgrade_succeeds`
errored at setup while dropping its scratch database; the three other runs
and the `tasks.ps1 check` run below passed. It is not caused by this
change (the test touches a separate `_roundtrip` database) and is noted
here so it is not forgotten if it recurs.

### Phase 4 — versioned import profiles (2026-09-15, ADR 0013)

The rule shapes the schema left open are now closed. Same layering as
phase 2, plus a small pure-Python import package that the real import
(phase 5/6) will reuse unchanged.

* **`app/imports/profile_rules.py`** — one Pydantic model per JSONB column,
  `extra="forbid"`, with the validators the brief lists: `column_map`
  needs `quantity_available` and one of `upc`/`vendor_sku`, no duplicate
  target or source; `normalization_rules` separators must differ;
  `availability_rules` in `status_column` mode needs the column and a
  value list, and the two lists cannot overlap; `quantity_semantics` in
  `fixed` mode needs a positive `fixed_pack_size`; `price_semantics`
  currency is ISO 4217; `pack_size_handling` has the single mode
  `store_as_stated`. `ProfileRules` adds the cross-rule checks (case
  quantities from a column need a `pack_size` mapping; price per case
  needs `unit_cost`). `rule_json_schemas()` exports the six schemas.
  `compute_header_signature()` is SHA-256 of the normalised header row.
* **`app/imports/readers.py`** — CSV (encoding fallback `utf-8-sig` →
  `cp1252` → `latin-1`, delimiter sniffed unless the profile names one,
  `skip_rows` / `header_row_index`) and XLSX via openpyxl in read-only
  mode, sheet by name or index. Every cell comes out as text; an
  integer-valued numeric cell is rendered without `.0`, so a UPC typed as a
  number keeps its digits (risk R2). Readers stop after `max_rows`.
* **`app/imports/mapping.py`** — resolves each mapped source against the
  header row (case- and whitespace-insensitive), checks the signature, and
  maps each row: UPC through the shared normaliser with the profile's
  strip/pad flags, quantity as an integer, cost as a decimal after currency
  and thousands stripping, availability by quantity or by a status column
  looked up by header text — unrecognised status → `UNKNOWN`. Per-row
  issues, never exceptions.
* **`app/services/import_profiles.py`** — `create_profile` (version =
  latest + 1 for the vendor+name line, so a re-created profile continues
  its history), `update_profile` (refuses an inactive version; merges the
  set fields over version n, re-validates the whole, retires n and inserts
  n+1 in one `transaction()` with `import_profile.superseded` and
  `import_profile.version_created` audit rows), `deactivate_profile`,
  `preview_file` (25 MB cap, first 20 rows, stores nothing). Typed
  refusals: `import_profile_name_taken` (409), `import_profile_not_active`
  (409), `import_profile_not_found` (404), `sample_file_unreadable` /
  `sample_file_too_large` / `import_profile_invalid` (422).
* **`app/api/v1/routes/import_profiles.py`** — reads for every role,
  writes for `DATA_OPERATOR`. The draft-validate route takes the profile as
  a JSON form field beside the file, since multipart carries no validated
  JSON body part; its validation errors come back in the same
  `{location, message, type}` shape as body errors, with no input echoed.
* **Dependencies** — `openpyxl` and `python-multipart` (runtime),
  `types-openpyxl` (dev). No migration: the columns already existed.

**Two decisions made while building, both recorded in the ADR.** (1) The
status column is located by header text in the file, not through the
column map, so an operator does not have to map "Status" to `ignore` just
to read it. (2) A `source` of `true` would have coerced to column 1 under
lax int parsing; a before-validator now refuses booleans.

**Tests.** `tests/unit/test_profile_rules.py` (70): every validator above
with its negative case, the schema export, the signature's sensitivity to
rename / reorder / add and insensitivity to case / spacing, the readers
(BOM, cp1252 fallback, sniffed and declared delimiters, skip rows, header
row index, truncation, padding, sheet selection, garbage input, numeric
UPC cell) and the mapper. `tests/integration/test_import_profile_api.py`
(46): 401 on every route, 403 by role, another tenant's vendor → 404,
eleven boundary rejections with no row written, version 1 with defaults
and one audit row, **PATCH creates v2 and retires v1 with two audit rows**,
inactive → 409, empty PATCH → no version, a cross-rule-breaking PATCH
refused whole, list active vs `include_inactive`, deactivate; the validate
routes with the brief's two layouts built in-test — `UPC | Description |
Quantity | Cost` as CSV (leading zero restored, `$` stripped, bad check
digit and non-numeric quantity reported per row) and `Vendor SKU | UPC |
Qty Available | Wholesale Price` as XLSX (numeric UPC cell read as digits,
named sheet) — plus signature mismatch, unpinned draft, 20-row cap,
unreadable workbook, wrong sheet, and three invalid drafts.

### Phase 2 — the vendor database API (2026-09-15)

The first real API, and the pattern every later one copies: **schema →
repository → service → route**, each layer doing one thing.

* **Migration `44c932e601b0`** — `vendors` gains `minimum_order_quantity`,
  `minimum_order_value` (both nullable, `>= 0` by check) and free-form
  `purchasing_terms` JSONB; new `vendor_contacts` (RESTRICT to its vendor,
  email unique per vendor case-insensitively via a `lower(email)` index, one
  active primary per vendor by partial index). The two checks are
  hand-written; `alembic check` clean; one-step round trip tested
  ([database-schema.md §3.3a](database-schema.md)).
* **`app/schemas/vendors.py`** — code normalised to an upper-case
  identifier, currency to ISO-4217, emails to a lower-case address shape;
  `VendorUpdate` has no `code` field at all (the business key is not
  editable). 42 unit tests.
* **`app/repositories/vendors.py`** — `VendorRepository(ScopedRepository)`
  (ADR 0012): paginated list with status filter and name/code search
  (`ILIKE` over the trigram index), get, add, `has_active_import_profiles`,
  and the contact reads. No delete exists.
* **`app/services/vendors.py`** — create / update / deactivate and
  contact add / update / deactivate, each in one `transaction()` with
  `audit.record_change` before it closes; a second primary contact demotes
  the previous one in the same transaction, audited. Typed refusals:
  `vendor_code_taken` (409, with the unique index as the backstop against a
  race), `vendor_has_active_import_profiles` (409),
  `vendor_already_inactive` (409), `vendor_contact_email_taken` (409),
  `vendor_not_found` / `vendor_contact_not_found` (404).
* **`app/api/v1/routes/vendors.py`** — the ten routes above, all documented
  with 403 in OpenAPI (contract test).

**Two findings during the build.** (1) The repository's count query built
through `TenantScope` added `vendors` to `FROM` a second time — a cartesian
product SQLAlchemy warns about, which is an error here. The count is now a
plain `select(count())` over the already-scoped subquery. (2) The role model
is flat: gating reads on `VIEWER` alone meant a `DATA_OPERATOR` could create
a vendor and then get 403 fetching it. Reads now accept every role; a test
logs in as each role and reads.

**35 route tests**: 401 on every route unauthenticated, 403 by role for
each of the four roles, duplicate code → 409 with a typed body and no second
row, the same code allowed in another organization, field-level 422s,
another tenant's vendor → 404, search / status filter / pagination / bad
query parameters, deactivation refused with active profiles and allowed
once they are retired, contacts end to end, and one test that performs
every mutation in sequence and asserts exactly one `vendor*` audit row per
mutation, each naming the acting user with `before` / `after` state.

### Amazon scheduler, run recovery and CLI (2026-09-15, POC step 7)

* **`app/jobs/runner.py`** — `JobRunner` protocol (`schedule` /
  `start` / `shutdown`) and `APSchedulerRunner`: APScheduler 3.x
  in-process, `max_instances=1`, `coalesce=True`,
  `misfire_grace_time=3600`, no broker, no job store. Replaceable in one
  module; tests use a fake runner and never start a thread.
* **`app/jobs/amazon.py`** — orders (24 h; `AMAZON_ORDERS_WINDOW_DAYS`=35
  split into ≤30-day report requests, which is how the brief's 35-day
  window and the report's 30-day cap coexist), inventory (60 min), listings
  (24 h, after inventory). Each opens its own session, runs `SCHEDULED`,
  catches everything. **Stale-run recovery** closes any `RUNNING` run of the
  job type older than `AMAZON_RUN_TIMEOUT_MINUTES` as `FAILED` ("timed out")
  with an audit event, before the new run claims the slot — integration
  tested, including that recovery frees the slot and never touches another
  job type or tenant. The scheduler starts in the lifespan only with
  `AMAZON_ENABLED=true` and never under `APP_ENV=test` (tested).
* **`app/cli/amazon_poc.py`** — `auth` (prints the token's expiry, nothing
  else), `sync-orders [--days]`, `sync-inventory`, `sync-listings`, `run`,
  `velocity [--level sku|product] [--top N]`; exit `1` on any `FAILED` run,
  `2` on configuration, `3` unexpected. README has a "Running the Amazon
  POC" section.
* **`app/services/amazon_velocity.py`** — the CLI needed a velocity
  computation that did not exist (the prompt sequence skipped from 11 to
  13): units 7/14/30 from order lines excluding cancellations, the latest
  snapshot per SKU, mapping + Catalog Item Number + UPC, days of supply
  blank when nothing sold. The HTTP endpoint from the ADR is still owed.
* Also: `AmazonClient.check_credentials()` (an LWA exchange that returns
  only expiry and a hash prefix), `run_listings_sync` for the standalone
  listings job, `resolve_amazon_organization` (`AMAZON_ORGANIZATION_SLUG`,
  optional while one organization exists).
* **Tests:** 17 CLI (parsing, table alignment, exit-code mapping with
  dispatch stubbed), 16 runner/jobs (protocol with a fake runner, APScheduler
  options without starting, window splitting, lifespan guards), 9
  integration (stale-run recovery ×4, organization resolution, the listings
  run, velocity ×2).

### Amazon listings → product mapping (2026-09-15, POC step 6)

`app/services/amazon_listings.py` is the **first matching code** in the
repository. It upserts `marketplace_listings` from the merchant listings
rows and resolves each seller SKU to a product through the CLAUDE.md §5.1
chain and nothing else: priority 4 (`AMAZON_SKU` identifier → approved
automatically, approver = a per-organization system user) then priority 1
(the listing's UPC/EAN, normalised → a **suggestion**, `PENDING` plus a
`SUGGESTION_ONLY` queue item, because §5.1 row 5 says a person approves
it); otherwise `UNMAPPED` with a `NO_MATCH`, `AMBIGUOUS_MATCH` or
`CONFLICTING_IDENTIFIER` queue item carrying every rule tried. `item-name`
is never an input; an `APPROVED` listing is never modified. Full description
in [amazon-integration.md §9](amazon-integration.md).

**Three schema corrections were needed first** — the brief and the tables
disagreed, and the tables were wrong: a listing could not exist without a
product, an exception could not be about a listing, and an `AMAZON_SKU`
identifier could not precede its listing. Migration `3de5c4e5def0` fixes all
three with hand-written check constraints (Alembic does not compare them) and
a one-step downgrade test ([database-schema.md](database-schema.md)).

`app/matching/normalize.py` (AC-7.6) is new and shared with the vendor
import: separators stripped, 11-digit UPC-A restored, UPC-E expanded, GTIN
check digit verified, canonical zero-padded GTIN-14.

**Tests:** 34 normaliser cases (valid UPC-A/EAN-13/EAN-8/GTIN-14, the
textbook UPC-E, bad check digit, 11-digit, separators, scientific notation),
20 resolver cases against an in-memory catalog (every branch, ambiguity
stops the chain, conflicting identifiers, item name never consulted,
determinism), 7 integration cases asserting the three-row fixture's
listings, exceptions and audit events exactly plus idempotency, permanence
of an approved mapping, a later priority-4 hit closing an open queue item,
and tenant isolation; 12 new constraint/relationship/migration cases.

### Amazon inventory ingestion (2026-09-15, POC step 5)

`app/services/amazon_inventory.py` — `run_inventory_sync` reads every FBA
inventory summary (with a configurable pause between pages,
`AMAZON_INVENTORY_PAGE_DELAY_S`, default 0.6 s) and the merchant listings
report in one run, merges them per seller SKU, and appends one
`AmazonInventorySnapshot` per SKU with `captured_at` = the run start. FBM
quantity comes from listings rows whose `fulfillment-channel` is `DEFAULT`;
an FBM-only SKU gets a snapshot with zero FBA quantities. Snapshots are
append-only — a second run adds rows and changes none. Full description in
[amazon-integration.md §8](amazon-integration.md).

The run bookkeeping shared with the orders sync — claim the `RUNNING`
slot, close with counts and an audit event, close as `FAILED` on any
exception — now lives once in `app/services/amazon_runs.py`; the orders
service uses it with no change to its behaviour or tests.

**33 parse/merge unit tests** (FBA row, FBM row, blank FBM quantity, unknown
channel, product-id-type 1 vs 3, Latin-1 name, merge cases including
FBM-only without ASIN and duplicate summaries) and **11 integration tests**
(one snapshot per SKU with FBA and FBM merged, page delay reaching the
client, append-only across two runs, the listings sink, rejected rows,
failure while paging, failure fetching the report, failure inside the insert
transaction, the concurrent-run guard, a running orders job not blocking
inventory). The parsed listings rows are passed to an optional
`listings_sink`; a named `TODO(prompt-11)` marks where the mapping service
takes over.

### Amazon orders ingestion (2026-09-15, POC step 4)

`app/services/amazon_orders.py` — `run_orders_sync` claims the `RUNNING`
slot in its own transaction, fetches the all-orders flat-file report
through the read-only client, parses it, upserts one line per (order, SKU)
and closes the run with counts and an audit event in one transaction; any
failure closes the run as `FAILED` with the exception recorded and
re-raises. Full description in
[amazon-integration.md §7](amazon-integration.md).

* **No PII can reach the database.** `raw` is built from the twelve
  required columns, never from the row, so `ship-*` and `product-name` are
  unreadable to the ingestion. Asserted at parse level and at schema level.
* **Idempotent.** A second identical run creates 0, updates 0, and sets
  `last_seen_sync_run_id` on every line; a newer `last-updated-date` updates
  exactly that line; an older report cannot regress a line — the "only if
  newer" rule is in the SQL, not only in Python.
* **42 parse unit tests** on a fixture with the report's real column set
  (FBA, FBM, Cancelled, Pending, a repeated (order, SKU) pair, a missing
  ASIN, a Latin-1 product name, quantity 0) in both encodings, plus row-level
  failure and window cases. **14 integration tests** with a fake fetcher:
  first run creates 8, identical run changes nothing, newer date updates 1,
  a client failure and a failure *inside* the upsert transaction each leave
  exactly one `FAILED` run and no `RUNNING` row, the partial unique index
  refuses a concurrent run, and a running inventory job does not block an
  orders run.

**A foundation defect found and fixed.** `transaction()` and `savepoint()`
had `commit()` in an `else` branch, so a constraint violation raised *by the
commit itself* was never rolled back and the session was left
pending-rollback. The orders sync hit it on its second call. `commit()` is
now inside the `try`; a regression test covers the commit-time path. Phase
2's vendor CRUD would have hit the same defect on its first duplicate code.

**One deviation from the brief.** The brief asked for a trailing window of
35 days and a 30-day maximum; the two cannot both hold, so
`trailing_window()` defaults to the 30-day report limit and refuses more.
Deeper coverage means two windows, which is a scheduler decision.

### Amazon ingestion tables (2026-09-15, POC step 3)

Migration `3767ee979011` adds three organization-scoped tables under every
convention in CLAUDE.md §4 — UUID keys, `TIMESTAMPTZ`, named constraints,
native enums — reusing the `sync_status` and `trigger_type` enums and adding
one, `amazon_sync_job_type`. Full description in
[database-schema.md §3.9](database-schema.md).

* **`amazon_sync_runs`** — one row per ingestion execution and job type. A
  partial unique index permits one `RUNNING` row per `(organization_id,
  job_type)`, so a scheduler tick cannot start a second orders pull while
  the first is still polling; a check keeps `completed_at` null while
  `RUNNING`.
* **`amazon_order_lines`** — grain is `(order, seller SKU)` because the
  all-orders flat-file report has no order-item id; the ingestion aggregates
  per pair before upserting. **No buyer or shipping column exists**, and a
  schema test asserts that against the live table, not the model.
* **`amazon_inventory_snapshots`** — append-only, unique per `(run, SKU)`,
  indexed on `(organization_id, seller_sku, captured_at DESC)`.

**Hand-checked after autogenerate.** Two things autogenerate gets wrong when
a revision reuses enum types, both fixed and both covered by a test that
downgrades exactly one step: the shared enums are declared with
`create_type=False` so upgrade does not re-`CREATE TYPE` them, and downgrade
drops only `amazon_sync_job_type` — dropping the shared two would take
`nineyard_sync_runs` down with them. `alembic check` is clean; the
`captured_at DESC` expression index compares correctly.

**39 new integration tests** across the four schema test files: every unique
(including the permitted case for each partial index), every check, every FK
delete behaviour, the PII-column assertion, and the round trip. The
tenant-scoping unit test picked up the three new tables automatically (+9
cases), which is exactly what it was written to do.

### Amazon SP-API client — the anti-corruption layer (2026-09-15, POC step 2)

`app/integrations/amazon/` wraps `python-amazon-sp-api` 2.1.23 (MIT, now a
dependency) the way `app/integrations/nineyard/` wraps Nineyard (ADR 0007).
Full description in [amazon-integration.md](amazon-integration.md). The
points that matter most:

* **Read-only by structure.** `AmazonClient` has exactly five public
  methods — `request_report`, `get_report_status`,
  `download_report_document`, `fetch_report`, `iter_inventory_summaries` —
  and no generic call method. A test asserts the set and a second asserts no
  write-shaped name (`create_`, `put_`, `post_`, `update_`, `delete_`,
  `submit_`) exists other than `request_report`.
* **One credential boundary.** The library's `{refresh_token, lwa_app_id,
  lwa_client_secret}` dict is built in one private method from `SecretStr`
  values and passed straight to the library constructor; it is not stored,
  logged or returned. A test drives a request through the real logging
  pipeline at `DEBUG`, adds the library's own logger output, and asserts no
  secret reaches the output.
* **Two library behaviours closed off**, found by reading the installed
  code rather than its README: `SP_API_DEFAULT_MARKETPLACE` in the
  environment silently overrides an explicitly passed marketplace (the
  client refuses to construct while it is set), and the library will look for
  credentials in its own env vars and config files if not given a dict (it
  always is).
* **Retries** only on the library's 429 / 500 / 503 / 504 exceptions, capped
  exponential backoff with jitter, `Retry-After` honoured, attempts from
  `AMAZON_MAX_ATTEMPTS`; LWA failures are never retried. A throttled
  inventory page is re-requested with the same `nextToken`.
* **50 unit tests** against fakes of the library classes: credential
  boundary, each DTO from a realistic payload, three-page pagination,
  retry-then-succeed, exhaustion into `AmazonRateLimited` /
  `AmazonTransientError`, auth not retried, `fetch_report` reaching `DONE`,
  `CANCELLED`, `FATAL` and timeout.

Not yet verified against the real account: the application's SP-API roles,
report latency, real throttling, and the report charset — listed in the
doc's §7.

### Amazon SP-API configuration and secret handling (2026-09-15, POC step 1)

The first slice of the ADR 0011 work, done before credentials arrive so that
nothing about their handling is improvised later:

* **Settings** (`app/core/config.py`): `amazon_enabled`, `amazon_lwa_client_id`,
  `amazon_lwa_client_secret` and `amazon_lwa_refresh_token` (both `SecretStr`),
  `amazon_seller_id`, `amazon_marketplace_id` (default `ATVPDKIKX0DER`),
  `amazon_region` (validated to `NA`/`EU`/`FE`), `amazon_timeout_seconds`,
  `amazon_max_attempts`. `amazon_configured` is true only when all four
  credentials are present and non-blank. With `AMAZON_ENABLED=true` and any of
  them missing, start-up is refused in **every** environment with a message
  naming exactly the missing variables — the same fail-fast style as the
  production guards.
* **Redaction** (`app/core/redaction.py`): `refresh_token`, `client_secret`,
  `lwa_*` and `x-amz-access-token` are masked by key. Two value patterns were
  added, and one of them closed a real gap: the existing inline rule caught
  `access_token=...` but **not** the JSON form `"access_token": "..."` — the
  closing quote after the key broke the match — so a raw LWA token-endpoint
  body logged by an HTTP library would have passed through intact. JSON
  members named `access_token`, `refresh_token`, `client_secret`, `id_token`,
  `api_key`, `password`, `secret` or `authorization` are now masked, and so is
  the bare LWA token shape (`Atza|…` access, `Atzr|…` refresh) wherever it
  appears.
* **Tests:** 39 new unit cases across `test_config.py`, `test_settings_secrets.py`
  and `test_redaction.py`, including that `repr(settings)`, `safe_dump()` and a
  real structlog line never contain the secret values, and the end-to-end
  pipeline masks the SP-API header, the token body and a token in free text.
* `.env.example` documents the variables with the Seller Central path
  (Apps & Services > Develop Apps, self-authorised private app). No real value
  exists anywhere in the repository.

Still owed from the ADR 0011 follow-ups: an AC-14 group in
[acceptance-criteria.md](acceptance-criteria.md) and an Amazon section in
[security.md](security.md).

### Deliberately absent

No Nineyard *synchronisation* (the client is read-only and diagnostic), no
file parsing, no matching engine, and no vendor or import endpoints. No password storage, MFA, refresh tokens, or rate limiting —
Entra ID will own credentials, and building a half-credential store first would
be work thrown away ([security.md §8](security.md) lists this honestly).

---

## 4. Quality gate results

Run on 2026-09-15 via `.\tasks.ps1 check`. Windows 11, Python 3.12.10, Node 24.19.0, PostgreSQL 16.15.
The same gates run in GitHub Actions on every push
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml), described in
[deployment.md](deployment.md#continuous-integration)). First run, all three
jobs green on the first attempt with no change to any Dockerfile or the Compose
file: https://github.com/cbfriedman/Power-BI-Data-Analysis-and-Inventory/actions/runs/34892991973.

| CI job | Result |
|---|---|
| `backend` (Ubuntu, Python 3.12, `postgres:16-alpine` service) | ✅ 83 files formatted · ruff clean · mypy clean (81 files) · **`302 passed in 5.63s`**, 0 skipped — the integration suite ran against the service container |
| `frontend` (Ubuntu, Node 24) | ✅ `npm ci`, eslint, tsc, `next build` |
| `compose-smoke` (Ubuntu, Docker) | ✅ all three images built; `/api/v1/health` → 200 with `postgresql: ok` 2 s after start; `alembic upgrade head` applied `506fd0ecc33a`; `alembic check` → "No new upgrade operations detected"; web answered on :3000; `down -v` clean. 93 s end to end. |

| Gate | Command | Result |
|---|---|---|
| Backend format | `ruff format .` | ✅ 144 files unchanged |
| Backend lint | `ruff check .` | ✅ All checks passed |
| Backend types | `mypy` (strict) | ✅ No issues in 138 source files |
| Backend tests | `pytest` | ✅ `1033 passed in 37.82s` — `tests/unit`: `681 passed`; `tests/integration`: `352 passed` |
| Migration apply | `alembic upgrade head` | ✅ All five revisions applied to PostgreSQL 16.15 |
| Migration reverse | `alembic downgrade base` → `upgrade head` | ✅ Clean round trip, 0 residual enum types; one-step downgrades of `3767ee979011`, `3de5c4e5def0`, `44c932e601b0` and `fadb756b1b85` each re-apply cleanly |
| Migration drift | `alembic check` | ✅ No new upgrade operations detected |
| Frontend lint | `npm run lint` | ✅ Clean |
| Frontend types | `npm run typecheck` | ✅ Clean |
| Frontend build | `npm run build` | ✅ 10 routes prerendered (12 static pages) |
| Compose config | `docker compose config` | ✅ Valid (client-side) |

### A design conflict the tests caught

`audit_events.actor_user_id` was written as `ON DELETE SET NULL`, the obvious
choice for "keep the audit entry, forget the user". It is wrong: `SET NULL` makes
PostgreSQL issue an `UPDATE` against `audit_events` during the delete, which the
append-only trigger correctly refuses — so deleting a user failed with a
confusing trigger error rather than a clear constraint error.

Changed to `RESTRICT`, which states the actual rule: a user who has acted cannot
be deleted, only deactivated. That is consistent with every other entity in this
schema, and it makes the audit trail genuinely immutable. Two tests now cover it
— the delete is refused, and deactivation leaves the trail attributable.

Nothing found this by inspection. It surfaced because the relationship tests
issue Core deletes and let the database decide.

### Repository hygiene (2026-09-14)

`README.md` had been committed as **UTF-16 LE without a BOM**, so Git stored it
as a binary blob and GitHub rendered it as garbage. It was decoded and rewritten
as UTF-8 without BOM; the decoded text was verified character-for-character
identical to the original (6,755 characters, 209 lines) before the write.

A scan of all 148 tracked files found no other non-UTF-8 file and no UTF-8 BOM.
Every committed blob already used LF; CRLF appeared only in the working copy
via `core.autocrlf`. A root `.gitattributes` now pins `* text=auto eol=lf` and
`*.ps1 text eol=crlf` so neither problem depends on a contributor's machine
again. `git add --renormalize .` changed nothing but `README.md`.

The README's "No migrations exist yet" sentence was also corrected: revision
`506fd0ecc33a` exists.

### Three more the security tests caught

* **Routes read the wrong settings.** Route dependencies called `get_settings()`
  directly, which returns the process-wide cache — so an app built by
  `create_app(settings)` could answer according to a *different* configuration.
  Harmless in production where they coincide; wrong in tests, and a real hazard
  for any multi-configuration process. Routes now take settings off
  `app.state` via `get_app_settings`, which makes disagreement impossible.
* **`X-Forwarded-For` could 500 a request.** The header is caller-supplied text
  and was written straight into an `INET` column, so `X-Forwarded-For: nonsense`
  would fail the INSERT and turn an ordinary request into a 500 — trivially
  triggerable from outside. The value is now parsed with `ipaddress` at the
  boundary and discarded if it is not a real address.
* **`savepoint()` skipped its own recovery.** It guarded the rollback on
  `nested.is_active`, but a failed flush leaves the nested transaction
  *inactive* — precisely when the rollback is needed. The enclosing transaction
  then died with `PendingRollbackError`. The guard is gone.

---

## 5. Toolchain changes made to this machine

| Change | Detail | Reversible |
|---|---|---|
| Python 3.12.10 installed | `winget install Python.Python.3.12 --scope user` (2026-09-07) | `winget uninstall` |
| **PostgreSQL 16.15 installed** | `winget install PostgreSQL.PostgreSQL.16`, service `postgresql-x64-16`, port 5432 | `winget uninstall` |
| Role and databases created | Role `prms` (LOGIN, CREATEDB); databases `prms` and `prms_test` | `DROP DATABASE` / `DROP ROLE` |
| Docker Desktop launched | Engine still fails to start (§6, S1) | Quit the app |

PostgreSQL was installed natively because the Docker container cannot run on
this machine. It matches the `postgres:16-alpine` image the Compose file uses,
so the schema is verified against the same major version that will run in
deployment.

Two local configuration changes accompanied it:

* `.env` now points `DATABASE_URL` at `localhost` rather than the `postgres`
  Compose service name. `.env.example` is unchanged and still documents both.
* `app/core/config.py` resolves `.env` from absolute paths (repository root and
  `backend/`). Previously a relative `.env` was read from the working directory,
  so running anything from `backend/` silently missed the file and fell back to
  defaults — a footgun that would have bitten every developer.

---

## 6. Open setup issues

### S1 — Docker is intermittent on this machine *(no longer blocks anything)*

**The Compose stack is verified in CI** (§4): the `compose-smoke` job builds
all three images from a clean checkout, starts them from `.env.example`,
confirms the API reports PostgreSQL healthy, applies and checks the migration
inside the `api` container, and confirms the web container answers. That is
Milestone 1 exit criterion #1, and it passed on the first run with no change
to any Dockerfile or the Compose file. The frontend image, which could never
be built on this machine, built cleanly there.

What remains true locally: the engine is unreliable here. It ran on
2026-09-08 long enough to build the **backend** image, but the **frontend**
image fails in `npm ci` with `ECONNRESET` because of a Docker networking fault
on this machine — see [deployment.md](deployment.md). The original failure
mode was:

```
WSL2 is unable to start since virtualization is not enabled on this machine.
Please ensure the "Virtual Machine Platform" optional component is enabled and
virtualization is turned on in your computer's firmware settings.
```

Diagnostics: `HypervisorPresent: False`, `VirtualizationFirmwareEnabled: True`,
WSL distro `docker-desktop` is `Stopped`.

Fix (elevated PowerShell, then **reboot**):

```powershell
wsl.exe --install --no-distribution
```

**What this does and does not block.** Nothing, now. Local Docker is a
convenience; CI is the proof. Anyone changing a Dockerfile or the Compose file
should expect the `compose-smoke` job to be the arbiter.

### S2 — Deployment state (Railway)

The **backend** service is deployed and healthy: `GET /api/v1/health` returns
`{"status":"ok","environment":"production","dependencies":[{"name":"postgresql","status":"ok"}]}`
against Railway's managed PostgreSQL. Still to do, in order: set
`alembic upgrade head` as the backend pre-deploy command; create the
`frontend` service (Root Directory `frontend`, `NEXT_PUBLIC_API_BASE_URL` set
**before** the first build); set backend `CORS_ALLOW_ORIGINS` to the frontend
origin. The first Railway frontend build is the real test of the frontend
image. There is **no authentication in production yet** — see
[deployment.md](deployment.md) before any mutation endpoint ships.

---

## 7. Blocking questions

Unchanged. B1 and B2 gate implementation work; the rest shape it. B3 and B4 have
become more pressing now that `vendor_import_profiles` exists and its JSONB rule
columns need real shapes.

### B1 — Nineyard API specification *(partially answered)*

**Answered:** the base URL, the authentication endpoint
(`POST /api/OAuth/UsernameToken`), its request body (`email`, `password`,
`companyId`), the bearer-token scheme, and four read-only endpoint groups —
`/api/Items`, `/api/Skus`, `/api/Vendors`, `/api/PurchaseOrders`.

The `nineyard_api_key` setting has been removed accordingly: the real
authentication is a credential triple, not an API key.

**Still unknown, and now answerable by running the probe rather than by asking:**
response envelope shape, field names and types, nullability, paging mechanism and
parameter names, rate limits, and which groups the integration account can
actually read. `docs/nineyard-field-mapping.md` tracks each one.

**Still needs a person at Nineyard**, because no single probe run can establish
it:

1. Which field is the **Catalog Item Number**, and is it stable across syncs?
   The whole product-identity model rests on this.
2. What is the relationship between Items and Skus? It decides how three tables
   are populated.
3. Is incremental sync supported (an `updatedSince` filter)? If not, full pulls
   are the only option and `nineyard_sync_runs.cursor` stays unused.
4. How are deletions represented — absence from the collection, or a flag?
   Getting this wrong silently deactivates live products.
5. Are there published rate limits?

### B2 — Users, roles, and authentication *(narrowed; now blocks only phase 10)*
**No longer blocks phase 8.** `Principal` supplies actor identity, the four
roles are defined and seeded, and `require_roles` is ready to guard mutation
endpoints, so the exception workflow can be built now.

Two decisions remain, both about Entra ID rather than about whether to use it:

1. **Is Entra authoritative for roles?** If yes, `user_roles` becomes a cache of
   Entra app roles and the local grant path is removed rather than left as a
   second source of truth. If no, Entra authenticates and this system authorizes.
2. **Provisioning:** just-in-time user creation on first sign-in (simplest), or
   SCIM (correct if deprovisioning must be prompt).

Confirmation that Entra ID is in fact the target would also be useful — see
assumption A21. Phase 10 needs a sign-in flow, which needs both answers.

### B3 — Pack size and unit-of-measure policy *(decided for Milestone 1 by ADR 0013)*
`pack_size_handling` now has exactly one mode, `store_as_stated`: quantities
are kept as the vendor states them, with `pack_size` and `unit_of_measure`
alongside, and no conversion is made. A `normalize_to_each` mode is the
obvious later addition; it needs a rounding and pricing policy from the
client first and would arrive as an additive shape change with its own ADR.

### B4 — Representative vendor files *(blocks realistic phases 6 and 7)*
3–5 real vendor files (CSV and XLSX, anonymized), ideally one known-messy
example, plus typical and maximum row counts and file cadence. The rule
shapes are now fixed (ADR 0013) and were exercised against the two layouts
the brief names, built in-test; real files would confirm the defaults
(currency symbols, separators, status vocabularies) and give phase 6 its
fixtures.

### B5 — Raw file storage destination and retention *(blocks phase 5 deployment)*
Local disk, S3, Azure Blob, or other? Any retention, deletion, or compliance
requirement? The local backend is built and in use; its URIs are
`local://<organization_id>/<yyyy>/<mm>/<sha256>.<ext>`, relative to the
configured root, so an S3 or Azure backend is a new scheme in
`build_storage_backend` and no change to `import_files`. Until B5 is
answered the Railway deployment retains files on the container's disk,
which does not survive a redeploy — **uploads to the deployed instance are
not durable yet.**

### B6 — Repository naming and stakeholder expectations
The repository is named `Power-BI-Data-Analysis-and-Inventory`, but Power BI is
explicitly out of scope for Milestone 1.

### B7 — Amazon SKU data source *(partially answered by ADR 0011)*
[ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)
makes the read-only SP-API listings retrieval a source for
`marketplace_listings`. Still open: whether *every* Amazon SKU the client sells
is visible through that account and marketplace set, or whether some still
need import or manual entry.

### B8 — Amazon SP-API credentials *(new; blocks any live POC run)*
The client must register an SP-API application (or authorise an existing one)
and supply the LWA client id, client secret, refresh token, seller id, and
marketplace id(s) as environment variables on the machine or service that runs
the ingestion. Until then the POC can be built and tested only against mocked
responses. None of these values will ever be committed, logged, or printed.

---

## 8. Assumptions

A1–A17 carry forward from earlier phases, with these changes:

| # | Change |
|---|---|
| **A6** | **Superseded.** The roles are `ADMIN`, `PURCHASING_MANAGER`, `DATA_OPERATOR`, `VIEWER` — not admin/reviewer/viewer. There is no local password login: the development backend issues tokens without credentials, and Entra ID will own authentication ([security.md §4](security.md)). |
| **A8** | **Superseded.** Single-tenancy no longer holds: the schema is organization-scoped throughout ([ADR 0009](decisions/0009-organization-scoped-multi-tenancy.md)). |
| **A15** | Confirmed for now — `import_files.storage_uri` is backend-agnostic, so local filesystem today and S3 later needs no schema change. |
| **A18** *(new)* | Approved mappings live on the owning row rather than in a separate mapping table, so full mapping *history* is reconstructed from `audit_events`. If that becomes a common query, a dedicated history table is the follow-up ([ADR 0010](decisions/0010-identifier-model-and-mapping-placement.md)). |
| **A19** *(amended 2026-09-14)* | Tenant isolation is enforced in the repository layer by `app/repositories/scoping.py`, which every repository must use ([ADR 0012](decisions/0012-tenant-scoping-enforced-in-repository-layer.md)); unit and integration tests fix the rule in place. PostgreSQL row-level security remains **deferred** until the first multi-tenant deployment, when it is added in addition to the helper, not instead of it. |
| **A20** | `pg_trgm` is available in every target environment. It ships with PostgreSQL contrib and is present in both `postgres:16-alpine` and the EDB Windows build. |
| **A21** *(new)* | Microsoft Entra ID is the intended production identity provider. The `AuthenticationBackend` protocol is shaped for it; if a different provider is chosen, the protocol still holds but the JWKS/RS256 assumptions in `EntraIdAuthenticationBackend` would change. |
| **A22** *(new)* | The service sits behind a proxy that sets `X-Forwarded-For`. Exposed directly, that header is caller-supplied and the recorded client address is worth nothing (the value is validated, so a bad one is simply dropped). |

The full A1–A17 list is unchanged from the 2026-09-07 revision and remains in
force.

---

## 9. Recommended next step

> **Superseded by §10.** The order below is the Milestone 1 order, which now
> follows the Amazon proof of concept.

**Phase 2 — the vendor database.** It is fully unblocked and is the natural
first consumer of everything phase 1 built: CRUD under `/api/v1/vendors`, guarded
by `require_roles(RoleCode.DATA_OPERATOR)`, with each mutation wrapped in
`transaction()` alongside an `audit.record_change()` call. That exercises the
audit writer, the transaction utilities, the role guard, and the error envelope
against real endpoints, which is the honest test of whether the foundation is
usable rather than merely present.

Phase 4 (import profiles), the upload half of phase 5 and phase 6 (parsing,
validation, report) all landed on 2026-09-15. Parsed jobs now sit RUNNING at
stage MATCHING. Next is phase 7: the vendor-row matching engine over the
priority chain, recording `match_result` / `matched_by` / `match_priority` on
each row and feeding the exception queue; after it, the worker claim loop
(AC-5.7) and the immutable `import_report` (AC-9.3). B5 still decides where
deployed files live.

---

## 10. Re-planning (2026-09-14)

The client has requested an **Amazon SP-API proof of concept** as the first
technical milestone. This is a scope change — Amazon SP-API is listed as out of
Milestone 1 in [CLAUDE.md §3](../CLAUDE.md) and
[milestone-1-scope.md §3](milestone-1-scope.md) — and
[ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)
records it: exactly what the bounded, read-only ingestion includes, what stays
out (no writes to Amazon, no buyer PII, no replenishment math), and the new
tables, dependencies (`python-amazon-sp-api`, APScheduler 3.x), and
configuration it brings. CLAUDE.md §2 now lists it as item 14 and §3 narrows
the Amazon exclusion accordingly. The work order is now
**(a)** the Amazon POC, then **(b)** Milestone 1 phases 2 through 11 in the
order documented in [architecture.md §6](architecture.md#6-implementation-order).
Nothing already built is affected: phases 0, 1, and the phase 3 diagnostic stand
as delivered. The blocking questions in §7 remain open, B7 now has a partial
answer, and B8 (SP-API credentials) is new. Still to do before the POC starts:
an AC-14 group in [acceptance-criteria.md](acceptance-criteria.md) and a
credential section in [security.md](security.md), both listed as follow-ups in
the ADR.
