# Phase 1 Status

Living document. It reflects **what is true**, not what is intended.
Last updated: 2026-09-22 (Amazon velocity HTTP endpoint and local verification)

---

## 1. Overall

| | |
|---|---|
| Milestone | 1 — Data foundation, ingestion, matching |
| **Milestone 1 status** | **Not complete.** Phases 0, 1, 2, 4, 6, 7, 8, 9 are complete and phase 11 (hardening) is done as far as it can be without live data; phase A and phase 5 are complete against fakes / without a worker; phase 3 is a diagnostic only (the Nineyard sync is blocked on B1) and phase 10 lacks the products screen for the same reason. Of the 85 acceptance criteria, **58 are proven by a named test or command, 13 are proven in part** (the automated half passes; the manual demonstration on real vendor data, or a stated deviation, is outstanding) **and 13 are open** — the whole of AC-3, AC-2.3/2.4/2.5, AC-5.7, AC-9.3, AC-13.3 (§11). None of the four Milestone-level exit-gate items is met yet: the current `main` has not run in CI since 2026-09-14, the real-data walkthrough has not happened (B4), phase 3 is not built and nine blocking questions are open. §11 says exactly why for each. |
| Stage | **Phases 0–2, 4, 6–9 complete; 11 done; A, 5, 10 built with a named gap each; 3 diagnostic only.** Backend is deployed to Railway. |
| Application code | Schema, audit service, auth foundation, error handling, redaction, read-only Nineyard client + CLI probe, tenant scoping helper for repositories (ADR 0012), Amazon SP-API configuration, read-only SP-API client, the three ingestion services, the listings→product mapping, the shared identifier normaliser, the sales-velocity service, an APScheduler runner behind a `JobRunner` protocol, the `amazon_poc` CLI, the vendor database API, versioned import profiles with typed rule shapes, CSV/XLSX readers and a validate-against-sample preview (ADR 0013), file upload with byte-identical raw retention behind a `StorageBackend` (ADR 0004), profile-driven parsing of CSV/XLSX into `import_job_rows` with coded per-row validation and the import report, and the deterministic matching engine (`app/matching/engine.py`) with the import matching step that attributes every row and feeds the exception queue, and the exception-queue API through which a purchasing manager approves (with explicit, audited supersession), rejects or defers an item — an approval is the permanent mapping the next import matches at priority 3; and the snapshot stage that closes an import — append-only inventory snapshots, availability events on every transition, the OOS watchlist and its status history, and the availability feed. **The import lifecycle is complete end to end**: upload → parse → match → snapshot → COMPLETED. The admin interface now has working screens for every one of those steps — sign-in, vendors, import profiles, imports, exception queue, watchlist, availability, audit log, dashboard — and a Playwright walk-through drives the whole Milestone 1 flow through them. Products is the one remaining placeholder (its API is phase 3, B1). Phase 11 made the pipeline fast enough for real files — **50,000 rows in 38.8 s (CSV) and 56.5 s (XLSX) at ~110 MB peak**, down from 15 min 56 s — proved every interface query index-backed, rehearsed backup and restore, and added `docs/runbook.md`, `seed_demo`, `perf_import` and `explain_queries`. |
| Database schema | 25 tables, 23 enum types, 136 indexes, 82 check constraints, 83 foreign keys, 1 append-only trigger |
| Migrations | 7 revisions (`506fd0ecc33a`, `3767ee979011`, `3de5c4e5def0`, `44c932e601b0`, `fadb756b1b85`, `fab7311199fc`, `52e787f49770`), applied and reversed against PostgreSQL 16.15; also applied by `pg_restore` of a live dump and confirmed with `alembic current` / `alembic check` (§3, phase 11) |
| Backend tests | **1111 passed** (`pytest`: 702 unit + 409 integration). Plus one Playwright end-to-end test (`frontend/e2e`), passed twice locally against the running stack. |
| Quality gates | 8 of 8 passing locally (§4, 2026-09-16); the same gates were green in GitHub Actions on 2026-09-14 and `main` has not been pushed since |
| Docker stack | **Verified in CI** — full `docker compose up --build` from `.env.example`, API healthy against PostgreSQL, migration applied and checked, web answering (§4, §6 S1). Backend also live on Railway (§6 S2). |
| Blocking questions open | 9 (see §7); B1 partially answered, B2 narrowed, B3 decided for Milestone 1, B7 partially answered by ADR 0011, B9 new (admin screens need their foundation) |

The schema, the audit writer, the security foundation, and a read-only Nineyard
diagnostic exist and are verified. A vendor file can be uploaded, retained byte for
byte, parsed through its profile into validated rows, matched to products
through the priority chain, snapshotted, diffed into availability events that
reach the watchlist, and reported on; a reviewer can resolve the queue through
the API or the screens. Nothing yet synchronises Nineyard data; that, and the
products screen it would feed, is what stands between here and the Milestone 1
exit gate (§11).

---

## 2. Phase tracker

