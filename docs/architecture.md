# Architecture — Milestone 1

Status: **Proposed.** No application code exists yet.
Last updated: 2026-09-07

---

## 1. System shape

Four runtime components, all started by Docker Compose for local development.

```
                 ┌──────────────────────────┐
                 │  Next.js admin (web)     │
                 │  React + TypeScript      │
                 └────────────┬─────────────┘
                              │ HTTPS, /api/v1
                 ┌────────────▼─────────────┐
                 │  FastAPI (api)           │
                 │  Pydantic v2 schemas     │
                 │  SQLAlchemy 2 ORM        │
                 └──┬───────────────────┬───┘
                    │                   │
     ┌──────────────▼────────┐ ┌────────▼─────────────────┐
     │  PostgreSQL 16        │ │  Object storage          │
     │  UUID PKs, TIMESTAMPTZ│ │  raw import files (WORM) │
     └──────────────▲────────┘ └────────▲─────────────────┘
                    │                   │
                 ┌──┴───────────────────┴───┐
                 │  Worker (same image)     │
                 │  • import processing     │
                 │  • Nineyard catalog sync │
                 └────────────┬─────────────┘
                              │ HTTPS
                 ┌────────────▼─────────────┐
                 │  Nineyard API (external) │
                 └──────────────────────────┘
```

**Why a separate worker.** Import parsing and catalog sync are long-running and
must survive an API restart mid-batch. The worker claims work from the database
with `SELECT ... FOR UPDATE SKIP LOCKED`, so no broker (Redis/RabbitMQ/Celery) is
required in Milestone 1. Adding one later is a contained change.

---

## 2. Proposed repository structure

```
/
├── CLAUDE.md
├── README.md
├── Makefile                      # task commands (make, for CI/Linux)
├── tasks.ps1                     # task commands (PowerShell, for Windows)
├── .env.example
├── .gitignore
├── infra/
│   └── docker-compose.yml        # postgres + api + web
├── storage/                      # retained import files (ADR 0004)
│   ├── raw/                      # byte-for-byte as uploaded
│   ├── processed/                # successfully ingested
│   └── rejected/                 # failed validation
├── docs/
│   ├── milestone-1-scope.md
│   ├── architecture.md
│   ├── acceptance-criteria.md
│   ├── phase1-status.md
│   └── decisions/
│       ├── README.md
│       ├── 0000-template.md
│       └── 000N-*.md
├── backend/
│   ├── pyproject.toml            # deps, ruff, mypy, pytest config
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py               # FastAPI app factory
│   │   ├── worker.py             # worker entrypoint            [phase 5]
│   │   ├── api/                  # HTTP layer only
│   │   │   ├── health.py         # GET /health — liveness, unversioned
│   │   │   ├── deps.py           # shared dependencies          [phase 1]
│   │   │   └── v1/
│   │   │       ├── router.py     # aggregates all /api/v1 routes
│   │   │       └── routes/
│   │   │           ├── health.py    products.py   vendors.py
│   │   │           ├── profiles.py  imports.py    exceptions.py
│   │   │           ├── mappings.py  watchlist.py  availability.py
│   │   │           └── audit.py
│   │   ├── core/                 # cross-cutting concerns
│   │   │   ├── config.py         # pydantic-settings, env only
│   │   │   ├── logging.py        # structlog JSON + correlation id
│   │   │   ├── middleware.py     # request id binding, access log
│   │   │   ├── clock.py          # single UTC now() source
│   │   │   ├── security.py       # auth, current-actor resolution [phase 1]
│   │   │   └── errors.py         # typed API error envelope       [phase 1]
│   │   ├── db/
│   │   │   ├── base.py           # DeclarativeBase, naming convention
│   │   │   ├── session.py        # engine, sessionmaker, unit of work
│   │   │   └── types.py          # UUID/TIMESTAMPTZ/JSONB helpers [phase 1]
│   │   ├── models/               # SQLAlchemy 2 mapped classes (§5)
│   │   ├── schemas/              # Pydantic request/response models
│   │   ├── repositories/         # data access; owns SQL, returns plain data
│   │   ├── services/             # business behaviour
│   │   │   ├── matching/         # normalize, rules, engine, suggestions
│   │   │   ├── availability/     # snapshot diff, transition events
│   │   │   ├── watchlist/
│   │   │   └── audit/            # audit writer, diff capture
│   │   ├── integrations/         # outbound external systems
│   │   │   └── nineyard/         # client + anti-corruption layer (ADR 0007)
│   │   ├── imports/              # vendor file ingestion
│   │   │   ├── readers/          # csv_reader.py, xlsx_reader.py
│   │   │   ├── profile.py        # profile → field extraction
│   │   │   ├── validation.py
│   │   │   └── report.py
│   │   ├── storage/              # StorageBackend protocol; local + S3
│   │   └── jobs/                 # claim/lease loop, schedules
│   └── tests/
│       ├── conftest.py
│       ├── fixtures/files/       # sample CSV/XLSX incl. malformed cases
│       ├── unit/                 # normalization, rules, profile engine
│       └── integration/          # DB-backed: sync, import, exceptions
└── frontend/
    ├── package.json
    ├── Dockerfile
    ├── tsconfig.json
    ├── next.config.ts
    ├── eslint.config.mjs
    ├── src/
    │   ├── app/                  # App Router
    │   │   ├── layout.tsx
    │   │   ├── vendors/     profiles/    imports/
    │   │   ├── exceptions/  products/    watchlist/
    │   │   └── availability/  audit/
    │   ├── components/
    │   ├── lib/
    │   │   ├── api-client.ts
    │   │   └── generated/api-types.ts   # from FastAPI OpenAPI
    │   └── styles/
    └── tests/
        ├── unit/                 # component tests
        └── e2e/                  # Playwright
```