Phases are defined in [architecture.md §6](architecture.md#6-implementation-order).

| # | Phase | Status | Notes |
|---|---|---|---|
| — | Planning and documentation | ✅ Complete | Scope, architecture, criteria, 13 ADRs |
| 0 | Scaffolding | ✅ Complete | Backend, frontend, infra, quality gates, GitHub Actions CI (2026-09-14) |
| 1 | DB foundation + audit | ✅ Complete | Schema, migration, transactional audit writer, transaction utilities, config/security foundation |
| A | Amazon SP-API read-only ingestion ([ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)) | 🟨 Implementation complete; live validation pending | Precedes phase 2 by client request (§10). **Exists:** settings, redaction, the read-only client, the tables, the three ingestions, listings→product mapping, the velocity service, scheduled jobs with stale-run recovery, the `amazon_poc` CLI, and authenticated `GET /api/v1/amazon/velocity` ([amazon-integration.md](amazon-integration.md)). **Not yet done:** a single run against the real seller account (B8) — the remaining client-side proof. |
| 2 | Vendor database | ✅ Complete | `GET/POST /api/v1/vendors`, `GET/PATCH /vendors/{id}`, `POST /vendors/{id}/deactivate`, and the same shape under `/vendors/{id}/contacts`. Reads for every role, writes for `DATA_OPERATOR`; every mutation audited in its own transaction; a vendor with active import profiles cannot be deactivated. AC-4.1–4.4 covered by 35 route tests. |
| 3 | Nineyard integration + sync | 🟨 Diagnostic only | **Exists:** read-only client (`app/integrations/nineyard/client.py`, `errors.py`, `sanitize.py`), probe (`app/integrations/nineyard/probe.py`), and CLI (`app/cli/nineyard_probe.py`), tested by `tests/unit/test_nineyard_client.py`, `test_nineyard_probe.py`, `test_nineyard_cli.py` (mocked; no live calls). The public OpenAPI spec has been analysed ([nineyard-field-mapping.md](nineyard-field-mapping.md)). **Does not exist:** any sync service — nothing writes Nineyard data to `products`, `product_identifiers`, `marketplace_listings`, `nineyard_sync_runs`, or `nineyard_item_payloads`. The probe has not been run against the live API. See [nineyard-integration.md](nineyard-integration.md) and B1. |
| 4 | Import profiles | ✅ Complete | The six JSONB rule columns have fixed Pydantic shapes with JSON Schema export ([ADR 0013](decisions/0013-import-profile-rule-shapes.md)); `GET/POST /vendors/{id}/import-profiles`, `GET/PATCH …/{profile_id}`, `POST …/deactivate`, `POST …/validate` (stored and draft) and `GET /import-profiles/rule-schemas`. Editing creates version n+1 and retires n, audited. AC-6.1–6.4 covered by 46 route tests and 70 rule/reader/mapper tests. Real vendor files (B4) would still sharpen the defaults. |
| 5 | File ingestion + raw retention | 🟨 Complete in-request; no worker | `StorageBackend` protocol with a local backend; `POST /api/v1/imports` retains the bytes before recording anything, dedupes by SHA-256, creates a `PENDING` job and — when a profile is named and `IMPORT_PROCESS_ON_UPLOAD` is on — parses it in the same request; `GET /imports`, `GET /imports/{id}`, `GET /imports/{id}/raw`. AC-5.1–5.6 and 5.8 covered. **Not built:** the worker claim loop and restart-safe resumption (AC-5.7); the `JobRunner` from the Amazon work is the intended host. |
| 6 | Parsing + validation + reporting | ✅ Complete (to the matching boundary) | Streaming CSV/XLSX readers with the file's own row numbers; `app/imports/extract.py` applies the profile and raises coded issues (`UPC_INVALID`, `QUANTITY_INVALID`, `QUANTITY_NEGATIVE`, `PRICE_INVALID`, `IDENTIFIER_MISSING`, `AVAILABILITY_UNKNOWN`, `DUPLICATE_IN_FILE`); `process_import_job` writes `import_job_rows` in 1,000-row transactions, fails the job before any row on a missing column or signature mismatch, reconciles counters from the rows, and leaves the job RUNNING at stage MATCHING; `GET /imports/{id}/report` and `GET /imports/{id}/rows`. AC-9.1, 9.2, 9.4, 9.5 covered by 31 tests including 20,000-row CSV and XLSX runs. **Not built:** the immutable `import_report` row (AC-9.3) and the matched-by-rule counts it needs — after phase 7. |
| 7 | Matching engine | ✅ Complete | `app/matching/engine.py` — pure `evaluate(MatchInput, lookups) -> MatchOutcome`, rules 1→4 in strict order, first single hit wins, more than one hit is AMBIGUOUS at that priority with no fall-through, rule 5 (pg_trgm name similarity) only when 1–4 yield nothing and only ever SUGGESTION_ONLY; every rule tried is on the trail. `MatchIndexRepository` builds the lookups once per import from checksum-valid identifiers, active catalog numbers, APPROVED vendor mappings and APPROVED listings. `match_import_job` attributes every OK/WARNING row, maintains `vendor_products` (an APPROVED mapping is never touched), and opens one queue item per vendor line. The Amazon listing resolver now runs on the same rule functions. AC-7.1–7.6 covered by 21 engine cases and 8 integration tests incl. determinism. The trail is stored on the row (`normalized_data.match`), not in a separate `match_attempt` table — see A23. |
| 8 | Exception workflow | ✅ Complete | `GET /api/v1/exceptions` (filters: status, reason, vendor, import job, age; deferred items hidden by default), `GET /{id}` (source row, candidates with product names and scores, the rule trail, the vendor line), `POST /{id}/approve` `{product_id, supersede?, note?}` → APPROVED mapping with `MANUAL_APPROVAL`, approver and UTC time on the vendor line (or listing), item APPROVED, source row MATCHED at priority 5, job counters moved; `POST /{id}/reject` `{note}`; `POST /{id}/defer` `{until, note?}` (migration `fab7311199fc`). Supersession needs `supersede: true` and is two audited steps. AC-8.1–8.7 and **Milestone 1 exit criterion 7** covered by 20 tests. The queue screen is built (phase 10). Assignment is stored but has no route. |
| 9 | Inventory, availability, watchlist | ✅ Complete | `snapshot_import_job` writes one append-only `vendor_inventory_snapshots` row per vendor line (matched or not — AC-11.7), diffs against the line's previous snapshot, raises exactly one `availability_events` row per transition (first seen with stock → BECAME_AVAILABLE), links it to the watchlist, and closes the job COMPLETED / COMPLETED_WITH_ERRORS. `POST/GET /api/v1/watchlist`, `POST /{id}/remove`, `GET /{id}/history`, `GET /api/v1/availability/events` (vendor, product, watchlist-only, since). `max_unit_cost` flags (`over_max_unit_cost`, migration `52e787f49770`), never drops. AC-10.1, 10.2, 10.4, AC-11.1–11.4, 11.6, 11.7 and **exit criterion 9** covered by 22 tests. The watchlist and availability screens are built (phase 10). **Not built:** event acknowledgement. |
| 10 | Admin interface | 🟨 Built except Products | Development sign-in, typed API client, react-query; working screens for vendors, import profiles (form generated from the rule JSON Schemas, validate-against-sample preview, header pinning), imports (drag-and-drop upload, job list, detail with report, row browser by status, raw download), exception queue (filters, drawer with the raw row, candidates with scores, rule trail, approve / reject / defer, supersede prompt), watchlist (add / remove / history), availability feed, audit log (filters, before/after diff), dashboard (live counts, last import per vendor, sync status). `python -m app.cli.seed_dev` seeds an organization, four users and one demo product. **Playwright walk-through** (`frontend/e2e/milestone-1.spec.ts`) drives the whole Milestone 1 flow and runs in the `compose-smoke` CI job. **Not built:** the products screen (needs the phase 3 product API — B1) and product search by name in the approve / watch forms (B9); production sign-in (B2). |
| 11 | Hardening | ✅ Done (what can be done without live data) | 50,000-row CSV and XLSX imports timed stage by stage and brought under the 60 s / 500 MB budget (`python -m app.cli.perf_import`); `EXPLAIN (ANALYZE, BUFFERS)` of the exceptions queue, availability feed, product search, catalog lookup, row browser and job list — zero large sequential scans (`python -m app.cli.explain_queries`); `pg_dump` → `pg_restore` → `alembic current` / `check` → integration suite on the restored copy, rehearsed and written up in [runbook.md](runbook.md) with rotation, re-run and stuck-run procedures; `python -m app.cli.seed_demo` (two vendors, CSV + XLSX, three imports, a watch that flips); architecture.md §5 reconciled with the shipped schema; **every exit-gate item and every AC criterion mapped to its proof in §11**. Not done here because it cannot be: the velocity view's plan (no Amazon data — B8), the real-data walkthrough (B4), the CI run (push). |

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

### Phase 11 — hardening, runbook, seed data and the exit gate (2026-09-16)

No new feature. The pipeline was made fast enough for real files, the
queries the screens lean on were read as plans rather than assumed, backup
and restore were rehearsed rather than described, and every acceptance
criterion was walked to the test that proves it (§11).

#### 50,000 rows

`python -m app.cli.perf_import --rows N --format csv|xlsx` generates a
file (half the rows carrying the demo UPC so that rule 1 fires, the rest
with descriptions so that rule 5 runs), pushes it through
`receive_upload` → `process_import_job` → `match_import_job` →
`snapshot_import_job` with the services themselves, and prints wall time per
stage, the number of SQL statements each stage issued (a
`before_cursor_execute` counter; `--histogram` groups them by statement
shape), and peak RSS. Nothing about the measurement lives in the services.

| Run (50,000 rows) | Parse | Match | Snapshot | Total | Statements | Peak RSS |
|---|---|---|---|---|---|---|
| **Before** — CSV, as shipped by phase 9 | 54 s | 8 min 32 s | 6 min 30 s | **15 min 56 s** | tens of thousands | not measured (the run died printing `→` to a cp1252 console) |
| **After** — CSV, re-import for a vendor with existing lines | 9.6 s | 18.1 s | 10.9 s | **38.8 s** | 409 | 109.7 MB |
| **After** — XLSX, first import for a new vendor | 9.3 s | 28.2 s | 17.0 s | **56.5 s** | 359 | 113.2 MB |

Both are inside the brief's 60 s / 500 MB budget. The XLSX first import is
the slower path — every line and every queue item is new — and it is the
closest to the budget; a real vendor file with a higher share of
identifier-less rows would push rule 5 harder. Intermediate runs on the way
down: 169 s, 73 s, 60.3 s, 90.4 s (a regression from over-eager identity
map flushing, reverted), 74.8 s, 64.4 s.

What was wrong, in order of cost:

* **The matcher issued one to four statements per row** — the vendor line
  lookup, the open-item lookup, the rejected-item lookup and the rule-5
  trigram query — and the unit of work then inserted every new
  `vendor_products` and `product_mapping_exceptions` row one statement at a
  time. It now loads the chunk's context in three queries (lines by
  normalised SKU, open items and latest rejected items for those lines),
  prefetches rule-5 suggestions for the whole chunk with **one** `LATERAL`
  trigram query (`MatchIndexRepository.suggest_many`), assigns UUIDs
  client-side and writes new lines and items with Core `executemany`, updates
  the chunk's rows with one bulk `UPDATE … WHERE id = :id` and touches
  `last_seen_at` with one statement. The session's identity map is released
  chunk by chunk (`identity_snapshot` / `release_since` in
  `app/db/transaction.py`), which is what keeps memory flat at 110 MB
  instead of growing with the file.
* **The snapshot stage did the same** — a query per line for the previous
  snapshot, a query per event for the watch entries, and per-row inserts.
  Now: one `DISTINCT ON` query for the chunk's latest snapshots, one for the
  lines this job already covered, Core `executemany` for snapshots and
  events, and one watch lookup per chunk (`watchlist.apply_events`).
* **The parser's bulk insert was silently per-row.** SQLAlchemy's ORM bulk
  insert falls back to one statement per distinct set of present keys, and
  rows with different `None` patterns (a cost here, a description there)
  produced dozens of shapes per chunk. `insert(...).execution_options(render_nulls=True)`
  restores one `executemany` per 1,000 rows. Parse went from 54 s to 9.6 s
  on that alone.

One behaviour changed, deliberately: an open queue item is now **left
alone** when a re-import produces the same reason and the same candidates
(counter `exceptions_unchanged` in the matching summary); it is refreshed —
new evidence, new `import_job_id` — only when something differs. The
previous code rewrote every open item on every import, which was most of the
matching cost on a re-import and made `updated_at` meaningless.
`test_a_second_import_refreshes_open_items_instead_of_duplicating_them` now
asserts both halves. Two defects in the rework were caught by the suites
before any run finished: the bulk row `UPDATE` referenced vendor lines the
session had not flushed yet (FK violation), and an `expunge_all()` detached
the job the next chunk needed (`DetachedInstanceError`) — hence the
snapshot/release pair instead.

#### Plans, not guesses

`python -m app.cli.explain_queries --org-slug demo` compiles the repository
statements the interface actually runs — the exception queue view, the
availability feed, the trigram product search (rule 5), the catalog-number
lookup, the row browser (job + status) and the job list — with literal
binds, runs `EXPLAIN (ANALYZE, BUFFERS)` against the current database and
flags any sequential scan whose estimate exceeds 10,000 rows. Against the
database after the 50,000-row runs (249,000 PENDING queue items, 245,000
availability events, 1.9 million rows across the import tables):

| Query | Plan (`EXPLAIN (ANALYZE, BUFFERS)`, 2026-09-16) | Execution |
|---|---|---|
| Exceptions list (queue view) | `Index Scan using ix_product_mapping_exceptions_status_created_at` — 1,001 rows read to fill 50 after the deferral filter, of 250,015 estimated | 1.8 ms |
| Availability feed (30 days, newest first) | `Index Scan Backward using ix_availability_events_organization_id_detected_at` — 97 rows read of 244,979 | 0.2 ms |
| Product search (trigram, rule 5) | `Index Scan using uq_products_organization_id_catalog_item_number` — the planner walks the five-product demo catalogue through its unique index and computes `similarity()` per row; `ix_products_name_trgm` is not worth choosing at that size | 0.6 ms |
| Product lookup by catalog number | `Index Scan using uq_products_organization_id_catalog_item_number` | 0.02 ms |
| Import rows by job and status | `Index Scan using uq_import_job_rows_import_job_id_row_number`, status as a filter | 0.03 ms |
| Import job list | **`Seq Scan on import_jobs`** — 37 rows, below the threshold and correct at that size | 0.06 ms |

**Zero large sequential scans.** No index was added: the schema's indexes
(database-schema.md §5) were already the right ones; what phase 11 fixed
was the number of times they were consulted, not whether they were. **Not
evaluated, and said so:** the sales-velocity view — there is no Amazon data
to plan against until B8 is answered, and a plan over empty tables says
nothing; and the trigram search **at catalogue scale** — the products table
will not have thousands of rows until the Nineyard sync exists (B1), so
whether the planner picks `ix_products_name_trgm` then is still to be
observed (`explain_queries` is the tool to observe it with).

#### Backup and restore, rehearsed

Written up as procedure in [runbook.md](runbook.md) §1–2; the numbers:
`pg_dump -Fc` of the development database — 132.8 MB in **17.6 s**;
`pg_restore` into a fresh `prms_restored` — **78.3 s**; row counts of all
fourteen operational tables identical between source and copy;
`alembic current` on the copy → `52e787f49770 (head)`; `alembic check` →
"No new upgrade operations detected"; the integration suite with
`DATABASE_URL` pointed at the restored server: **408 passed** in three
consecutive runs. The first of four runs had **one failure whose output was
not captured** — the run was not saved before the retry — so it is recorded
here as an unexplained failure, not as a flake: the same suite has passed on
the primary database in every full run since (§4), and the restored copy
was dropped after the rehearsal along with its scratch test database. The
dump lives under `storage/diagnostics/`, which is git-ignored. Point-in-time
recovery is not configured and the runbook says so.

The runbook also covers rotating `AUTH_JWT_SECRET`, the Amazon credential
triple and the Nineyard credentials (verify with `amazon_poc auth` /
`nineyard_probe`, never by reading a log), re-running a failed Amazon sync or
vendor import (both idempotent by design), and clearing a `RUNNING` row a
dead process left behind — automatic for Amazon (`recover_stale_runs`),
SQL for an import job until AC-5.7's resume exists.

#### `seed_demo`

`python -m app.cli.seed_demo` (refused unless `DEV_AUTH_ENABLED`) builds on
`seed_dev`: four more products with real-check-digit UPCs, vendor
**NORTHWIND** with a CSV profile (`UPC | Description | Quantity | Cost`,
`$` stripped) and vendor **CONTOSO** with an XLSX profile (`Vendor SKU | UPC
| Qty Available | Wholesale Price`, sheet "Price List") — the brief's two
layouts — a watch on DEMO-001 with a 5.00 ceiling, and three imports run
through the whole lifecycle in order: Northwind Monday (Blue Widget at 0),
Contoso Monday, Northwind Tuesday (Blue Widget at 40 → the watch flips to
IN_STOCK, one BECAME_AVAILABLE event). It is idempotent: the fixture bytes
are stable across runs — openpyxl stamps the workbook with the current time,
so the XLSX writer pins the document properties and rewrites the zip entries
with a fixed date — and a second run reports every import as "already
imported" with nothing new. `TestSeedDemo` runs it twice and checks the
watch, the event and the counts. This is the two-vendors-two-formats
rehearsal the exit gate's item 2 asks for, on synthetic data; it does not
replace the walkthrough on real files (B4).

#### Documents

[architecture.md §5](architecture.md) no longer describes a superseded
sketch: it carries a planned-vs-shipped table for the 25 tables and points
at [database-schema.md](database-schema.md) for the authority. §11 below is
the exit-gate evidence.

### Phase 10 — the admin interface and the walk-through (2026-09-16)

Every screen except Products, built on a foundation that did not exist
before this step (the vendors-screen prompt was never run), plus the
Playwright walk-through of the Milestone 1 exit gate.

* **Foundation** (`frontend/src/lib`) — `api.ts`: one typed client that
  knows the base URL, the bearer token and the error envelope, so screens
  react to `mapping_supersession_required` by name; `auth.tsx`:
  development sign-in through `POST /auth/dev-token` (email of an existing
  user, no password — B2 replaces this), the principal from `/auth/me`,
  `hasRole` with ADMIN passing everything; `queries.ts`: react-query hooks
  for every list and detail; `types.ts`: the response shapes, hand-written
  from `app/schemas`. The shell shows the sign-in screen until a token is
  held and the signed-in email afterwards.
* **Screens** — vendors (search, status filter, create / edit / deactivate
  in a drawer, link to a vendor's profiles); import profiles (per vendor,
  retired versions on request, editor whose six rule sections are
  **generated from the JSON Schemas** the API serves — enums as selects,
  booleans as checks, `column_map.columns` as an editable table — with
  validate-against-sample preview and header pinning; saving an active
  profile creates the next version); imports (drag-and-drop or click upload
  with vendor and profile selectors, job list with vendor / status filters,
  detail with the report counters and issue breakdown, row browser filtered
  by status showing each row's match and deciding rule, raw download with
  the original filename); exception queue (status / reason / vendor /
  deferred filters, drawer with the row as imported, the vendor line's
  mapping, candidates with catalog numbers and similarity, the rule trail,
  approve a candidate or a typed product id, reject with a required note,
  defer to a time; a `mapping_supersession_required` refusal turns into a
  supersede tick-box; an approved item offers "Watch this product");
  watchlist (add with vendor / priority / quantity / cost ceiling, remove,
  status history drawer; arrives pre-filled from an approved exception);
  availability feed (vendor, watched-only, since; each event with its
  quantities, watch link and cost-ceiling flag); audit log (entity, action,
  actor, entity id, date range; a before/after diff highlighting changed
  fields); dashboard (open exceptions, watched items back in stock in 7
  days, watched in stock / watched, imports running, last import per
  vendor, Amazon and Nineyard sync status — every number a live count).
* **Backend reads the screens needed** — `GET /api/v1/audit/events`
  (entity type / id, action prefix, actor email or label, date range;
  distinct entity types and actions for the filters) and
  `GET /api/v1/dashboard`. `python -m app.cli.seed_dev` (refused unless
  `DEV_AUTH_ENABLED`) creates an organization, the four roles' users
  (`admin@`, `buyer@`, `operator@`, `viewer@example.test`) and one product
  — "Blue Widget 12 pack", DEMO-001, UPC 012345678905 — so the walk-through
  has something to match before the Nineyard sync exists.
* **Playwright** (`frontend/e2e/milestone-1.spec.ts`, `playwright.config.ts`)
  — one serial test, 90 s budget, fixture CSVs built in memory: sign in →
  create vendor → create profile from the schemas and validate it against
  the fixture → upload → report and row browser → open the exception →
  approve the suggested product → re-upload (header case changed so the
  bytes differ) → the row shows *vendor sku mapping (p3)* and no new
  exception → "Watch this product" pre-fills the watch form → upload the
  now-available fixture → the availability event shows *watched*, *0 → 40*
  and the product name → the watch row reads *in stock* → the dashboard's
  newly-available count is non-zero → the audit log shows
  `mapping_exception.approved` with a before/after diff. Every created name
  carries a per-run suffix so it can run repeatedly against one database.
  **Passed twice locally** (12.1 s, 9.1 s) against `uvicorn` + `next dev` on
  the development database.
* **CI** — the `compose-smoke` job now seeds the stack (`seed_dev`),
  installs Chromium, runs the walk-through against
  `http://localhost:3000`, and uploads the Playwright report and traces on
  failure. Not yet observed green in GitHub Actions: `main` has not been
  pushed since 2026-09-14 (see §4).