Entries marked `[phase N]` do not exist yet; everything else is present as of
the phase 0 scaffold. The backend's layering is enforced by direction of
dependency, not convention alone:

| Layer | May depend on | Never contains |
|---|---|---|
| `api/` | schemas, services, core | SQL, business rules |
| `services/` | repositories, core | HTTP types, SQL |
| `repositories/` | models, db, core | HTTP types, business rules |
| `integrations/` | core | domain models (translated at the boundary, ADR 0007) |
| `imports/` | services, repositories, core | HTTP types |

`integrations/` and `imports/` sit at the top level rather than under
`services/` because both are boundaries with their own translation
responsibilities — one to an external API, one to arbitrary vendor file
formats — and keeping them visible at the top level makes those boundaries hard
to erode by accident.

---

## 3. Technical plan

### 3.1 Backend execution model

**Synchronous SQLAlchemy 2 + psycopg 3**, with FastAPI route handlers declared
as plain `def` so Starlette runs them in a threadpool. Rationale: this milestone
is dominated by bulk row processing and file parsing, where async provides no
benefit and introduces real risk (blocking calls in the event loop, async/sync
session bridging, harder-to-test transaction boundaries). Recorded as
[ADR 0008](decisions/0008-synchronous-sqlalchemy.md).

Transactions: one unit of work per request; imports commit **per chunk** of rows
(default 500) so a large file makes forward progress and a failure leaves a
resumable batch rather than losing hours of work.

### 3.2 File parsing — exactness over convenience

**Do not use pandas for import parsing.** Pandas infers dtypes and will silently
convert `"0012345678905"` to a float, destroying leading zeros and precision on
UPCs and vendor SKUs. Milestone 1 reads:

- **CSV** — stdlib `csv` module, every field read as `str`, no coercion. Encoding
  detected with `charset-normalizer`, overridable per profile. BOM stripped.
- **XLSX** — `openpyxl` in `read_only=True` mode, cell values taken as-is and
  converted to `str` deterministically (integers rendered without a trailing
  `.0`, dates rendered ISO-8601). Formula cells read as cached values; a profile
  flag controls whether a formula cell is an error.