**Two things found on the way.** (1) Port 3000 on this machine is held by
an unrelated project's dev server; the local run used `next dev --port
3100` with `CORS_ALLOW_ORIGINS` widened for the session. CI uses 3000. (2)
The exception drawer originally showed the "Watch this product" link only
right after approving; it now shows on every approved item, which is what
the walk-through (and a reviewer coming back later) needs.

**Not built.** The products screen — there is no product API until the
Nineyard sync (phase 3, B1); the approve and watch forms take a product id,
with candidates offered where the engine found any. Production sign-in
(B2). The e2e test has not yet run inside GitHub Actions.

### Phase 9 — snapshots, availability events and the watchlist (2026-09-16)

The last stage of an import, and the brief's "Product ABC is now available
from Vendor B". Backend complete; the two screens the prompt asked for are
not built — same reason as phase 8, see B9.

* **Migration `52e787f49770`** — `availability_events.over_max_unit_cost`
  (nullable boolean): set when an event is linked to a watch entry with a
  cost ceiling; true means the vendor's cost is above it.
* **`app/services/snapshots.py`** — `snapshot_import_job` requires RUNNING
  at stage SNAPSHOTTING. One snapshot per vendor line the job touched
  (every OK/WARNING row with a `vendor_product_id`, matched or not, with
  `product_id` where matched — AC-11.7), `effective_at` = the file's upload
  time, `captured_at` = now; the unique `(import_job_id, vendor_product_id)`
  index plus an existence check mean an interrupted stage cannot
  double-write. The diff is one function, `transition(previous, current)`:
  AVAILABLE from nothing / OUT_OF_STOCK / DISCONTINUED / UNKNOWN →
  `BECAME_AVAILABLE`; OUT_OF_STOCK or DISCONTINUED from AVAILABLE →
  `BECAME_UNAVAILABLE`; anything involving a move *to* UNKNOWN, or no
  change → nothing. Each event references both snapshots; the unique
  `(current_snapshot_id, event_type)` index is the backstop (AC-11.4).
  The job then closes: `COMPLETED`, or `COMPLETED_WITH_ERRORS` when
  `error_rows > 0`; `current_stage` null, `completed_at` set, audited
  `import_job.completed` with the snapshot/event/watch-hit counts in
  `error_details.snapshotting`.
* **`app/services/watchlist.py`** — add (product active, vendor exists,
  one active entry per product / product+vendor by the partial unique
  indexes; `current_status` initialised from the latest snapshots), remove
  (deactivated with who and when, never deleted — AC-10.2), list, history;
  both mutations audited (AC-10.4). `apply_event` links each event to the
  most specific active entry (vendor-specific before all-vendors), moves
  every matching entry's `current_status` — IN_STOCK on BECAME_AVAILABLE;
  OUT_OF_STOCK on BECAME_UNAVAILABLE only when no vendor line the entry
  watches is still available — and writes one `oos_status_history` row per
  actual change. `max_unit_cost` is honoured by flagging: the event is
  linked with `over_max_unit_cost = true`, never dropped.
* **Routes** — `/api/v1/watchlist` (list with vendor / product /
  include_inactive; add; get; `POST /{id}/remove`; `GET /{id}/history`) and
  `/api/v1/availability/events` (vendor, product, watchlist_only, since;
  newest first, with vendor SKU and product name denormalised). Reads for
  every role; add / remove for PURCHASING_MANAGER.
* **Hook** — the upload request now runs parse → match → snapshot; a job
  uploaded with a profile comes back COMPLETED.

**Exit criterion 9, as a test** (`TestProductBecomesAvailable`): watch a
product with a ceiling of 5.00; import Vendor B showing 0 → one snapshot,
OUT_OF_STOCK, no event, watch still UNKNOWN; import Vendor B showing 40 at
4.50 → exactly one BECAME_AVAILABLE referencing both snapshots, linked to the
watch entry, `over_max_unit_cost = false`, entry IN_STOCK, one history row
UNKNOWN → IN_STOCK pointing at the snapshot, the feed shows it under
`watchlist_only`, audit rows for the watch and the completed jobs; the same
bytes a third time are a duplicate upload (nothing runs), and a
byte-different file with the same state is a new COMPLETED job with a third
snapshot and **no new event and no new history row**.

**Tests** (`tests/integration/test_availability_api.py`, 22): the flow
above; the state machine's twelve transitions; reverse transition
referencing the earlier snapshot with quantities, plus an unmatched line's
event with a null product (AC-11.7); COMPLETED_WITH_ERRORS when a row was
rejected; snapshot `effective_at` = the file's upload time and a
PRICE_INVALID cost stored as null; watchlist add / duplicate 409 / list
filters / remove with who-and-when / second remove 409 / row kept / audit
`watchlist.added` + `watchlist.removed` / VIEWER 403 / 401; unknown product
or vendor 404, bad quantity 422; a new watch starts IN_STOCK when the
latest snapshot says so; **`max_unit_cost` flags but never drops**;
OUT_OF_STOCK only once no vendor has it, with the history
UNKNOWN → IN_STOCK → OUT_OF_STOCK; feed filters and tenancy.

**A flake, fixed.** The migration round-trip test's intermittent setup
error (noted under phase 6) reproduced in this run and its cause is now
known: `drop_database` terminated every backend on the scratch database,
and an autovacuum worker there runs as a superuser, which the test role may
not terminate (`InsufficientPrivilege`). It now terminates client backends
only. Recorded under §4.

### Phase 8 — the exception queue API (2026-09-16)

The queue where a person decides what the engine could not (AC-8). Backend
complete; the screen the prompt asked for is **not** built — see the end of
this section.

* **Migration `fab7311199fc`** — `product_mapping_exceptions.deferred_until`
  (nullable TIMESTAMPTZ) and a partial index on it. `ExceptionStatus` keeps
  its three values; a deferred item is PENDING and hidden from the default
  queue view until its time.
* **`app/repositories/exceptions.py`** — the queue query (oldest first;
  status defaults to PENDING; filters by reason, vendor, import job and
  minimum age; deferred items excluded unless asked for), detail with the
  vendor line, listing, source row and products preloaded, candidate
  product lookup, and the active-product check an approval needs.
* **`app/services/exceptions.py`** — `approve`: refuses a non-PENDING item
  (409 `exception_not_pending`) or an unknown/inactive product (404
  `product_not_found`); sets the vendor line — or the marketplace listing —
  to APPROVED with `MANUAL_APPROVAL`, the approver and the UTC moment; the
  item APPROVED with the same approver and note; the originating row
  MATCHED by `MANUAL_APPROVAL` at priority 5 with the resolution recorded
  in its JSON; the job's counters move one row from exceptions to matches.
  **Supersession (AC-8.6):** if the line already has an APPROVED mapping to
  a *different* product the call is refused with 409
  `mapping_supersession_required` naming the current product, unless
  `supersede: true`; then the old mapping is first set to SUPERSEDED — its
  own audit row whose before/after name the old product — and only then
  approved to the new one. Nothing is deleted; the mapping's history is the
  audit log, as database-schema.md §3.3 says. `reject` requires a note,
  marks the item REJECTED, makes no mapping, and clears a PENDING
  suggestion the matcher had left on the line (UNMAPPED again). `defer`
  requires a future, timezone-aware `until`. Every action is one
  transaction with `before`/`after` audit rows: `mapping_exception.approved`
  / `.rejected` / `.deferred`, `vendor_product.mapping_approved` /
  `.mapping_superseded` / `.mapping_suggestion_rejected` (and the listing
  equivalents), `import_job_row.matched_manually`.
* **Re-review policy (AC-8.5)** — the matcher now remembers the latest
  REJECTED item per vendor line: the same reason with the same candidates
  is not re-queued (the row records `previously_rejected`); different
  evidence — a UPC that now points somewhere — opens a new item.
* **`app/api/v1/routes/exceptions.py`** — reads for every role; approve /
  reject / defer for PURCHASING_MANAGER (ADMIN passes). The list carries
  the row's vendor SKU, description, row number and age so the queue can be
  scanned; the detail carries the raw row, the extractor's values, each
  candidate with its product's catalog number and name and the rule that
  found it (with score for rule 5), the full trail and the vendor line.

**Milestone 1 exit criterion 7, as a test**
(`TestReimportAfterApproval`): a row with no identifier lands in the queue
as SUGGESTION_ONLY; a purchasing manager approves it; the line is APPROVED
to the product with approver and time, the row is MATCHED at priority 5,
the job's counters move; the same rows are imported again and the row
matches by `VENDOR_SKU_MAPPING` at priority 3 with the trail
`UPC skipped → CATALOG_ITEM_NUMBER skipped → VENDOR_SKU_MAPPING matched →
AMAZON_SKU_MAPPING skipped`, and no new exception exists.

**Tests** (`tests/integration/test_exception_api.py`, 20): the re-import
above; 401 on every route; VIEWER and DATA_OPERATOR read but every decision
is 403; ADMIN decides; another tenant's item is 404 and absent from the
list; filters (reason, vendor, import job, age, status, paging, bad
enum → 422); detail shows the raw row, candidates with catalog numbers,
the ambiguous trail and the vendor line; reject (blank note → 422, the
line's PENDING suggestion cleared to UNMAPPED, row verdict untouched,
second reject → 409, audited); a rejected line is not re-queued for the
same evidence but is for new evidence; defer (past → 422, hidden, visible
with `include_deferred`, back once the time passes, audited); approve
refuses unknown and inactive products; approving a PENDING suggestion makes
it permanent; **supersession** — refused without the flag with the current
product named and nothing changed, the same product re-affirmed without a
flag, and with `supersede: true` both steps audited with before/after
(`APPROVED → SUPERSEDED`, then `SUPERSEDED → APPROVED` to the new product)
and the next import using the new mapping; every unresolved row has
exactly one item with a reason (AC-8.1).

**What is not built, and why.** Item 4 of the prompt — replacing
`frontend/src/app/exceptions/page.tsx` with a queue screen "copying the
patterns from the vendors screen" — is not done. There is no vendors
screen: `/vendors` is the same placeholder as `/exceptions`, and the
frontend has no typed API client, no react-query, no zod, no dev sign-in
and no Vitest — the foundation Prompt 16 specified and stopped on because
its API did not exist. Building that foundation here, for one screen, would
have meant doing Prompt 16's infrastructure under a different commit
message without the vendors screen it was designed around. The vendors API
now exists, so Prompt 16 is unblocked; the exceptions screen needs, in
addition, a product search endpoint (by catalog number / UPC / name) that
does not exist yet — recorded as **B9**. Prompt 22's watchlist and
availability screens (item 6) are not built for the same reason.

### Phase 7 — the deterministic matching engine (2026-09-15)

One engine, two callers. Prompt 11's listing resolver had the only matching
code; its rule implementations now live in `app/matching/engine.py` and it
calls them, keeping the listing policy it documented (priority 4 first, a
UPC hit on a *listing* is a suggestion, a disagreement is a conflict). The
vendor-row import uses the engine's `evaluate()`, which is CLAUDE.md §5.1
verbatim.

* **`app/matching/engine.py`** — pure; no database, clock or randomness.
  `MatchInput` (normalised UPC + its checksum flag, catalog item number,
  vendor id + normalised vendor SKU, Amazon seller SKU, description);
  `MatchLookups` protocol with the four lookups plus `suggest_by_description`;
  `MatchIndex`, the in-memory implementation; `RuleEvaluation` (rule,
  priority, input, outcome, candidates, note); `MatchOutcome` mirroring
  `MatchResult` — MATCHED(product, method, priority), AMBIGUOUS(priority,
  candidates), UNMATCHED(trail), SUGGESTION_ONLY(scored candidates, trail).
  `evaluate()` runs rules 1→4 in order and stops at the first single hit;
  more than one hit ends evaluation as AMBIGUOUS at that priority and the
  weaker rules are recorded as *skipped* — never consulted. Rule 5 runs only
  when 1–4 all yield nothing: candidates with scores above pg_trgm's 0.3,
  at most five, never a match. Rule 1 refuses a UPC whose check digit
  failed. Candidates are sorted so lookup order cannot change an outcome.
* **`app/repositories/matching.py`** — `MatchIndexRepository.build(vendor)`:
  GTIN identifiers (active, checksum not false), active products' catalog
  numbers, this vendor's APPROVED `vendor_products`, APPROVED
  `marketplace_listings`; tenant-scoped. `suggest_by_description` is
  `similarity(products.name, :description)` through `ix_products_name_trgm`,
  ordered by score then id.
* **`app/services/matching.py`** — `match_import_job`: requires RUNNING at
  stage MATCHING; builds the index once; walks OK/WARNING rows in file
  order in 1,000-row transactions; skips rows superseded by a later
  duplicate SKU (the last occurrence carries the line). MATCHED rows get
  `match_result` / `matched_by` / `match_priority` / `product_id` (the
  deciding-rule CHECK). The vendor line is created UNMAPPED or refreshed;
  **an APPROVED mapping is never modified** — if the chain's product (a
  stronger rule) differs from the approved one, the row keeps the chain's
  answer and a `CONFLICTING_IDENTIFIER` item asks a person to reconcile.
  A match by UPC or catalog number gives the line a **PENDING** mapping
  and a `SUGGESTION_ONLY` item: per §5.2 the vendor-SKU mapping becomes
  permanent only through approval. AMBIGUOUS / UNMATCHED / SUGGESTION_ONLY
  rows open an item with the reason, candidates and full trail; a line with
  an open item is refreshed, not duplicated (the partial unique index is the
  backstop). The whole outcome is also written to the row's
  `normalized_data.match` without timestamps. Counters `matched_rows` /
  `exception_rows`, `error_details.matching` breakdown, stage →
  SNAPSHOTTING (RUNNING; phase 9's step), audit `import_job.matched`.
* **Hook** — the upload request now runs parse *then* match when a profile
  is named (`IMPORT_PROCESS_ON_UPLOAD`).

**A decision worth naming.** The priority order is CLAUDE.md's: a UPC hit
(1) outranks an approved vendor-SKU mapping (3). When the two disagree the
engine reports the UPC's product, and the service neither overwrites the
approval nor silently accepts the disagreement — it raises a conflict. Both
rules of §5 hold: matching is deterministic and approvals are permanent.

**Tests.** `tests/unit/test_match_engine.py` (21), each named for its rule:
exact UPC at priority 1; **a UPC matching two products is AMBIGUOUS at
priority 1 even though the vendor SKU would have matched**, with rules 2–4
recorded as skipped; a bad check digit is not an identifier; fall-through
on no hit; exact catalog number; approved vendor SKU reused on the second
import (same input, UNMATCHED before, MATCHED by rule 3 after); another
vendor's mapping ignored; approved Amazon SKU at priority 4; ambiguity at
4 never reaches rule 5; a description identical to a product name is
SUGGESTION_ONLY, never MATCHED; suggestions ranked, capped, thresholded;
rule 5 not run after a match; **empty input → UNMATCHED with four
evaluations recorded**; determinism over five input shapes; candidate
order independent of lookup order. `tests/integration/test_import_matching.py`
(8): the index admits only what the rules may read (bad checksum, inactive
identifier, inactive product, PENDING mapping, another vendor, PENDING
listing, another tenant all excluded); pg_trgm suggestions scored and
ordered; **every outcome recorded on rows, lines and the queue** for an
eight-row file (UPC match with PENDING mapping, ambiguous UPC across two
identifier types, approved SKU, name-only suggestion, nothing, a
superseded duplicate, a bad-UPC WARNING row); approved mapping untouched
and a disagreement raised as a conflict; a second import refreshes the
open item instead of duplicating it; not-at-MATCHING refused; the upload
hook parses then matches; and **determinism** — the same rows imported and
matched twice against the same mapping state give identical per-row
results, methods, priorities, products, trails and queue items.

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

**Observed once, later explained.** In one of four full-suite runs the
pre-existing `test_migrations.py::test_upgrade_then_downgrade_then_upgrade_succeeds`
errored at setup while dropping its scratch database. It recurred on
2026-09-16 with the real error visible — the test role cannot terminate an
autovacuum worker's superuser backend — and was fixed in the fixture (phase
9 section).

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
  blank when nothing sold. `GET /api/v1/amazon/velocity` now exposes the same
  read-only result to authenticated users at SKU or product level.
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

Run on 2026-09-16 via `.\tasks.ps1 check`. Windows 11, Python 3.12.10, Node 24.19.0, PostgreSQL 16.15.
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
| Backend format | `ruff format .` | ✅ 169 files unchanged |
| Backend lint | `ruff check .` | ✅ All checks passed |
| Backend types | `mypy` (strict) | ✅ No issues in 161 source files |
| Backend tests | `pytest` | ✅ `1111 passed in 45.33s` — `tests/unit`: `702 passed`; `tests/integration`: `409 passed`. The integration suite was also run four times against a `pg_restore`d copy of the development database on 2026-09-16: **408, 408, 408 passed** in runs two to four; **run one had one failure whose output was not kept** before the retry, so it is recorded as unexplained rather than dismissed (phase 11 section) |
| Migration apply | `alembic upgrade head` | ✅ All seven revisions applied to PostgreSQL 16.15; the same head confirmed on a restored dump with `alembic current` |
| Migration reverse | `alembic downgrade base` → `upgrade head` | ✅ Clean round trip, 0 residual enum types; one-step downgrades of `3767ee979011`, `3de5c4e5def0`, `44c932e601b0`, `fadb756b1b85`, `fab7311199fc` and `52e787f49770` each re-apply cleanly |
| Migration drift | `alembic check` | ✅ No new upgrade operations detected |
| Frontend lint | `npm run lint` | ✅ Clean |
| Frontend types | `npm run typecheck` | ✅ Clean |
| Frontend build | `npm run build` | ✅ 10 routes prerendered (12 static pages); every route except `/products` is now a real screen (2–5 kB each) |
| End-to-end | `npx playwright test` (frontend) | ✅ `1 passed` — the Milestone 1 walk-through, run twice locally against `uvicorn` + `next dev`; wired into `compose-smoke`, not yet run there |
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

### B9 — Product search for the approve and watch forms *(narrowed 2026-09-16)*
The admin screens and their foundation were built in phase 10 (this was the
first half of B9). What remains: the reviewer approving an exception, or a
buyer adding a watch, can only pick a product that the engine offered as a
candidate or paste a product id. A product search — `GET /api/v1/products?q=`
over catalog number, UPC and name — needs the product API, which arrives with
the Nineyard sync (phase 3, B1). Until then the demo product from `seed_dev`
is the only product a fresh environment has.

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
| **A23** *(new)* | The full rule trail of a match is stored on `import_job_rows.normalized_data.match` and on the queue item's `match_evaluations`, not in a separate `match_attempt` table as architecture.md §5.5 sketched. It is the same data, addressable by the row, and reproducible from the engine; a table can be split out if the audit UI needs to query trails across jobs. |
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

Phases 4, 5 (upload half), 6 and 7 landed on 2026-09-15; phases 8, 9, 10 and
11 on 2026-09-16. The import lifecycle is complete end to end and fast enough
for real files, every step has a screen, the Milestone 1 walk-through passes
as a Playwright test, and §11 says what is proven and what is not. Next, in
the order that closes the most of §11: **push `main` and watch the three CI
jobs** (gate item 1 — the compose-smoke job now runs the walk-through and has
not yet been seen green); **Nineyard sync when B1 is answered** (AC-2.3–2.5,
all of AC-3, the products screen and product search — gate items 2 and 3);
the **real-data walkthrough** once B4's files arrive (every [M] criterion);
then the worker claim loop (AC-5.7), the immutable `import_report` (AC-9.3),
generated API types (AC-13.3) and production sign-in (B2). B5 still decides
where deployed files live.

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

---

## 11. Milestone 1 exit gate — evidence (2026-09-16)

Every item of the Milestone-level exit gate and every criterion of
[acceptance-criteria.md](acceptance-criteria.md), each with the command or
test that proves it. Test names are `file::Class::test` under
`backend/tests/`; every named test is in the `1111 passed` run of §4. A
criterion is ✅ only when the evidence is a test or command that ran and
passed; 🟨 means the automated half passed and the manual demonstration, or
part of the wording, is outstanding — the note says which; ⬜ means nothing
proves it yet. **Nothing below is marked done on the strength of code
existing.**

Tally: **58 ✅ · 14 🟨 · 13 ⬜** of 85 criteria (AC-13.6 moved to part-proven on 2026-09-23).

### Milestone-level exit gate

| # | Gate | Status | Evidence / what is missing |
|---|---|---|---|
| 1 | All [A] criteria pass in CI on a clean checkout | ⬜ | The three CI jobs were last green on 2026-09-14 ([run 34892991973](https://github.com/cbfriedman/Power-BI-Data-Analysis-and-Inventory/actions/runs/34892991973)) at 302 backend tests; the current `main` (1111 tests, the Playwright job) has not been pushed. Locally `.\tasks.ps1 check` runs the same gates and is green (§4). **Missing: the push.** |
| 2 | All [M] criteria demonstrated against real data from two vendors with materially different formats (one CSV, one XLSX) | ⬜ | Rehearsed on synthetic data: `python -m app.cli.seed_demo` builds NORTHWIND (CSV) and CONTOSO (XLSX) with the brief's two layouts and runs three imports through the lifecycle (`test_admin_reads::TestSeedDemo`), and the Playwright walk-through drives every screen. **Missing: real vendor files (B4) and the walkthrough itself.** |
| 3 | phase1-status.md shows every phase complete with no open blocking questions | ⬜ | Phase 3 is a diagnostic (B1); phase 5 has no worker (AC-5.7); phase 10 has no products screen (B1/B9); phase A has not run against the real account (B8). Nine blocking questions are open (§7). |
| 4 | No item from milestone-1-scope.md §3 has been built | ✅ | `git grep -n -i -E "connectbooks\|walmart\|power ?bi\|analyzer\.tools\|rfq\|purchase_order\|replenish\|profitab" -- backend/app frontend/src` → the product's title string and one comment in `amazon_velocity.py` saying no replenishment quantity is computed. Amazon is read-only by construction (`tests/unit/test_amazon_client.py` asserts the method set and the absence of write-shaped names). |

### Scope summary criteria ([milestone-1-scope.md §4](milestone-1-scope.md))

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | `docker compose up` yields Postgres + API + web from a clean checkout | ✅ (2026-09-14) | `compose-smoke` CI job (§4): images built, `/api/v1/health` 200 with `postgresql: ok`, `alembic upgrade head` + `check` inside the container, web on :3000. Not re-run since the e2e step was added. |
| 2 | Nineyard catalog sync populates products with Catalog Item Number + UUID, raw payloads retained | ⬜ | Not built (B1). |
| 3 | Vendor and profile created entirely through the admin UI | ✅ | `frontend/e2e/milestone-1.spec.ts` steps *create a vendor*, *create an import profile from the rule schemas* (passed twice locally). |
| 4 | CSV and XLSX import end to end, raw file retained byte-identical | ✅ | `test_import_upload_api::TestRetention::test_csv_round_trip_is_byte_identical`, `::test_xlsx_round_trip_is_byte_identical`; `test_import_processing::TestBriefLayouts::test_layout_a_as_csv`, `::test_layout_b_as_xlsx`; `TestSeedDemo` (both formats through the whole lifecycle). |
| 5 | Priority chain, deciding rule recorded, uncertain rows to the queue | ✅ | `test_match_engine` (21), `test_import_matching::TestMatchImportJob::test_every_outcome_is_recorded_on_rows_lines_and_the_queue`, `test_exception_api::test_every_unresolved_row_produces_exactly_one_item_with_a_reason`. |
| 6 | No row auto-matched on description alone | ✅ | `test_match_engine::TestRule5Suggestion::test_a_description_identical_to_a_product_name_is_a_suggestion_never_a_match`. |
| 7 | Approved exception → permanent mapping used on the next import | ✅ | `test_exception_api::TestReimportAfterApproval::test_an_approved_exception_is_used_automatically_on_the_next_import`; Playwright step *re-upload: the row matches automatically at priority 3, no new exception*. |
| 8 | Batch report reflects totals, outcomes and errors | ✅ | `test_import_processing::TestEveryRowProblem::test_counters_statuses_and_codes_are_exact` (exact counters, breakdowns and summary sentence for a 12-problem file). The immutable report *row* is AC-9.3, open. |
| 9 | Watch a product, import a file that flips it, see the event | ✅ | `test_availability_api::TestProductBecomesAvailable::test_watch_then_zero_then_forty_raises_exactly_one_linked_event`; Playwright step *upload the now-available fixture and see the availability event*; `TestSeedDemo`. |
| 10 | Every CLAUDE.md §6 mutation in the audit log with actor, before/after, UTC | ✅ | See AC-12.1 below. |

### AC-0 — Foundation and environment

| ID | Status | Evidence |
|---|---|---|
| AC-0.1 [M] | 🟨 | `compose-smoke` on 2026-09-14 brought up `postgres`, `api`, `web` from `.env.example` with no manual step. **There is no `worker` service** — the criterion names one; it arrives with AC-5.7. |
| AC-0.2 [A+M] | ✅ | [A] `test_settings_secrets::test_credentials_are_typed_as_secrets`, `::test_repr_does_not_leak_secrets`, `::test_safe_dump_does_not_leak_secrets`, `::test_a_formatted_log_message_does_not_leak_secrets`; `test_config.py` (29). [M] `git grep -n -i -E "(password\|secret\|token\|api[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9+/_-]{12,}['\"]" -- ':!*.md' ':!backend/tests' ':!frontend/e2e'` → one hit, `INSECURE_DEV_JWT_SECRET` in `config.py`, which production refuses to start with (`TestProductionGuards::test_the_shipped_dev_signing_key_is_refused`). |
| AC-0.3 [A] | ✅ | `test_migrations::test_upgrade_then_downgrade_then_upgrade_succeeds` plus the five one-step tests (`…_reverses_and_reapplies`). |
| AC-0.4 [A] | 🟨 | `.github/workflows/ci.yml` runs ruff, mypy, pytest, eslint, `tsc --noEmit`, `next build`, the Compose smoke and now Playwright; it was green on 2026-09-14. The current tree has run only through `.\tasks.ps1 check` locally. |
| AC-0.5 [A] | ✅ | `test_health::test_readiness_reports_ok_when_database_reachable`, `::test_readiness_reports_503_when_database_unreachable`. |
| AC-0.6 [A] | ✅ | `test_api_contract::test_every_route_is_versioned_or_explicitly_allowlisted`, `::test_no_infrastructure_url_escapes_the_version_prefix`. |

### AC-1 — PostgreSQL database foundation

| ID | Status | Evidence |
|---|---|---|
| AC-1.1 | ✅ | `test_schema_invariants::test_every_primary_key_is_a_uuid`, `::test_uuid_primary_keys_are_generated_server_side`. |
| AC-1.2 | ✅ | `test_schema_invariants::test_no_column_stores_a_naive_timestamp`. |
| AC-1.3 | ✅ | `test_schema_invariants::test_timestamps_are_stored_in_utc_regardless_of_session_timezone`. |
| AC-1.4 | ✅ | `test_schema_invariants::test_no_foreign_key_targets_a_business_key`. |
| AC-1.5 | ✅ | `test_constraints::test_catalog_item_number_is_unique_per_organization`, `::test_vendor_code_is_unique_within_an_organization`, `::test_the_same_file_content_cannot_be_stored_twice`, `::test_vendor_sku_is_unique_within_a_vendor`; "one approved mapping per vendor product" holds structurally — the mapping lives on the `vendor_products` row (ADR 0010) — with `::test_an_approved_mapping_must_point_at_a_product`, `::test_an_approved_mapping_is_accepted_when_complete`. |
| AC-1.6 | ✅ | `test_schema_invariants::test_constraint_names_follow_the_convention`; `test_migrations::test_the_models_and_the_migration_do_not_drift`. |

### AC-2 — Nineyard API integration

| ID | Status | Evidence |
|---|---|---|
| AC-2.1 [A+M] | 🟨 | [A] `test_nineyard_client::TestNoCredentialLeaksIntoLogs::test_authentication_logs_omit_every_credential`, `TestConfiguration::test_the_password_stays_a_secret_on_the_config`, `TestAuthentication::test_the_token_is_held_as_a_secret`. [M] the probe has never run against the live API — no credentials (B1). |
| AC-2.2 | ✅ | `test_nineyard_client::TestRetryPolicy` (5: transient retried and succeeds, transport retried, budget exhausted, definite failures never retried, the retryable set is exactly the transient statuses); `TestStatusHandling::test_rate_limiting_reports_retry_after`. |
| AC-2.3 | ⬜ | No sync exists; the paging mechanism is unknown (B1). |
| AC-2.4 | ⬜ | No sync exists. |
| AC-2.5 [A+M] | ⬜ | No test asserts it. By inspection `app/models/` carries `catalog_item_number`, `nineyard_sync_runs`, `nineyard_item_payloads` — this system's names — but upstream field names are not known yet (B1), so the assertion cannot be written meaningfully. |

### AC-3 — Catalog synchronization

| ID | Status | Evidence |
|---|---|---|
| AC-3.1 – AC-3.7 | ⬜ | Not built. Blocked on B1 (Prompt 23's prerequisites: credentials in `.env`, written answers in `nineyard-field-mapping.md`). The tables exist and are tested at the schema level only. |

### AC-4 — Vendor database

| ID | Status | Evidence |
|---|---|---|
| AC-4.1 | ✅ | `test_vendor_api::TestVendorLifecycle` (create, get, patch), `TestDeactivate::test_deactivation_keeps_the_row_and_audits`. |
| AC-4.2 | ✅ | `test_vendor_api::TestVendorLifecycle::test_a_duplicate_code_is_a_409_with_a_typed_body`. |
| AC-4.3 | ✅ | `TestDeactivate::test_deactivation_keeps_the_row_and_audits`, `::test_a_vendor_with_an_active_import_profile_cannot_be_deactivated`; no `DELETE` route exists (`test_api_contract`). |
| AC-4.4 | ✅ | `test_vendor_api::test_every_mutation_produces_exactly_one_audit_row_with_before_and_after`. |

### AC-5 — Vendor inventory imports

| ID | Status | Evidence |
|---|---|---|
| AC-5.1 | ✅ | `test_import_processing::TestBriefLayouts::test_layout_a_as_csv`; `test_readers::TestStreaming::test_rows_carry_the_files_own_line_numbers`, `::test_a_quoted_record_spanning_lines_is_numbered_by_its_first_line`. |
| AC-5.2 | ✅ | `test_import_processing::TestBriefLayouts::test_layout_b_as_xlsx`, `::test_layout_a_as_xlsx_and_b_as_csv`; `test_readers::TestStreaming::test_xlsx_rows_are_numbered_by_sheet_row`; `test_import_profile_api::TestValidate::test_a_wrong_sheet_name_is_422`. |
| AC-5.3 | ✅ | `test_import_upload_api::TestRetention::test_csv_round_trip_is_byte_identical`, `::test_xlsx_round_trip_is_byte_identical` (SHA-256 of upload == recorded == on disk == download; BOM and CRLF intact). |
| AC-5.4 | ✅ | `test_import_processing::TestFileLevelFailures::test_missing_required_column_fails_before_any_row`, `::test_an_unreadable_workbook_fails_the_job`, `::test_a_tampered_retained_file_fails_the_job` — in each the file is on disk and the job FAILED with nothing parsed; `receive_upload` stores before it records (`test_storage`, 14). |
| AC-5.5 | ✅ | `test_import_profile_api::TestValidate::test_csv_sample_against_the_stored_profile` (leading zero restored), `test_profile_rules::TestReaders::test_xlsx_numeric_upc_keeps_its_digits`, `test_import_processing::TestBriefLayouts::test_layout_b_as_xlsx`, `test_normalize::TestInvalidValues::test_scientific_notation_from_a_spreadsheet_is_unusable`. |
| AC-5.6 | 🟨 | Detection: `test_import_upload_api::TestDuplicates::test_identical_bytes_return_the_existing_job`, `::test_a_live_or_completed_job_is_a_duplicate` — the existing job is returned with `duplicate: true` and no second batch is ever created. **The "explicit confirmation" half does not exist**: a byte-identical file can be imported again only after its job FAILED or was CANCELLED (ADR 0004). Either the criterion is amended to say so or a confirmed re-import flag is added. |
| AC-5.7 | ⬜ | No worker; stages run in the upload request. Resume-after-restart is unproven. |
| AC-5.8 | ✅ | `test_import_upload_api::TestRetention::test_csv_round_trip_is_byte_identical` (UTF-8 with BOM), `::test_cp1252_encoding_is_recorded`; `test_import_processing::TestEncodingsAndDelimiters::test_latin_1_csv`, `::test_declared_encoding_is_used_strictly`; `test_readers::TestEncodings::test_detection_order`. |

### AC-6 — Import profiles

| ID | Status | Evidence |
|---|---|---|
| AC-6.1 [A+M] | 🟨 | [A] Playwright step *create an import profile from the rule schemas* creates and validates a profile through the UI with no code change; `test_import_profile_api::TestCreate` (5). [M] against a real vendor's file: pending B4. |
| AC-6.2 | ✅ | `test_import_upload_api::TestProfilesAndListing::test_a_profile_is_pinned_by_id_and_version`. |
| AC-6.3 | ✅ | `test_import_profile_api::TestVersioning::test_editing_creates_the_next_version_and_retires_the_current_one`, `::test_the_list_shows_the_active_version_only_unless_asked`. |
| AC-6.4 | ✅ | `test_import_processing::TestFileLevelFailures::test_header_signature_mismatch_fails_with_the_diff`; `test_import_profile_api::TestValidate::test_a_file_with_different_columns_fails_the_signature`. |
| AC-6.5 | ✅ | `TestCreate::test_creates_version_one_with_defaults_and_an_audit_row`, `TestVersioning::test_editing_creates_the_next_version…` (two audit rows), `::test_deactivate_keeps_the_row_and_audits`. |

### AC-7 — Deterministic matching

| ID | Status | Evidence |
|---|---|---|
| AC-7.1 | ✅ | `test_match_engine::TestRule1Upc` … `TestRule4AmazonSkuMapping`, `::test_a_upc_the_catalog_does_not_know_falls_through`, `TestRule5Suggestion::test_rule_5_does_not_run_when_an_identifier_matched`. |
| AC-7.2 | ✅ | `test_import_matching::TestMatchImportJob::test_every_outcome_is_recorded_on_rows_lines_and_the_queue`; `test_constraints::test_a_matched_row_must_record_the_deciding_rule`, `::test_match_priority_stays_within_the_chain`. Deviation, recorded as A23: the trail is on `import_job_rows.normalized_data.match` and the queue item, not a `match_attempt` table. |
| AC-7.3 | ✅ | `test_import_matching::TestDeterminism::test_the_same_job_matched_twice_against_the_same_state_is_identical`; `test_match_engine::TestDeterminism` (2). |
| AC-7.4 | ✅ | `test_match_engine::TestRule1Upc::test_a_upc_matching_two_products_is_ambiguous_even_though_the_sku_would_match`, `TestRule4AmazonSkuMapping::test_an_ambiguous_amazon_sku_is_ambiguous_at_priority_4`. |
| AC-7.5 | ✅ | `test_match_engine::TestRule5Suggestion::test_a_description_identical_to_a_product_name_is_a_suggestion_never_a_match`. |
| AC-7.6 | ✅ | `test_normalize` (34, incl. `TestUpcE`, `TestCheckDigit`, `test_values_differing_only_in_leading_zeros_compare_equal`); `test_match_engine::TestRule1Upc::test_a_upc_with_a_bad_check_digit_is_not_an_identifier`; `test_profile_rules::TestMapper::test_a_bad_upc_still_imports_on_the_vendor_sku` (the warning). |
| AC-7.7 | ✅ | `test_match_engine::TestEmptyInput::test_empty_input_is_unmatched_with_four_evaluations_recorded`; `test_exception_api::test_every_unresolved_row_produces_exactly_one_item_with_a_reason`. |
| AC-7.8 | ✅ | `test_import_matching::TestMatchImportJob::test_every_outcome_is_recorded…` (a UPC match leaves the vendor line **PENDING**, never APPROVED), `::test_an_approved_mapping_is_never_touched_and_a_disagreement_is_a_conflict`; `test_exception_api::TestDecisions::test_approving_a_pending_suggestion_makes_it_permanent`. |

### AC-8 — Exception workflow

| ID | Status | Evidence |
|---|---|---|
| AC-8.1 | ✅ | `test_exception_api::test_every_unresolved_row_produces_exactly_one_item_with_a_reason`. |
| AC-8.2 [A+M] | 🟨 | [A] `test_exception_api::TestQueue::test_detail_shows_the_source_row_candidates_and_trail`; Playwright step *open the exception and approve the suggested product* (drawer with the raw row and candidates). [M] on real data: pending B4. |
| AC-8.3 | ✅ | `TestReimportAfterApproval::test_an_approved_exception_is_used_automatically_on_the_next_import` (approver and UTC time on the line), `TestDecisions::test_approving_a_pending_suggestion_makes_it_permanent`. |
| AC-8.4 | ✅ | Same test — the re-import matches by `VENDOR_SKU_MAPPING` at priority 3 with no new item; Playwright step *re-upload…*. |
| AC-8.5 | ✅ | `TestDecisions::test_reject_records_the_decision_and_makes_no_mapping`, `::test_a_rejected_line_is_not_requeued_for_the_same_evidence`. |
| AC-8.6 | ✅ | `test_import_matching::…::test_an_approved_mapping_is_never_touched_and_a_disagreement_is_a_conflict`; `test_exception_api::TestSupersession::test_a_conflicting_approval_needs_the_explicit_flag_and_is_recorded_twice`, `::test_supersede_true_replaces_the_mapping_with_both_steps_audited`. |
| AC-8.7 | ✅ | `TestDecisions::test_reject…`, `::test_defer_hides_the_item_until_its_time`, `TestSupersession::test_supersede_true…` (before/after on every step); Playwright step *the audit log records the decisions*. |

### AC-9 — Validation and reporting

| ID | Status | Evidence |
|---|---|---|
| AC-9.1 | ✅ | `test_profile_rules::TestMapper::test_bad_values_become_coded_issues`; `test_import_processing::TestEveryRowProblem::test_counters_statuses_and_codes_are_exact` (code, severity, field, row number for all 12 rows). |
| AC-9.2 | ✅ | `TestEveryRowProblem::test_counters_statuses_and_codes_are_exact` (row errors, batch continues); `TestFileLevelFailures` (missing column, signature mismatch, unreadable workbook fail before any row). |
| AC-9.3 | ⬜ | No immutable `import_report` row. The report is computed on read (`GET /imports/{id}/report`); matched-by-rule and event counts exist only inside `import_jobs.error_details`. |
| AC-9.4 | ✅ | `TestEveryRowProblem::test_counters_statuses_and_codes_are_exact` — counters reconciled from the rows. |
| AC-9.5 [A+M] | 🟨 | [A] `TestEveryRowProblem::test_rows_can_be_filtered_by_status_and_code` — `GET /imports/{id}/rows?status=ERROR` lists every failed row with its number in the retained file; the imports screen's row browser shows the same. **No file export** of the error listing exists — "downloadable" is met only in the API sense. |
| AC-9.6 [M] | 🟨 | Playwright step *upload the fixture and read the report* sees status and report on the imports screen. [M] on real data: pending B4. |

### AC-10 — OOS watchlist

| ID | Status | Evidence |
|---|---|---|
| AC-10.1 | ✅ | `test_availability_api::TestWatchlistApi::test_add_list_remove_with_audit` (reason, who, when; vendor-specific or all-vendors). |
| AC-10.2 | ✅ | Same test — the row is kept after removal with `deactivated_by_user_id` / `deactivated_at`; second remove 409. |
| AC-10.3 [M] | 🟨 | The watchlist screen filters by **vendor** and **active state**; the API also filters by product. **No product filter in the UI** — it needs product search (B9). |
| AC-10.4 | ✅ | `TestWatchlistApi::test_add_list_remove_with_audit` (`watchlist.added`, `watchlist.removed`). |

### AC-11 — Availability transition detection

| ID | Status | Evidence |
|---|---|---|
| AC-11.1 | ✅ | `test_availability_api::TestSnapshotsAndEvents::test_snapshot_carries_the_files_moment_and_the_cost`; `TestProductBecomesAvailable`. |
| AC-11.2 | ✅ | `TestProductBecomesAvailable::test_watch_then_zero_then_forty_raises_exactly_one_linked_event`. |
| AC-11.3 | ✅ | `TestSnapshotsAndEvents::test_reverse_transition_and_unmatched_lines`. |
| AC-11.4 | ✅ | `TestTransitions::test_transition` (12 parametrised cases); `TestProductBecomesAvailable` (a byte-different file with the same state: no event, no history row; the same bytes: a duplicate upload, nothing runs); `test_constraints::test_one_snapshot_raises_a_given_event_only_once`. |
| AC-11.5 | ✅ | `test_profile_rules::TestMapper::test_status_column_availability` (unlisted value → UNKNOWN with `AVAILABILITY_UNKNOWN`); `TestEveryRowProblem::test_counters_statuses_and_codes_are_exact` (row 11). |
| AC-11.6 [A+M] | 🟨 | [A] `TestProductBecomesAvailable` — the event is linked to the watch entry and the entry's status and history move; Playwright step *upload the now-available fixture…* sees the *watched* badge, *0 → 40*, and the dashboard's newly-available count. There is no `watchlist_hit` table: the link is `availability_events.watchlist_entry_id` + `oos_status_history`. [M] on real data: pending B4. |
| AC-11.7 | ✅ | `TestSnapshotsAndEvents::test_reverse_transition_and_unmatched_lines` (event with `product_id` null). |

### AC-12 — Audit logging

| ID | Status | Evidence |
|---|---|---|
| AC-12.1 | ✅ | Per suite rather than one sweep, each exercising the endpoint and asserting its rows: vendors and contacts — `test_vendor_api::test_every_mutation_produces_exactly_one_audit_row_with_before_and_after`; profiles — `test_import_profile_api::TestCreate`, `TestVersioning`; import lifecycle — `test_import_upload_api::TestRetention::test_audit_rows_for_file_and_job`, `test_import_processing` (`import_job.started` / `.parsed` / `.failed`), `test_import_matching` (`.matched`), `test_availability_api` (`.completed`); approve / reject / defer / supersede — `test_exception_api::TestDecisions`, `TestSupersession`; watchlist — `TestWatchlistApi::test_add_list_remove_with_audit`; Amazon runs — `test_amazon_orders_sync`, `test_amazon_inventory_sync`, `test_amazon_jobs` (stale-run recovery). |
| AC-12.2 | ✅ | `test_audit_service::TestRecordingEvents::test_a_user_action_is_recorded`, `::test_a_system_action_needs_no_user`, `TestCorrelation::test_the_request_id_is_carried_onto_the_row`; `test_schema_invariants::test_timestamps_are_stored_in_utc…`. |
| AC-12.3 | ✅ | `test_audit_append_only` (7: update and delete refused, including from raw SQL); the only audit route is `GET /api/v1/audit/events`. |
| AC-12.4 | ✅ | `test_audit_service::TestTransactionalIntegrity::test_a_rollback_leaves_no_audit_row_behind`, `::test_both_commit_together`; `test_transactions` (8). |
| AC-12.5 [M] | 🟨 | The audit screen filters by entity, actor, action and date range (`test_admin_reads::TestAuditFeed::test_filters_and_shape` for the API; Playwright step *the audit log records the decisions*). [M] on real data: pending B4. |
| AC-12.6 | ✅ | `test_audit_service::TestSecretsNeverReachTheTrail` (2); `test_auth_flow::TestDevTokenFlow::test_the_token_itself_is_never_written_to_the_audit_trail`. |

### AC-13 — Administration interface

| ID | Status | Evidence |
|---|---|---|
| AC-13.1 [M] | 🟨 | Screens exist and are driven by Playwright for vendors, profiles, upload and job status, reports, the queue, watchlist, availability and audit. **Product lookup does not exist** (B1/B9). |
| AC-13.2 | ✅ | `npm run typecheck` and `npm run lint` clean (§4); `grep -rn -E ":\s*any\b\|as any\b\|<any>" frontend/src` → 0. |
| AC-13.3 | ⬜ | `frontend/src/lib/types.ts` is hand-written from `app/schemas`; nothing generates it from OpenAPI and CI does not check staleness. |
| AC-13.4 | 🟨 | `frontend/e2e/milestone-1.spec.ts` covers the full path and more (watch, availability, audit); passed twice locally (12.1 s, 9.1 s). **Not yet run in CI** — wired into `compose-smoke`, awaiting the push. |
| AC-13.5 | ✅ | `test_exception_api::TestAccessControl::test_viewers_and_operators_read_but_do_not_decide`, `::test_admin_can_decide`; `test_availability_api::TestWatchlistApi` (VIEWER 403). The UI additionally hides the actions by `hasRole`; that is not tested. |
| AC-13.6 [A+M] | 🟨 | [M] Every timestamp on every screen goes through `fmtDate` or `fmtWhen` in `frontend/src/components/ui.tsx`, which since 2026-09-23 render the reader's local time **with the zone named** (`timeZoneName: "short"`, e.g. *Sep 23, 2026, 5:26:32 PM EDT*); stored values are UTC (AC-1.3). Verified by rendering the dashboard against fixture data. [A] **No automated assertion** — the walk-through does not check any timestamp, and the frontend has no unit-test runner. |

### What the tally means

The engine, the import pipeline, the exception workflow, the watchlist and
the audit trail — the parts of Milestone 1 that carry correctness risk — are
proven by tests that ran today. What is open falls into four groups, each
with one owner: **Nineyard** (AC-2.3–2.5, AC-3, the products screen, product
search — the client, B1); **the real-data walkthrough** (every [M] — the
client, B4); **the CI run** (gate 1, AC-0.4, AC-13.4 — a push); and **three
engineering items** with no external dependency (AC-5.7 worker, AC-9.3
report row, AC-13.3 generated types), plus two wording deviations to settle
with the client (AC-5.6 confirmation, AC-9.5 export).