Typed coercion happens **after** extraction, per profile field rule, with the raw
string retained alongside the parsed value.

### 3.3 Normalization (deterministic, pure functions)

- **UPC/GTIN** — strip non-digits, expand UPC-E to UPC-A where applicable,
  validate the check digit, then left-pad to GTIN-14 as the canonical comparison
  form. Store `raw_value` and `normalized_value` separately. A failed check digit
  is a validation warning and disqualifies the value from priority-1 matching; it
  does not silently pass.
- **Catalog Item Number / SKU** — trim, collapse internal whitespace, casefold,
  strip a configured set of separators. Rules are per-profile and versioned so a
  historical import can be replayed identically.
- **Text** — normalized for *display and suggestion only*, never as a match key.

### 3.4 Matching engine

*Implemented 2026-09-15 in `backend/app/matching/engine.py`; this section
describes what exists.*

`engine.evaluate(candidate: MatchInput, lookups: MatchLookups) -> MatchOutcome`
is a pure function — no database, clock or randomness — that evaluates rules
1→4 in the order CLAUDE.md §5.1 gives and short-circuits on the first rule
returning exactly one product. `MatchInput` carries the normalised UPC and
its checksum flag, the catalog item number, the vendor id and normalised
vendor SKU, the Amazon seller SKU and the description. `MatchLookups` is
what the rules read: `products_by_gtin`, `products_by_catalog_item_number`,
`products_by_vendor_sku(vendor_id, sku)` (APPROVED mappings only),
`products_by_amazon_sku` (APPROVED listings only) and
`suggest_by_description`. For an import, `MatchIndexRepository` builds an
in-memory `MatchIndex` once per job; the Amazon listing sync answers the
same protocol from SQL.

```
MatchOutcome = {
  result:        MATCHED | AMBIGUOUS | UNMATCHED | SUGGESTION_ONLY   # the MatchResult enum
  product_id:    UUID | None                                         # MATCHED only
  method:        UPC | CATALOG_ITEM_NUMBER | VENDOR_SKU_MAPPING | AMAZON_SKU_MAPPING | None
  priority:      1..4 for MATCHED/AMBIGUOUS, 5 for SUGGESTION_ONLY, None for UNMATCHED
  candidates:    [product_id]                                        # the rule's hits
  suggestions:   [ {product_id, score, reason} ]                     # priority 5 only
  evaluations:   [ {rule, priority, input, outcome, candidates, note} ]  # every rule, in order
}
```

`outcome` per rule is `matched`, `ambiguous`, `no_match`, `skipped` (with
the reason: no input, an earlier rule matched, an earlier rule was
ambiguous) or `suggested`.

Invariants enforced by tests (`tests/unit/test_match_engine.py`,
`tests/integration/test_import_matching.py`):

- A rule returning **more than one** product yields `AMBIGUOUS` at that
  priority and an exception. It never falls through to a lower-priority rule
  and never picks one; the remaining rules are recorded as skipped.
- Rule 5 runs only when rules 1–4 all return nothing. `SUGGESTION_ONLY`
  **never** writes a mapping; it creates an exception with scored
  candidates (pg_trgm similarity on `products.name`, threshold 0.3, top 5).
- Description similarity appears only inside `suggestions`. There is no
  code path in which it sets `product_id`.
- A UPC whose check digit failed is not an identifier: rule 1 skips it.
- Given identical lookups, `evaluate()` is a pure function of the input;
  candidates are sorted so lookup order cannot change an outcome.

The import step (`app/services/matching.py`) applies the outcome: MATCHED
rows record `match_result` / `matched_by` / `match_priority` / `product_id`;
the vendor line is created or refreshed but an APPROVED mapping is never
modified — a match by UPC or catalog number leaves the line PENDING with a
`SUGGESTION_ONLY` queue item, because a vendor-SKU mapping becomes permanent
only through approval (§5.2), and a disagreement between the chain's product
and an approved mapping raises `CONFLICTING_IDENTIFIER` rather than
overwriting either. Every non-matched row opens one queue item per vendor
line with reason, candidates and the full trail; the trail is also stored on
the row (`normalized_data.match`), which is where the plan's `match_attempt`
table lives in practice (phase1-status A23).

### 3.5 Nineyard integration

`httpx` client wrapped in an anti-corruption layer. Nineyard response shapes are
parsed into `NineyardCatalogItemDTO` (Pydantic) and then translated into domain
models; no Nineyard field name reaches `app/models/`. Credentials come from
environment variables only. `tenacity` handles retry with exponential backoff and
jitter on 429 and 5xx, honoring `Retry-After`. Every raw item payload is
persisted so a sync can be replayed and diffed without re-calling the API.

### 3.6 Raw file retention

`StorageBackend` protocol with a local-filesystem implementation for development
and an S3-compatible implementation for deployment (MinIO in Compose). On upload:
compute SHA-256, write once to a content-addressed path, and record size,
declared MIME, detected encoding, and original filename. The stored object is
never rewritten. Re-uploading an identical file is detected by checksum and
surfaced to the user rather than silently duplicated.

### 3.7 Audit logging

A single `audit_log` table written through one service. Writes participate in the
same transaction as the change they describe, so an audit entry cannot exist for
a rolled-back change and vice versa. `before`/`after` are JSONB snapshots limited
to the changed fields. `request_id` correlates HTTP request → audit rows → logs.

### 3.8 API and contract sharing

FastAPI emits OpenAPI at `/api/v1/openapi.json`; the frontend generates
`src/lib/generated/api-types.ts` with `openapi-typescript`. The generated file is
committed, and CI fails if it is stale, so contract drift is caught at build time.

---

## 4. Dependency list

### 4.1 Backend (Python 3.12+)

**Runtime**

| Package | Purpose |
|---|---|
| `fastapi` | HTTP framework, OpenAPI |
| `uvicorn[standard]` | ASGI server |
| `pydantic` (v2) | schemas, DTO validation |
| `pydantic-settings` | typed config from environment variables only |
| `sqlalchemy>=2.0` | ORM, typed `Mapped[...]` models |
| `alembic` | migrations |
| `psycopg[binary,pool]` | PostgreSQL driver (psycopg 3) |
| `python-multipart` | multipart file upload |
| `openpyxl` | XLSX reading (`read_only=True`) |
| `charset-normalizer` | CSV encoding detection |
| `httpx` | Nineyard HTTP client |
| `tenacity` | retry/backoff for Nineyard |
| `structlog` | structured logging with correlation ids |
| `pyjwt` | admin session tokens |
| `passlib[argon2]` | password hashing for admin users |
| `apscheduler` | cron trigger for catalog sync |
| `boto3` | S3-compatible raw-file storage |

**Development / test**

| Package | Purpose |
|---|---|
| `pytest`, `pytest-cov` | test runner and coverage |
| `testcontainers[postgres]` *or* a Compose test DB | real PostgreSQL for integration tests |
| `httpx` (test client) | API tests |
| `freezegun` | deterministic time in tests |
| `ruff` | lint + format |
| `mypy` | static typing |
| `types-*` stubs | typing support |

*Optional performance:* `python-calamine` as a faster XLSX reader if `openpyxl`
proves too slow on large vendor files. Not adopted up front.

### 4.2 Frontend

**Runtime**

| Package | Purpose |
|---|---|
| `next`, `react`, `react-dom` | framework |
| `typescript`, `@types/react`, `@types/node` | typing |
| `@tanstack/react-query` | server state, caching, retries |
| `zod` | runtime validation at the API boundary |
| `react-hook-form` | profile and vendor forms |
| `tailwindcss` | minimal admin styling |

**Development / test**

| Package | Purpose |
|---|---|
| `eslint`, `eslint-config-next` | standard Next.js lint |
| `vitest`, `@testing-library/react`, `@testing-library/user-event` | component tests |
| `@playwright/test` | e2e for the critical flows |
| `openapi-typescript` | generate API types from OpenAPI |
| `prettier` | formatting |

### 4.3 Infrastructure

`postgres:16`, `minio/minio` (raw file storage in development), the backend image
(run twice: `api` and `worker`), and the frontend image — all defined in
`docker-compose.yml` and configured entirely through `.env`.

---

## 5. Database entity list

> **Superseded as of 2026-09-08.** The schema is implemented, and
> [docs/database-schema.md](database-schema.md) is now the authoritative
> description — it documents the 21 tables as built, with the ER diagram,
> relationship and delete-behaviour reference, and index coverage.
>
> The section below is the original planning sketch, kept for the reasoning it
> records. It differs from what shipped in three ways: table names are plural
> (`products`, not `product`); tenancy was added, so every table carries
> `organization_id` ([ADR 0009](decisions/0009-organization-scoped-multi-tenancy.md));
> and approved mappings live on `vendor_products` and `marketplace_listings`
> rather than in a separate mapping table
> ([ADR 0010](decisions/0010-identifier-model-and-mapping-placement.md)).

All tables carry a UUID primary key (`id`) and `created_at` / `updated_at` as
`TIMESTAMPTZ` in UTC. Business keys are separate, uniquely constrained columns.
Foreign keys always target UUIDs.

### 5.1 Catalog and product identity

| Entity | Purpose | Key fields |
|---|---|---|
| `product` | Canonical catalog item — the identity anchor | `id` (UUID, immutable), `nineyard_catalog_item_number` (**unique**), `title`, `brand`, `manufacturer`, `pack_size`, `uom`, `status`, `nineyard_last_seen_at`, `is_active` |
| `product_identifier` | Many identifiers per product (UPC / EAN / GTIN / MPN / ASIN) | `product_id`, `identifier_type`, `raw_value`, `normalized_value`, `is_valid_check_digit`, `source`. Partial unique on (`identifier_type`, `normalized_value`) where valid — enables priority-1 matching |
| `amazon_sku` | One Catalog Item → many Amazon SKUs | `product_id`, `seller_sku`, `asin`, `marketplace_id`, `approved_by`, `approved_at`, `is_active`. Unique (`marketplace_id`, `seller_sku`) |
| `nineyard_sync_run` | One catalog sync execution | `started_at`, `finished_at`, `status`, `items_seen/created/updated/unchanged/removed`, `error_summary`, `triggered_by` |
| `nineyard_item_payload` | Retained raw API payload, per item per run | `sync_run_id`, `catalog_item_number`, `payload` (JSONB), `payload_sha256`, `fetched_at` |

### 5.2 Vendors and import profiles

| Entity | Purpose | Key fields |
|---|---|---|
| `vendor` | Supplier record | `code` (**unique**), `name`, `status`, `currency`, `timezone`, `default_lead_time_days`, `is_active`, `notes` |
| `vendor_contact` | Contact people per vendor | `vendor_id`, `name`, `email`, `phone`, `role` |
| `vendor_import_profile` | Versioned parsing config (data, not code) | `vendor_id`, `name`, `version` (int), `is_active`, `file_format` (csv/xlsx), `encoding`, `delimiter`, `quote_char`, `header_row_index`, `sheet_name_or_index`, `skip_rows`, `column_map` (JSONB), `normalization_rules` (JSONB), `quantity_semantics`, `price_semantics`, `pack_size_handling`, `availability_rule` (JSONB), `header_signature`, `created_by`. Unique (`vendor_id`, `name`, `version`) |
| `vendor_product` | A vendor's catalogue line | `vendor_id`, `vendor_sku`, `vendor_description`, `raw_upc`, `normalized_upc`, `pack_size`, `uom`, `first_seen_at`, `last_seen_at`. Unique (`vendor_id`, `vendor_sku`) |

### 5.3 Approved mappings (permanent)

| Entity | Purpose | Key fields |
|---|---|---|
| `vendor_product_mapping` | Approved vendor SKU → product (match priority 3) | `vendor_product_id`, `product_id`, `status` (approved / superseded / revoked), `matched_by`, `approved_by`, `approved_at`, `superseded_by_id`, `notes`. Partial unique: one `approved` row per `vendor_product_id` |

Approved **Amazon SKU** mappings (match priority 4) are the `approved_*` columns
on `amazon_sku`. Both mapping surfaces are append-oriented: revocation writes a
new state and preserves history rather than deleting.

### 5.4 Imports

| Entity | Purpose | Key fields |
|---|---|---|
| `import_batch` | One import run | `vendor_id`, `profile_id`, `profile_version`, `import_file_id`, `status` (received / parsing / validating / matching / completed / failed / cancelled), `uploaded_by`, `started_at`, `finished_at`, `row_count`, `claimed_by`, `claimed_until` (worker lease), `error_summary` |
| `import_file` | Raw file, retained unchanged | `original_filename`, `storage_uri`, `sha256` (**unique**), `size_bytes`, `declared_mime`, `detected_encoding`, `uploaded_at`, `uploaded_by` |
| `import_row` | One source row, addressable by line number | `batch_id`, `source_row_number`, `raw_payload` (JSONB, original strings), `extracted` (JSONB, post-profile), `status` (ok / warning / error / skipped), `outcome` (matched / exception / unmatched), `product_id` (nullable), `vendor_product_id` (nullable) |
| `import_issue` | Validation finding | `batch_id`, `import_row_id` (nullable for file-level issues), `severity` (error / warning / info), `code`, `field`, `message`, `context` (JSONB) |
| `import_report` | Immutable summary produced at completion | `batch_id` (**unique**), `generated_at`, `totals` (JSONB), `matched_by_rule_counts` (JSONB), `issue_counts_by_code` (JSONB), `exception_count`, `availability_event_count` |

### 5.5 Matching and exceptions

| Entity | Purpose | Key fields |
|---|---|---|
| `match_attempt` | Full deterministic evaluation trail per row | `import_row_id`, `evaluations` (JSONB: each priority, rule, hit count, decision), `result_status`, `matched_by`, `rule_priority`, `product_id`, `evaluated_at` |
| `match_exception` | The exception queue | `import_row_id`, `batch_id`, `vendor_id`, `vendor_product_id`, `reason` (no_match / ambiguous / suggestion_only / conflicting_identifier), `status` (open / approved / rejected / deferred), `assigned_to`, `resolved_by`, `resolved_at`, `resolution_note`, `resulting_mapping_id` |
| `match_candidate` | A priority-5 suggestion offered to a reviewer | `match_exception_id`, `product_id`, `score`, `reason_codes` (array), `rank` |

### 5.6 Inventory, availability, watchlist

| Entity | Purpose | Key fields |
|---|---|---|
| `vendor_inventory_snapshot` | Vendor's stated state at a point in time | `vendor_id`, `vendor_product_id`, `batch_id`, `product_id` (nullable until matched), `quantity_available`, `unit_cost`, `currency`, `availability_status` (available / out_of_stock / discontinued / unknown), `effective_at`. Unique (`batch_id`, `vendor_product_id`) |
| `availability_event` | Detected transition between snapshots | `vendor_id`, `vendor_product_id`, `product_id` (nullable), `previous_status`, `new_status`, `previous_quantity`, `new_quantity`, `transition` (became_available / became_unavailable), `detected_at`, `batch_id`, `previous_snapshot_id` |
| `watchlist_entry` | OOS watchlist | `product_id` (nullable), `vendor_product_id` (nullable), `vendor_id` (nullable), `added_by`, `reason`, `is_active`, `deactivated_at`, `deactivated_by`. A check constraint requires at least one target column |
| `watchlist_hit` | An availability event matched a watch entry | `watchlist_entry_id`, `availability_event_id`, `created_at`, `acknowledged_by`, `acknowledged_at`. Unique (`watchlist_entry_id`, `availability_event_id`) |

### 5.7 Administration and audit

| Entity | Purpose | Key fields |
|---|---|---|
| `app_user` | Admin user, and the actor referenced by audit rows | `email` (**unique**), `display_name`, `password_hash`, `role` (admin / reviewer / viewer), `is_active`, `last_login_at` |
| `audit_log` | Append-only trail of important changes | `occurred_at`, `actor_user_id` (nullable for system), `actor_type` (user / system / worker), `action`, `entity_type`, `entity_id` (UUID), `before` (JSONB), `after` (JSONB), `request_id`, `ip_address`, `summary`. Indexed on (`entity_type`, `entity_id`, `occurred_at`) |
| `job_run` | Observability for scheduled and background work | `job_type`, `status`, `started_at`, `finished_at`, `context` (JSONB), `error_summary` |

**Count: 22 tables.** Enum-valued columns use native PostgreSQL enum types
created and altered through Alembic.

---

## 6. Implementation order

Each phase is independently reviewable and leaves the system in a working state.

| # | Phase | Delivers | Depends on |
|---|---|---|---|
| 0 | **Scaffolding** | Compose stack, `.env.example`, Dockerfiles, CI (ruff / mypy / pytest / eslint / tsc), health endpoint, empty Next.js app | — |
| 1 | **DB foundation + audit** | `db/base`, naming conventions, UUID / TIMESTAMPTZ helpers, `app_user`, `audit_log`, audit service, Alembic baseline | 0 |
| 2 | **Vendor database** | `vendor`, `vendor_contact`, CRUD under `/api/v1/vendors`, audited | 1 |
| 3 | **Nineyard integration + sync** | httpx client, ACL / DTOs, `product`, `product_identifier`, `amazon_sku`, `nineyard_sync_run`, `nineyard_item_payload`, sync job, `/api/v1/products` read API | 1 |
| 4 | **Import profiles** | `vendor_import_profile` with versioning, profile validation, `/api/v1/profiles` | 2 |
| 5 | **File ingestion + raw retention** | `StorageBackend`, `import_file`, `import_batch`, upload endpoint, checksum and dedupe, worker claim loop | 1, 2 |
| 6 | **Parsing + validation + reporting** | CSV / XLSX readers, profile-driven extraction, `import_row`, `import_issue`, `import_report`, report endpoints and download | 4, 5 |
| 7 | **Matching engine** | `normalize.py`, priority chain, `match_attempt`, `vendor_product`, `vendor_product_mapping`; unit tests including ambiguity and description-only cases | 3, 6 |
| 8 | **Exception workflow** | `match_exception`, `match_candidate`, review and approve / reject / defer endpoints, permanent mapping creation, full audit | 7 |
| 9 | **Inventory, availability, watchlist** | `vendor_inventory_snapshot`, snapshot diff, `availability_event`, `watchlist_entry`, `watchlist_hit` | 6, 7 |
| 10 | **Admin interface** | Next.js screens for all of the above; Playwright e2e over vendor → profile → import → exception → approval → re-import | 2–9 |
| 11 | **Hardening** | Indexing and query review, large-file performance pass, backup and restore rehearsal, runbook, seed / demo data | 10 |

Phases 2 and 3 can run in parallel once phase 1 lands. Phase 4 can start
alongside phase 5.

---

## 7. Identified risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Nineyard API is unspecified** — auth model, pagination, rate limits, catalog schema, delta support, and sandbox availability are all unknown to this plan. | High. Phase 3 cannot be estimated or built accurately. | Blocking question B1. The anti-corruption layer confines the blast radius; build against a recorded-fixture double until real documentation and a sandbox credential exist. |
| R2 | **Spreadsheet type coercion corrupts identifiers** — UPCs losing leading zeros or becoming floats (`1.2345678905e+10`). | High. Silently wrong matches. | No pandas. All fields read as strings. Explicit typed coercion after extraction, with the raw string retained. Fixture tests for leading zeros, scientific notation, and Excel-stored-as-number UPCs. |
| R3 | **Vendor file drift** — a vendor changes columns, adds a header row, or ships a new sheet without notice. | Medium-high. Batches fail or, worse, map columns wrongly. | Versioned profiles; a `header_signature` check on every import that fails fast on mismatch instead of guessing; the report shows expected vs. observed headers. |
| R4 | **Match ambiguity at scale** — many vendor lines with no UPC and no prior mapping flood the exception queue. | Medium-high. Human review becomes the bottleneck and the feature is judged unusable. | Measure the exception rate on the first real file. Bulk-approve tooling for identical repeated cases, queue grouping by reason, and prioritized review ordering. Escalate as a product question early rather than loosening match rules. |
| R5 | **Pack-size and unit-of-measure mismatch** — a vendor sells a case of 12 where the catalog item is an each. | High. Quantities, and later replenishment math, are wrong. | `pack_size` / `uom` on both `product` and `vendor_product`; `pack_size_handling` in the profile; a validation issue is raised when they disagree. A mismatch does not block Milestone 1 but is reported. Blocking question B3. |
| R6 | **Availability semantics vary by vendor** — `0`, blank, "call", "discontinued" all mean different things. | Medium. Spurious or missed availability events. | `availability_rule` JSONB in the profile maps vendor values to the canonical status enum. Unmapped values become `unknown` plus a validation warning — never a silent `available`. |
| R7 | **Large files** — a multi-hundred-thousand-row XLSX exhausts memory or wall-clock time. | Medium. | Streaming / read-only parsing, chunked commits, worker leases so a batch resumes. A performance budget is set in phase 11 once real file sizes are known (B4). |
| R8 | **Duplicate or replayed imports** — the same file uploaded twice creates double snapshots and phantom availability events. | Medium. | SHA-256 uniqueness on `import_file`, explicit re-import confirmation, `effective_at` ordering on snapshots, and idempotent diffing against the previous snapshot for that vendor. |
| R9 | **Approved mappings drift from a changing catalog** — Nineyard retires a Catalog Item that mappings point at. | Medium. | `product` is soft-deactivated, never hard-deleted; sync flags mappings pointing at retired items into a review queue rather than breaking foreign keys. |
| R10 | **Audit completeness is assumed rather than proven.** | Medium. The compliance value evaporates if coverage is patchy. | Audit writes share the transaction with the change; integration tests assert that an audit row exists for every mutating endpoint. |
| R11 | **Raw-file storage is a hard requirement with no stated destination** (local disk vs. S3 vs. Azure) and no retention policy. | Medium. | The `StorageBackend` abstraction keeps the choice late-binding; MinIO locally. Blocking question B5. |
| R12 | **Authentication and user provisioning are unspecified**, yet audit attribution and approvals require an actor. | Medium. Blocks phases 8 and 10. | Minimal local email/password with roles as the default assumption (A6), replaceable behind `core/security.py`. Blocking question B2. |
| R13 | **UPC check-digit failures in real vendor data** are common; treating them as hard errors would reject valid business data. | Medium. | An invalid check digit is a warning: the value is excluded from priority-1 matching, and the row is still processed and routed to exceptions if nothing else matches. |
| R14 | **Repository name says "Power BI"** while Power BI is explicitly out of scope, suggesting a naming or expectation mismatch. | Low technically, notable for stakeholders. | Blocking question B6. |
| R15 | **Timezone handling at the presentation layer** — vendors state effective dates in local time. | Low-medium. | All storage is UTC; a `timezone` attribute on `vendor` governs interpretation of vendor-supplied dates during extraction. |
