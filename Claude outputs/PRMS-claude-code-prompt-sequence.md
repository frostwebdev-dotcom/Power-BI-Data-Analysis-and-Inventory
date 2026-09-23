# PRMS — Claude Code Prompt Sequence

Paste these into Claude Code (VS Code) one at a time, in order, inside the `Power-BI-Data-Analysis-and-Inventory` repo. Each prompt is one commit-sized unit of work with its own acceptance criteria. Claude Code reads the repo's `CLAUDE.md` automatically, so its rules (UUID keys, UTC timestamps, Alembic-only schema changes, audit on every mutation, matching hierarchy, no scope creep without an ADR) apply to every prompt below without restating them.

## How to run these

- Start each prompt in a **fresh context** (`/clear`) unless the prompt says otherwise, so earlier work does not crowd out the current task.
- For prompts marked **[plan first]**, press `Shift+Tab` to switch Claude Code to plan mode, paste the prompt, read the plan, then approve. These are the prompts where a wrong design costs days.
- Every prompt ends with the same closing instruction. Do not skip it: the gates are the only proof the work is real.
- On Windows the gate command is `.\tasks.ps1 check`; on macOS/Linux it is `make check`. The prompts say `.\tasks.ps1 check` — change it if you are not on Windows.
- If Claude Code reports a failing gate, do not move to the next prompt. Paste the failure back with: "Fix this failure without weakening the test or the check, then re-run the gates."

Closing instruction used by every prompt (already included in each; shown here so you recognise it):

> When done: run `.\tasks.ps1 check` and paste the full results. Update `docs/phase1-status.md` in the same commit to reflect exactly what now exists. Commit with the message given. Do not begin any further work.

---

# Stage 0 — Orientation (one prompt)

## Prompt 0 — Confirm the environment before touching anything

```
Read CLAUDE.md, docs/phase1-status.md, docs/architecture.md section 6 (implementation order), and docs/acceptance-criteria.md in full. Then, without changing any code:

1. Confirm the backend virtualenv at backend\.venv exists and uses Python 3.12; if not, create it and install with `pip install -e ".\backend[dev]"`.
2. Confirm PostgreSQL is reachable using DATABASE_URL from .env and that the `prms` and `prms_test` databases exist.
3. Run `.\tasks.ps1 check` and paste the complete output.
4. Run `git status` and `git log --oneline -n 10` and paste them.
5. Count test functions with `git grep -c "def test_" -- backend/tests` and report unit vs integration totals.

Report all five results and list anything that failed. Do not fix anything yet and do not commit.
```

---

# Stage 1 — Day-1 fixes (five prompts, about half a day)

## Prompt 1 — Fix README encoding

```
README.md is stored as UTF-16 and renders as garbage on GitHub. Fix it:

1. Convert README.md to UTF-8 without BOM, preserving content exactly. Verify with `git diff --stat` that only encoding changed (the diff will show every line, so also confirm the decoded text is identical to the UTF-16 original).
2. Scan the whole repository for any other non-UTF-8 text file or a UTF-8 BOM (check *.md, *.py, *.ts, *.tsx, *.toml, *.yml, *.json, *.ps1) and fix any you find.
3. Add a .gitattributes at the repo root with `* text=auto eol=lf` and `*.ps1 text eol=crlf`, so this cannot recur.
4. Fix the stale sentence in README.md under "Database migrations" that says "No migrations exist yet" — one migration (506fd0ecc33a) exists.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit to reflect exactly what now exists. Commit with the message "fix: store README and text files as UTF-8; add .gitattributes". Do not begin any further work.
```

## Prompt 2 — Bring the status document to truth

```
docs/phase1-status.md is one commit stale. Make it match reality:

1. Section 1 and section 4: the test count. Count with `git grep -c "def test_" -- backend/tests/unit backend/tests/integration`, run pytest, and record the real unit / integration / total numbers from the pytest summary line, not from memory.
2. Section 2 phase tracker: phase 3 (Nineyard) must say the read-only diagnostic client, probe, and CLI exist (app/integrations/nineyard/, app/cli/nineyard_probe.py) with their test files, and that no sync service exists.
3. Add a new section "10. Re-planning (2026-09-14)" stating: the client has requested an Amazon SP-API proof of concept as the first technical milestone; an ADR will record the scope change; work order is now (a) Amazon POC, (b) Milestone 1 phases 2 through 11 in the documented order. Keep it to one paragraph.
4. Update "Last updated" at the top.

Do not change any code. When done: run `.\tasks.ps1 check` and paste the full results. Commit with the message "docs: correct test counts and record re-planning". Do not begin any further work.
```

## Prompt 3 — Record the Amazon scope change as an ADR **[plan first]**

```
The client has made a working Amazon SP-API proof of concept the condition for continuing the project. CLAUDE.md section 3 currently forbids all Amazon SP-API work in Milestone 1. Resolve this the way CLAUDE.md section 7 requires — with an ADR — rather than by silently expanding scope.

1. Create docs/decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md using docs/decisions/0000-template.md. Context: client request quoted verbatim below. Decision: a bounded, READ-ONLY SP-API ingestion is added to Milestone 1 scope, consisting of exactly: LWA authentication; retrieval of order lines sufficient for 7/14/30-day sales velocity; retrieval of FBA inventory including inbound quantities and FBM quantity; retrieval of listings to map Amazon seller SKUs to Nineyard Catalog Item # / UPC; storage in PostgreSQL; scheduled ingestion; error handling and logging; one read-only velocity endpoint and a CLI. Explicitly still OUT: replenishment quantities, profitability, ConnectBooks, any write to Amazon, buyer PII / Restricted Data Tokens, Walmart, Power BI. Consequences: new tables, new dependency (python-amazon-sp-api, MIT), a scheduler dependency (APScheduler 3.x), and new configuration.
2. Amend CLAUDE.md: in section 2 add item 14 "Amazon SP-API read-only ingestion (ADR 0011)"; in section 3 replace the "Amazon SP-API" bullet with "Amazon SP-API beyond the read-only ingestion defined in ADR 0011 (no writes, no PII, no replenishment math)". Do not change anything else in CLAUDE.md.
3. Update docs/milestone-1-scope.md: add the ingestion to the in-scope list and narrow the out-of-scope entry the same way. Also add "inbound vendor email monitoring" to the out-of-scope table, since it is currently unstated.
4. Update docs/decisions/README.md index.

Client request, verbatim: "Before moving forward, I would like the first technical milestone to include a working proof of concept with our actual Amazon account that demonstrates: SP-API/LWA authentication; retrieval of the sales data required for 7/14/30-day velocity; retrieval of relevant inventory and inbound inventory; mapping Amazon SKUs to our Nineyard Catalog Item #/UPC structure; storage of the data in PostgreSQL; scheduled data ingestion; basic error handling and logging."

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "docs: ADR 0011 — Amazon SP-API read-only ingestion enters Milestone 1". Do not begin any further work.
```

## Prompt 4 — Continuous integration that proves the stack **[plan first]**

```
The Docker stack has never been started (developer machine cannot run WSL2) and "passes locally" is the only evidence for the quality gates. Add GitHub Actions so every push proves both.

Create .github/workflows/ci.yml with three jobs:

1. backend: ubuntu-latest, Python 3.12, a `postgres:16-alpine` service container. Read backend/tests/integration/conftest.py first — it derives the test database from DATABASE_URL by appending "_test" or uses TEST_DATABASE_URL if set; set the env so the integration tests run against the service container. Steps: pip install -e "./backend[dev]", ruff format --check, ruff check, mypy (strict, as configured), pytest with -q. Cache pip.
2. frontend: ubuntu-latest, Node 24 (match docs/phase1-status.md section 4), npm ci, npm run lint, npm run typecheck, npm run build. Cache npm.
3. compose-smoke: ubuntu-latest; `cp .env.example .env`; `docker compose --env-file .env -f infra/docker-compose.yml up -d --build`; wait up to 120 s for http://localhost:8000/api/v1/health to return HTTP 200; run `docker compose ... exec -T api alembic upgrade head` and then `alembic check`; on failure dump `docker compose logs`; always `docker compose down -v`.

Also add a status badge to README.md and a short "Continuous integration" section to docs/deployment.md describing the three jobs. Do not modify any Dockerfile or the compose file unless the smoke job cannot pass without it; if you must, explain exactly why in the commit message.

Push the branch and report the workflow result. If the compose-smoke job fails, paste the logs and fix the root cause — that job is Milestone 1 exit criterion #1 and it has never been verified.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit (section 6 issue S1 should now say the stack is verified in CI). Commit with the message "ci: backend, frontend and compose smoke jobs on GitHub Actions". Do not begin any further work.
```

## Prompt 5 — Tenancy safety net (small, prevents a future leak)

```
Every table carries organization_id (ADR 0009) but nothing enforces the filter, and docs/phase1-status.md assumption A19 admits a forgotten filter is a cross-tenant leak. Add the cheap safety net now, before any repository code exists to get it wrong:

1. In backend/app/repositories/, add a base class or helper that every future repository must use, which requires an organization_id argument and applies `.where(Model.organization_id == organization_id)` to every select/update/delete it builds. Keep it minimal — no ORM magic, just a function that a reviewer can read.
2. Add a unit test in backend/tests/unit/ that introspects every subclass of OrganizationScopedMixin (see backend/app/models/mixins.py) and asserts the helper can scope a query on it.
3. Add an integration test in backend/tests/integration/ that creates two organizations, inserts one vendor in each, and proves the helper returns only the right one.
4. Write docs/decisions/0012-tenant-scoping-enforced-in-repository-layer.md recording that row-level security is deferred until the first multi-tenant deployment and this helper is the interim rule.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: repository-layer tenant scoping helper with tests (ADR 0012)". Do not begin any further work.
```

---

# Stage 2 — Amazon SP-API proof of concept (nine prompts, five to eight working days)

Prerequisite: the client's LWA client id, client secret, refresh token, Seller ID, and marketplace id, delivered through a secure channel into your local `.env`. Prompts 6 and 7 can be done before the credentials arrive; Prompt 8 onward needs them for the live checks.

## Prompt 6 — Amazon configuration and secret handling

```
Add the configuration for a read-only Amazon SP-API integration, following exactly how the Nineyard settings are done in backend/app/core/config.py (typed Settings fields, SecretStr for secrets, production start-up validation) and how secrets are masked in backend/app/core/redaction.py.

1. Add Settings fields: amazon_lwa_client_id: str | None, amazon_lwa_client_secret: SecretStr | None, amazon_lwa_refresh_token: SecretStr | None, amazon_seller_id: str | None, amazon_marketplace_id: str = "ATVPDKIKX0DER", amazon_region: str = "NA", amazon_timeout_seconds: float = 60.0, amazon_max_attempts: int = 5, amazon_enabled: bool = False. Environment variable names are the upper-cased field names.
2. Add a computed property `amazon_configured` that is true only when client id, secret, refresh token and seller id are all present; when amazon_enabled is true and amazon_configured is false, start-up must fail with a clear message, in the same style as the existing production checks.
3. Extend redaction so values of keys matching refresh_token, client_secret, lwa_*, and any header named x-amz-access-token are masked, and add the LWA token endpoint's `access_token` JSON field to the pattern list.
4. Add the new variables to .env.example with placeholder values and a comment that they are obtained from Seller Central > Apps & Services > Develop Apps (self-authorised private app).
5. Tests: extend backend/tests/unit/test_config.py, test_settings_secrets.py and test_redaction.py with cases for every rule above, including that repr(settings) and a structlog line never contain the secret values.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: Amazon SP-API settings, validation and secret redaction". Do not begin any further work.
```

## Prompt 7 — Amazon client as an anti-corruption layer **[plan first]**

```
Build backend/app/integrations/amazon/ as a read-only anti-corruption layer over the python-amazon-sp-api library, mirroring the structure and guarantees of backend/app/integrations/nineyard/ (client.py, errors.py, typed DTOs, retry policy, structural read-only guarantee, ADR 0007).

Before writing code, read the installed library: add `python-amazon-sp-api>=1.9` to backend/pyproject.toml dependencies, install it, and inspect sp_api.api.Reports, sp_api.api.Inventories and sp_api.base (Marketplaces, ReportType, exceptions, credentials handling) so the wrapper matches the real signatures rather than guessed ones. Note the library reads credentials from a dict with keys refresh_token, lwa_app_id, lwa_client_secret; build that dict from Settings at the single boundary and never pass it anywhere else.

Expose exactly these operations on AmazonClient, each returning a frozen dataclass DTO, and nothing else (no generic "call" method, no write operations):
- request_report(report_type, data_start, data_end, report_options=None) -> ReportRequest(report_id)
- get_report_status(report_id) -> ReportStatus(processing_status, report_document_id)
- download_report_document(report_document_id) -> bytes (already gunzipped, decoded as the library returns it)
- fetch_report(report_type, data_start, data_end, report_options=None, poll_interval_s=15, timeout_s=1800) -> bytes  (convenience: request, poll until DONE/CANCELLED/FATAL, download; raise AmazonReportFailed on the failure states)
- iter_inventory_summaries(details=True) -> Iterator[InventorySummary] handling nextToken pagination; InventorySummary carries seller_sku, asin, fnsku, condition, last_updated, total, fulfillable, inbound_working, inbound_shipped, inbound_receiving, reserved_total, unfulfillable_total, researching_total.
Errors: define AmazonConfigurationError, AmazonAuthError, AmazonRateLimited, AmazonTransientError, AmazonReportFailed in errors.py, all subclasses of one AmazonError. Retry only on the library's rate-limit and 5xx exceptions with capped exponential backoff and jitter, max attempts from settings; never retry auth errors.

Tests (backend/tests/unit/test_amazon_client.py): mock the library classes; cover token/credential boundary (no secret in any log line), each DTO mapping from a realistic payload fixture, pagination across three pages, retry-then-succeed, retry exhaustion, auth error not retried, report polling reaching DONE, CANCELLED and timeout, and a structural test that AmazonClient has no public method whose name starts with create_, put_, post_, update_, delete_, submit_ other than request_report.

Write docs/amazon-integration.md describing the boundary, the DTOs, the error taxonomy and the retry policy, in the style of docs/nineyard-integration.md.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: read-only Amazon SP-API client behind an anti-corruption layer". Do not begin any further work.
```

## Prompt 8 — Amazon schema: sync runs, order lines, inventory snapshots **[plan first]**

```
Add the tables for Amazon ingestion as one Alembic migration, following every convention in CLAUDE.md section 4 and the patterns in backend/app/models/ (UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, TIMESTAMPTZ, enum types created and dropped in the migration exactly as 506fd0ecc33a does). Reuse the existing SyncStatus and TriggerType enums from backend/app/models/enums.py.

Models in backend/app/models/amazon.py:

1. AmazonSyncRun (table amazon_sync_runs): job_type enum AmazonSyncJobType {ORDERS_REPORT, FBA_INVENTORY, LISTINGS_REPORT}; status SyncStatus; trigger_type TriggerType; window_start, window_end (nullable TIMESTAMPTZ); marketplace_id; report_id (nullable); rows_seen, rows_created, rows_updated, rows_unchanged, rows_failed integers default 0; error_message, error_details JSONB; started_at, completed_at; triggered_by_user_id FK users RESTRICT; request_id. Partial unique index: one RUNNING row per (organization_id, job_type). CHECK: completed_at is null while RUNNING.
2. AmazonOrderLine (table amazon_order_lines): amazon_order_id, seller_sku, asin (nullable), quantity_ordered int >= 0, purchase_date TIMESTAMPTZ, last_updated_at TIMESTAMPTZ, order_status (string, values as Amazon sends them), item_status (nullable), fulfillment_channel (nullable), sales_channel (nullable), marketplace_id, currency (nullable), item_price numeric(12,2) nullable, first_seen_sync_run_id and last_seen_sync_run_id FK amazon_sync_runs RESTRICT, raw JSONB (only the columns we keep — no ship-* address columns, ever). Unique (organization_id, amazon_order_id, seller_sku). Indexes on (organization_id, seller_sku, purchase_date) and (organization_id, purchase_date). Document in the model docstring that the all-orders flat-file report has no order-item-id, so lines are aggregated per (order, SKU) before upsert.
3. AmazonInventorySnapshot (table amazon_inventory_snapshots): seller_sku, asin, fnsku (nullable), condition (nullable), fulfillable, inbound_working, inbound_shipped, inbound_receiving, reserved_total, unfulfillable_total, researching_total, fbm_quantity (nullable), all integers >= 0 with CHECKs; amazon_last_updated_at nullable; captured_at TIMESTAMPTZ not null; sync_run_id FK RESTRICT; raw JSONB. Unique (sync_run_id, seller_sku). Index (organization_id, seller_sku, captured_at DESC).

Migration: autogenerate, then hand-check it — enum creation and downgrade drop, CHECK names following the existing naming convention, and `alembic check` clean afterwards.

Tests: extend backend/tests/integration/test_constraints.py, test_relationships.py, test_schema_invariants.py and test_migrations.py so the new tables are covered the same way as the existing ones (every unique, every CHECK, every FK delete behaviour, upgrade → downgrade → upgrade round trip with zero residual enum types).

Update docs/database-schema.md (table list, ER diagram, relationship reference).

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: Amazon sync run, order line and inventory snapshot tables". Do not begin any further work.
```

## Prompt 9 — Orders ingestion (sales data for velocity)

```
Implement the orders ingestion job in backend/app/services/amazon_orders.py using AmazonClient (Prompt 7) and the tables from Prompt 8.

Behaviour:
1. run_orders_sync(session, organization_id, window_start, window_end, trigger_type, triggered_by_user_id=None, client=None) -> AmazonSyncRun. It creates the RUNNING run inside a transaction (use app.db.transaction.transaction), releases it, then requests GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL for the window via client.fetch_report, parses the TSV, upserts order lines, and finally marks the run COMPLETED (or COMPLETED_WITH_ERRORS when rows_failed > 0) or FAILED with counts — SyncStatus has no SUCCEEDED value. A crash must leave a FAILED run with the exception class and message in error_details, never a dangling RUNNING row — use try/except/finally.
2. Parsing: read the TSV with the csv module (delimiter tab, utf-8 with fallback to latin-1), require the columns amazon-order-id, purchase-date, last-updated-date, order-status, fulfillment-channel, sales-channel, sku, asin, item-status, quantity, currency, item-price; fail the run with a clear message if any is missing. Aggregate rows by (amazon-order-id, sku) summing quantity, keeping the latest last-updated-date and its statuses. Parse timestamps to aware UTC. Never persist ship-* columns.
3. Upsert on (organization_id, amazon_order_id, seller_sku) using PostgreSQL INSERT ... ON CONFLICT DO UPDATE; update only when last_updated_at is newer; count created/updated/unchanged; set last_seen_sync_run_id every time.
4. Windows: reject windows longer than 30 days (report limit) and windows ending in the future; a "trailing" helper computes [now-35d, now] for the scheduler.
5. Audit: record one audit event per run via app.services.audit.record with action amazon.orders_sync.completed / .failed, actor type SYSTEM, in the same transaction as the run's final update.

Tests: backend/tests/unit/test_amazon_orders_parse.py with a fixture TSV (at least: a normal FBA line, an FBM line, a Cancelled line, a Pending line, two lines for the same order and SKU, a line with a missing asin, a latin-1 product name, a line with quantity 0). backend/tests/integration/test_amazon_orders_sync.py with a fake client: first run creates N lines, second identical run creates 0 and updates 0, a run with a newer last-updated-date updates 1, a client that raises halfway leaves exactly one FAILED run and no RUNNING row, and the partial unique index blocks a second concurrent RUNNING run.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/amazon-integration.md in the same commit. Commit with the message "feat: Amazon orders report ingestion with idempotent upsert". Do not begin any further work.
```

## Prompt 10 — Inventory ingestion (FBA on-hand, inbound, and FBM quantity)

```
Implement backend/app/services/amazon_inventory.py with the same run/transaction/audit shape as amazon_orders.py (Prompt 9).

1. run_inventory_sync(...): iterate client.iter_inventory_summaries(details=True), write one AmazonInventorySnapshot per seller SKU with captured_at = run start time, sync_run_id = the run. Snapshots are append-only; never update earlier ones.
2. FBM quantity: FBA Inventory does not cover merchant-fulfilled stock. In the same run, request GET_MERCHANT_LISTINGS_ALL_DATA via fetch_report, parse the TSV (columns seller-sku, quantity, fulfillment-channel, asin1, product-id, product-id-type, item-name, status), and for rows where fulfillment-channel is DEFAULT set fbm_quantity on that SKU's snapshot; create a snapshot with only fbm_quantity for FBM-only SKUs that FBA Inventory did not return. Keep the parsed listings rows in memory and pass them to the listings mapping service (Prompt 11) if it exists yet; otherwise leave a clearly named TODO that Prompt 11 removes.
3. Counts: rows_seen = summaries + listings rows; rows_created = snapshots written.
4. Rate limits: iter_inventory_summaries already backs off; add a configurable sleep between pages (settings amazon_inventory_page_delay_s, default 0.6) so a 450-SKU account stays under the documented ~2 requests/second.

Tests: unit tests for the listings TSV parser (FBA row, FBM row, FBM row with blank quantity, unknown fulfillment-channel value, product-id-type 3 = UPC vs 1 = ASIN) and for merging FBM quantity into summaries; an integration test with a fake client proving one snapshot per SKU per run, append-only across two runs, and FAILED-run handling identical to orders.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/amazon-integration.md in the same commit. Commit with the message "feat: Amazon FBA inventory and FBM quantity ingestion". Do not begin any further work.
```

## Prompt 11 — Map Amazon SKUs to the Nineyard catalog **[plan first]**

```
Implement backend/app/services/amazon_listings.py: populate marketplace_listings (backend/app/models/catalog.py, MarketplaceListing) from the parsed GET_MERCHANT_LISTINGS_ALL_DATA rows and resolve each seller SKU to a product using ONLY the CLAUDE.md section 5.1 priority chain. This is the first code that performs matching, so read section 5.2 twice before starting.

For each listings row:
1. Upsert the MarketplaceListing on (organization_id, marketplace, marketplace_id, seller_sku) with asin, listing status and the raw row; never touch mapping_status, product_id, mapping_method, mapping_approved_* on a row that is already APPROVED (approved mappings are permanent — section 5.2).
2. If the listing is not yet APPROVED, resolve in order, stopping at the first rule that yields exactly one product:
   a. Priority 4 (AMAZON_SKU_MAPPING): a product_identifiers row of type AMAZON_SKU with this seller SKU (this is what a future Nineyard /api/Skus sync will populate) → set product_id, mapping_status APPROVED, mapping_method AMAZON_SKU_MAPPING, approver = system user, approved_at = now, and record the audit event.
   b. Priority 1 (UPC): product-id-type 3 or 4 → normalise the product-id with the same rules the vendor import will use (strip, digits only, UPC-A/EAN-13 check digit; put this in backend/app/matching/normalize.py so Track B reuses it) → exactly one product_identifiers row of type UPC → mapping_status PENDING with product_id set and mapping_method UPC, and open a product_mapping_exceptions row with reason SUGGESTION_ONLY carrying the candidate, because an Amazon-SKU link inferred from a UPC still needs a human to approve it per section 5.1 row 5.
   c. Otherwise mapping_status UNMAPPED and a product_mapping_exceptions row with reason NO_MATCH (or AMBIGUOUS_MATCH if a rule matched more than one product), with match_evaluations recording every rule tried and its result — the deterministic attribution section 5.2 demands.
3. Never use item-name for anything except display. Add a test that proves two listings with identical item-name and different UPCs resolve independently.

Tests: unit tests for normalize.py (valid UPC-A, valid EAN-13, bad check digit, 11-digit UPC missing leading zero, whitespace and hyphens) and for the resolver against an in-memory fake repository covering each branch a–c, the "already APPROVED is never modified" rule, and the identical-name case. Integration test: seed one product with a UPC identifier and one with an AMAZON_SKU identifier, run the mapping over a three-row listings fixture, and assert the resulting marketplace_listings and product_mapping_exceptions rows and audit events exactly.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/amazon-integration.md in the same commit. Commit with the message "feat: map Amazon seller SKUs to catalog products through the priority chain". Do not begin any further work.
```

## Prompt 12 — Velocity: deterministic SQL, one read-only endpoint

```
Implement sales velocity as deterministic SQL and expose it read-only. No estimation, no AI, no rounding tricks.

1. Migration: create a SQL view v_amazon_sku_velocity (not materialized yet) computed from amazon_order_lines and the latest amazon_inventory_snapshots per SKU. Windows are anchored to `as_of` = completed_at of the latest COMPLETED or COMPLETED_WITH_ERRORS ORDERS_REPORT run for the organization — never now() — so a failed sync cannot silently show zeros. Columns: organization_id, seller_sku, asin, product_id (via marketplace_listings where mapping_status = APPROVED, else null), units_7d, units_14d, units_30d, avg_daily_7d, avg_daily_14d, avg_daily_30d, trend_7_over_30, fulfillable, fbm_quantity, on_hand_total, inbound_total, days_of_supply (null when avg_daily_14d = 0), as_of. Exclude lines whose order_status is Cancelled and whose item_status is Cancelled; count Pending and Unshipped as demand. Boundaries: purchase_date > as_of - interval 'N days' and <= as_of, in UTC.
2. Service backend/app/services/amazon_velocity.py: list_sku_velocity(session, organization_id, filters) and list_product_velocity(...) which rolls SKUs up to product_id (sum units, sum inventory, recompute days of supply), plus get_sync_status(...) returning last successful and last failed run per job_type.
3. Routes under /api/v1/amazon/ in backend/app/api/v1/routes/amazon.py, registered in router.py: GET /velocity (query: level=sku|product, search on seller_sku/asin, limit/offset), GET /status. Guard both with require_roles(RoleCode.VIEWER) — any authenticated role may read. Pydantic response schemas in backend/app/schemas/amazon.py. Follow the error envelope and settings access patterns in the existing routes.
4. Tests, integration, with fixture data built in factories.py: a cancelled order is excluded; a product with two SKUs rolls up to one row with summed units; an order exactly at the 7-day boundary lands in the right bucket; a SKU with inventory and zero sales has null days_of_supply; the view uses the last COMPLETED run's completed_at even when a later run FAILED; the endpoints return 401 without a token and 200 with a VIEWER token; the API contract test in test_api_contract.py is extended for the new routes.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/amazon-integration.md in the same commit. Commit with the message "feat: SKU and product sales velocity view with read-only endpoints". Do not begin any further work.
```

## Prompt 13 — Scheduler, run lock, and the POC CLI

```
Add scheduled ingestion and a CLI, keeping the scheduler replaceable.

1. Define a JobRunner protocol in backend/app/jobs/runner.py with schedule(job_id, func, interval|cron), start(), shutdown(). Implement APSchedulerRunner using APScheduler 3.x (add `apscheduler>=3.10,<4` to pyproject.toml) with an in-process BackgroundScheduler, max_instances=1, coalesce=True, misfire_grace_time=3600. Do not add Celery or Redis.
2. Jobs in backend/app/jobs/amazon.py: orders (default every 24 h, trailing 35-day window), inventory (default every 60 min), listings mapping (default every 24 h, runs after inventory). Each job opens its own session via app.db.transaction.session_scope, uses trigger_type SCHEDULED, and catches everything so one failure never stops the scheduler. Intervals come from settings (amazon_orders_interval_minutes etc.). The scheduler starts in the FastAPI lifespan only when settings.amazon_enabled is true, and never in tests.
3. Stale-run recovery: before starting, a job marks any RUNNING run of its job_type older than settings.amazon_run_timeout_minutes (default 120) as FAILED with error_message "timed out", then proceeds. Test it.
4. CLI backend/app/cli/amazon_poc.py, structured like app/cli/nineyard_probe.py (argparse, configure_logging, exit codes), subcommands: auth (obtain a token, print its expiry, print nothing else), sync-orders [--days N], sync-inventory, sync-listings, run (all three in order), velocity [--level sku|product] [--top N] printing an aligned table: seller SKU, ASIN, Catalog Item #, UPC, units 7/14/30, avg daily 14, fulfillable, FBM, inbound, days of supply, mapping method. Non-zero exit on any FAILED run.
5. Tests: unit tests for the CLI argument parsing and table formatting (no network), the runner protocol with a fake runner, and stale-run recovery as an integration test.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md, docs/amazon-integration.md and README.md (a "Running the Amazon POC" section) in the same commit. Commit with the message "feat: scheduled Amazon ingestion and amazon_poc CLI". Do not begin any further work.
```

---

# Stage 2.5 — Corrections found in the 2026-09-16 code review (run these BEFORE Prompt 14)

The review of commits 05d5d1a…28d25d4 is in `PRMS-code-review-2026-09-16.md`. These seven prompts fix everything rated BLOCKER or MAJOR there. Same rules: one at a time, `/clear` between them, do not proceed on a red gate.

## Prompt 13a — Report request body must be JSON-native (BLOCKER)

```
backend/app/integrations/amazon/client.py request_report() puts raw datetime objects into the createReport body ("dataStartTime": data_start, lines ~254-259). python-amazon-sp-api serialises the body with json.dumps and no datetime handler, so the first live call raises TypeError ("Object of type datetime is not JSON serializable"), which is not a SellingApiException and escapes _call untranslated. The unit tests inject a fake Reports class, which is why they pass.

1. Serialise dataStartTime and dataEndTime as RFC 3339 UTC strings with a trailing Z (e.g. 2026-09-01T00:00:00Z) — one small helper in client.py, used by request_report only.
2. Add a unit test that captures the kwargs handed to the reports class and asserts json.dumps(body) succeeds and the two timestamps equal the expected Z-suffixed strings.
3. Audit every other call into the library (Inventories.get_inventory_summary_marketplace and any other) for the same class of problem: no datetime, Decimal, UUID or enum instance may reach a request body or query string. Add one assertion per call site.
4. Make _call translate an unexpected non-library exception into AmazonTransientError with the original chained, so a serialisation or network-layer error still ends as a FAILED run rather than an unhandled crash — and test it.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "fix(amazon): send RFC 3339 strings in report requests; translate unexpected client errors". Do not begin any further work.
```

## Prompt 13b — Velocity anchored to the last completed orders run; cancellations; roll-up (MAJOR M1–M3)

```
Three defects in backend/app/services/amazon_velocity.py:

1. `at = now()` (line ~90). The window must be anchored to completed_at of the latest amazon_sync_runs row with job_type ORDERS_REPORT and status COMPLETED or COMPLETED_WITH_ERRORS for the organisation, so a failed or stale sync cannot silently shorten the 7-day bucket. If no such run exists, return an empty result with as_of = None — never fall back to the wall clock. Expose as_of and as_of_run_id on every returned row and in the CLI table header.
2. Only order_status is checked for cancellation (line ~147). Also exclude lines whose item_status is Cancelled (case-insensitive); Pending and Unshipped continue to count as demand. Document the rule in the module docstring.
3. Product-level roll-up follows listing.product_id regardless of mapping_status (lines ~226-256). Roll up only listings whose mapping_status is APPROVED; PENDING and UNMAPPED SKUs stay as their own rows with product_id None.

Tests (integration, in test_amazon_jobs.py or a new test_amazon_velocity.py): as_of equals the last COMPLETED run's completed_at even when a later run FAILED; no run → empty result and as_of None; an order exactly 7 days, 14 days and 30 days before as_of lands in the correct bucket (inclusive lower bound as the code defines it — assert the precise rule); a line with item_status Cancelled in a Shipped order is excluded; a PENDING listing with product_id set is NOT merged into the product row; a SKU with inventory and zero sales has days_of_supply None asserted directly at service level.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/amazon-integration.md in the same commit. Commit with the message "fix(amazon): anchor velocity to the last completed orders run; exclude cancelled items; roll up approved listings only". Do not begin any further work.
```

## Prompt 13c — Migration 3de5c4e5def0 downgrade order (MAJOR M5)

```
In backend/alembic/versions/*3de5c4e5def0*.py, downgrade() calls _drop_checks() before _remove_rows_the_old_schema_cannot_hold(). _drop_checks() re-creates the OLD ck_product_identifiers_context_matches_identifier_type, and PostgreSQL validates existing rows when a CHECK is added, so any unlinked AMAZON_SKU identifier row — precisely what this migration exists to allow — makes the downgrade fail before the deletion runs. The round-trip test passes only because the scratch database is empty.

1. Reorder downgrade(): delete the rows first, then restore the old constraints. Log the deleted row count with op.get_bind().execute(...).rowcount so a destructive downgrade is visible.
2. Extend backend/tests/integration/test_migrations.py: after upgrade to head, insert one organisation, one product and one product_identifiers row of type AMAZON_SKU with marketplace_listing_id NULL, then downgrade one step and upgrade again; assert it succeeds and the row is gone after the downgrade.
3. Correct the migration docstring and docs/database-schema.md §5 so they describe the real order.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "fix(db): delete rows before restoring constraints in 3de5c4e5def0 downgrade". Do not begin any further work.
```

## Prompt 13d — Scheduler placement and the inventory/listings collision (MAJOR M6) **[plan first]**

```
Two problems with the scheduler:

A. ADR 0011 says APScheduler must run in one dedicated worker process, never inside each uvicorn replica, and that infra/docker-compose.yml gains a worker service. backend/app/main.py starts it in the API lifespan and compose has no worker. Resolve it by building what the ADR says: add backend/app/jobs/worker.py (an entrypoint that builds the runner, starts it, and blocks until SIGTERM/SIGINT with a clean shutdown), a `worker` service in infra/docker-compose.yml using the same image with command `python -m app.jobs.worker`, and remove the scheduler start from the API lifespan (keep app.state.job_runner = None). Update docs/deployment.md, docs/amazon-integration.md and railway.json guidance (a second Railway service for the worker).

B. backend/app/jobs/amazon.py register_amazon_jobs() claims the listings job runs after inventory on a shared tick because it is registered later. APScheduler's BackgroundScheduler runs due jobs concurrently in a thread pool; the 60-minute inventory and 1440-minute listings intervals coincide once a day, so both request GET_MERCHANT_LISTINGS_ALL_DATA and both call map_listings on the same tenant at once. Fix: remove the standalone listings job (the inventory job already fetches the listings report and maps it), or keep it and take a shared run lock (a RUNNING amazon_sync_runs row of job_type LISTINGS_REPORT blocks both). Choose the first unless you can show a reason to keep the separate job; update the docstring either way.

Tests: worker entrypoint starts and stops cleanly on a signal (unit, fake runner); the API lifespan never starts a scheduler (adjust the existing one-sided test and add the "enabled outside test" case for the worker path instead); no two jobs share a report type at the same tick (assert on the registered job set).

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat(jobs): dedicated worker process per ADR 0011; remove duplicate listings job". Do not begin any further work.
```

## Prompt 13e — Record the matching decision as ADR 0014 and align the code (MAJOR M4) **[plan first]**

Replace the bracketed choice with the option you picked from section 4 of the review before pasting.

```
CLAUDE.md §5.1 row 1 says an exact normalised UPC match is automatic, and ADR 0011 says listings are mapped "automatically at priority 1". backend/app/services/amazon_listings.py instead evaluates priority 4 first (auto-APPROVE on a Nineyard AMAZON_SKU identifier with the system user as approver) and demotes a UPC hit to a PENDING suggestion. That was a deliberate but unrecorded choice, and CLAUDE.md §7 requires an ADR.

Decision: [OPTION A — a single unambiguous normalised-UPC match APPROVES the listing automatically with mapping_method UPC and the system user as approver; ambiguity or no match goes to the queue] / [OPTION B — keep UPC hits as PENDING suggestions; amend CLAUDE.md §5.1 to say that for marketplace listings priority 1 yields a suggestion, and amend ADR 0011 line ~74 accordingly].

1. Write docs/decisions/0014-marketplace-listing-mapping-rules.md recording: the evaluation order for listings (priority 4 from the catalog source, then priority 1 UPC/EAN, priority 2 not applicable because listings carry no Catalog Item Number, priority 3 not applicable), that a Nineyard-sourced AMAZON_SKU identifier counts as a previously approved mapping with the system user as approver, the chosen treatment of a UPC hit, and that an ambiguous priority-4 result must not block a unique priority-1 hit (fix that in the code: evaluate both, and only an ambiguity at the winning rule queues the row).
2. Align the code with the decision; keep match_evaluations recording every rule tried; add or adjust tests for: unique UPC → the chosen outcome; ambiguous priority 4 with unique UPC → UPC outcome; already-APPROVED never modified (existing).
3. Amend CLAUDE.md §5.1 only if Option B; amend ADR 0011's "Mapping obeys the existing rules" paragraph to point at ADR 0014 either way; fix the stale sentence at CLAUDE.md line ~58 ("calling Amazon is not") to match item 14.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md, docs/amazon-integration.md and docs/decisions/README.md in the same commit. Commit with the message "docs: ADR 0014 marketplace listing mapping rules; align listings resolver". Do not begin any further work.
```

## Prompt 13f — CI must fail rather than skip; documentation sweep (MAJOR M8 + MINORs)

```
1. backend/tests/integration/conftest.py skips the whole integration package when PostgreSQL is unreachable, and .github/workflows/ci.yml runs plain `pytest -q`, so a misconfigured service container would turn 208 tests into skips and CI would stay green. Change the conftest to pytest.fail (not skip) when the environment variable CI is set; add `-rs` to the pytest step; add a step that greps the pytest summary and fails on "skipped". Add the `docker compose config` gate to the compose-smoke job so the "8 of 8" claim becomes literally true.
2. Fix every stale statement found in review, in one commit: CLAUDE.md line ~58 (if not already fixed in 13e); README.md lines ~8 and ~152-154 ("21 tables", "one migration"); docs/phase1-status.md §3 lines ~57 and ~127 (21 tables / one revision), §7 opening word "Unchanged.", "five public methods" → six, the literal TAB in `.\tasks.ps1` at line ~412 (also docs/deployment.md ~262), "Run on 2026-09-14" date, and "8 of 8 in GitHub Actions" — link the CI run for the current HEAD or state which gates CI covers; docs/database-schema.md ~438-441 (AMAZON_SKU "has a marketplace_listing_id"), the ER diagram optionality of PRODUCTS→MARKETPLACE_LISTINGS and MARKETPLACE_LISTINGS→PRODUCT_IDENTIFIERS, and "152 database-backed tests"; docs/architecture.md ~345 and ~424 table counts; ADR 0012 assertion/table counts.
3. "Backend deployed to Railway" in phase1-status.md: add the service URL and the deploy date, or remove the claim.
4. Small code hygiene from the review: use TenantScope in amazon_orders.py ~380-389 and system_user.py ~29-31; pass amazon_sync_runs.error_message/error_details through the same redaction as audit payloads; record an audit row when _ensure_exception refreshes an existing queue item; strip header keys consistently in the orders and inventory parsers; make the orders job continue with the remaining windows after one fails and report all failures in the run.

When done: run `.\tasks.ps1 check` and paste the full results. Commit with the message "chore: CI fails on skipped tests; documentation sweep; review hygiene fixes". Do not begin any further work.
```

## Prompt 13g — The two read-only endpoints from Prompt 12 (MAJOR M7)

```
ADR 0011 capability 9 and Prompt 12 asked for read-only HTTP endpoints; none exist (backend/app/api/v1/routes/ has only auth.py and health.py). Build them now, small and exactly in the style of the existing routes:

1. backend/app/api/v1/routes/amazon.py registered in router.py: GET /api/v1/amazon/velocity (query: level=sku|product, q= search on seller_sku/asin, limit/offset with sane maxima) and GET /api/v1/amazon/status (last completed and last failed run per job_type, plus as_of). Both guarded with require_roles(RoleCode.VIEWER); tenant-scoped by the principal's organisation; Pydantic response schemas in backend/app/schemas/amazon.py; the existing error envelope.
2. Extend backend/tests/unit/test_api_contract.py for the new routes and add integration tests: 401 without a token, 200 with a VIEWER token, rows match the service output, and a second organisation's data never appears.
3. Do not add a SQL view; the Python service is the single implementation.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md, docs/amazon-integration.md and the ADR 0011 follow-ups list in the same commit. Commit with the message "feat(api): read-only Amazon velocity and sync-status endpoints". Do not begin any further work.
```

## Prompt 14 — Live validation against the client's account and POC sign-off

```
The Amazon credentials are now in .env. Validate the whole POC against the real account and produce the evidence the client asked for. Do not change behaviour in this prompt unless a live call reveals a defect; if it does, fix the defect with a test that would have caught it, and say so.

1. Run `python -m app.cli.amazon_poc auth` and confirm a token is obtained. Grep the log output for the client secret, refresh token and access token values and confirm none appears.
2. Run `python -m app.cli.amazon_poc run`. Paste the three run rows from amazon_sync_runs (expected status COMPLETED) (job_type, status, rows_seen/created/updated/failed, duration).
3. Run `python -m app.cli.amazon_poc velocity --level product --top 25` and paste the table.
4. Compare the 30-day units for three SKUs against Seller Central's Business Reports for the same window and record the numbers side by side in docs/amazon-integration.md under "Validation"; explain any difference (timezone of the window, pending orders, cancellations).
5. Report how many seller SKUs were mapped APPROVED, PENDING and UNMAPPED, and what the exception queue contains. This tells the client how much Nineyard SKU data we still need.
6. Failure drill: start `sync-orders`, disconnect the network mid-run, and paste the FAILED run row and the log line; reconnect and show the next run recovers.
7. Leave the scheduler running for 48 hours (AMAZON_ENABLED=true) and afterwards paste the list of runs with their statuses.
8. Write docs/amazon-poc-signoff.md: the client's seven bullets, and under each the evidence from steps 1–7. Update docs/phase1-status.md: Amazon POC complete, with the date.

When done: run `.\tasks.ps1 check` and paste the full results. Commit with the message "docs: Amazon SP-API proof-of-concept validation and sign-off". Do not begin any further work.
```

---

# Stage 3 — Finish Milestone 1 (the repo's own order, tightened)

Each prompt names the acceptance criteria in `docs/acceptance-criteria.md` it must satisfy; Claude Code should read that section before starting. Every mutation goes through `app.db.transaction.transaction` with `app.services.audit.record_change` in the same transaction, and every mutating route is guarded with `require_roles` from `backend/app/api/deps.py`.

## Prompt 15 — Vendor CRUD plus the columns the brief needs (AC-4)

```
Implement architecture.md phase 2, the vendor database, and satisfy docs/acceptance-criteria.md AC-4. This is the first real API; it sets the pattern every later prompt copies, so keep it clean and small.

1. Migration: add to vendors — minimum_order_quantity int null CHECK >= 0, minimum_order_value numeric(12,2) null CHECK >= 0, purchasing_terms JSONB not null default '{}' (free-form terms: payment terms, freight, cut-off times), and a new table vendor_contacts (vendor_id FK RESTRICT, name, email not null, role null, is_primary bool, is_active bool; unique (vendor_id, lower(email))). The brief requires "email address(es)" and minimum order requirements per vendor.
2. Repository backend/app/repositories/vendors.py using the tenant-scoping helper from Prompt 5: list (paginated, filter by status and name search using the existing trigram index), get, create, update, deactivate (never delete), and contact add/update/deactivate.
3. Service backend/app/services/vendors.py: each mutation inside transaction(session) with audit.record_change in the same transaction; deactivating a vendor with active import profiles is refused with a clear error.
4. Routes backend/app/api/v1/routes/vendors.py registered in router.py: GET /vendors, GET /vendors/{id}, POST /vendors, PATCH /vendors/{id}, POST /vendors/{id}/deactivate, and the same shape under /vendors/{id}/contacts. Reads require VIEWER; writes require DATA_OPERATOR (require_roles in api/deps.py). Schemas in backend/app/schemas/vendors.py with validation (code pattern, email format, currency ISO-4217 length 3).
5. Tests: unit tests for schema validation; integration tests for every route including 401/403 by role, duplicate code conflict → 409, deactivate refusal, and an assertion that every mutation produced exactly one audit_events row with before/after state. Extend test_api_contract.py.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/database-schema.md in the same commit. Commit with the message "feat: vendor database API with contacts, MOQ and purchasing terms". Do not begin any further work.
```

## Prompt 16 — Vendor UI screen (first real screen, replaces a placeholder)

```
Replace frontend/src/app/vendors/page.tsx (currently a <Placeholder>) with a working vendors screen against the API from Prompt 15, and establish the frontend patterns every later screen copies.

1. Add @tanstack/react-query and zod to frontend/package.json. Extend frontend/src/lib/api-client.ts from its single fetchReadiness() into a small typed client: a base fetch that attaches the bearer token from an in-memory auth store, maps the backend error envelope to a typed ApiError, and typed functions for the vendor endpoints. Keep NEXT_PUBLIC_API_BASE_URL as the only public config (see lib/config.ts); never store tokens in localStorage.
2. A minimal sign-in for development: a page that calls POST /api/v1/auth/dev-token (see backend/app/api/v1/routes/auth.py) and stores the token in memory; wire the AppShell to show the current principal from GET /api/v1/auth/me and a sign-out.
3. Vendors list (search, status filter, pagination), vendor detail with contacts, create/edit forms validated with zod schemas mirroring the backend schemas, deactivate with confirmation. Show API validation errors inline. Reuse components/AppShell.tsx and the existing theme.
4. Keep the other seven placeholders untouched.
5. npm run lint, typecheck and build must pass; add a Vitest setup with two component tests (form validation error, list renders rows from a mocked client). Add the test script to package.json and to the CI frontend job.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat(web): vendors screen, typed API client, dev sign-in". Do not begin any further work.
```

## Prompt 17 — Import profiles with defined rule shapes (AC-6) **[plan first]**

```
Implement architecture.md phase 4, vendor import profiles, satisfying AC-6. The vendor_import_profiles table exists (backend/app/models/vendor.py, VendorImportProfile) with six JSONB rule columns whose shapes are undefined. Define them now with JSON Schema and Pydantic, validated on write.

Rule shapes (Pydantic models in backend/app/imports/profile_rules.py, each with a JSON Schema exported for the UI):
- column_map: list of {target: enum[vendor_sku, upc, description, quantity_available, unit_cost, pack_size, unit_of_measure, ignore], source: header text or 0-based index, required: bool}. At least one of upc or vendor_sku must be present; quantity_available is required.
- normalization_rules: {trim: bool, upc_strip_non_digits: bool, upc_pad_to_12: bool, decimal_separator: "."|",", thousands_separator: ""|","|".", currency_symbols_to_strip: [str]}.
- availability_rules: {available_when: "quantity_gt_zero"|"status_column", status_column: str|null, available_values: [str], unavailable_values: [str]}.
- quantity_semantics: {unit: "each"|"case", case_pack_from: "pack_size_column"|"fixed", fixed_pack_size: int|null}.
- price_semantics: {currency: ISO-4217, includes_tax: bool, per: "each"|"case"}.
- pack_size_handling: {mode: "store_as_stated"} only, per docs/phase1-status.md B3 recommendation; leave room for "normalize_to_each" later.
Also: header_signature is computed from the normalised header row and used later to auto-identify a vendor's file.

Then: repository, service (versioning — editing an active profile creates version+1 and deactivates the old one, audited), routes under /api/v1/vendors/{vendor_id}/import-profiles (VIEWER read, DATA_OPERATOR write), a "validate against sample file" endpoint that takes an uploaded CSV/XLSX and returns the first 20 rows as the profile would map them (no persistence), and tests for every rule-shape validator plus integration tests for versioning and the validate endpoint using two fixture files matching the brief's examples: "UPC | Description | Quantity | Cost" and "Vendor SKU | UPC | Qty Available | Wholesale Price". Add openpyxl and python-multipart to pyproject.toml.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md, docs/database-schema.md and docs/decisions/ (an ADR for the rule shapes) in the same commit. Commit with the message "feat: versioned vendor import profiles with validated rule shapes". Do not begin any further work.
```

## Prompt 18 — File upload and raw retention (AC-5 part 1, ADR 0004)

```
Implement architecture.md phase 5: manual file upload with byte-identical raw retention, satisfying the retention half of AC-5 and ADR 0004.

1. StorageBackend protocol in backend/app/imports/storage.py with put(bytes, suggested_name) -> storage_uri, open(storage_uri) -> bytes, exists(uri); a LocalStorageBackend writing under settings.storage_raw_dir as <organization_id>/<yyyy>/<mm>/<sha256>.<ext>, never overwriting, never modifying. Design the URI so an S3/Azure backend can be added without a schema change (import_files.storage_uri is already a plain string).
2. Service backend/app/services/imports.py: receive_upload(session, organization_id, vendor_id, profile_id|None, filename, content, uploaded_by): compute sha256; if a non-failed import_files row with that sha256 exists for the organisation, return it with a "duplicate" flag instead of creating a new one (see the unique index and partial unique index on import_files/import_jobs); otherwise store the bytes, create import_files, create an import_jobs row in status PENDING, audit both.
3. Route POST /api/v1/imports (multipart; DATA_OPERATOR), GET /api/v1/imports, GET /api/v1/imports/{id}, GET /api/v1/imports/{id}/raw (streams the original bytes with the original filename; VIEWER). Reject files over settings.import_max_upload_mb (default 50) and extensions other than .csv/.xlsx with 413/415.
4. Tests: raw bytes round-trip is byte-identical (compare sha256 of upload vs stored vs downloaded); duplicate upload returns the existing job; a FAILED job allows re-upload of the same bytes; storage directory layout; size and type rejections; audit rows.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: vendor file upload with byte-identical raw retention". Do not begin any further work.
```

## Prompt 19 — Parsing, validation and the import report (AC-5 part 2, AC-9) **[plan first]**

```
Implement architecture.md phase 6: profile-driven parsing of CSV and XLSX files into import_job_rows with per-row validation, and the import report the brief's section 33 describes. This prompt stops BEFORE matching; rows end in status OK/WARNING/ERROR with normalised values, and product matching is Prompt 20.

1. Readers in backend/app/imports/readers.py: CSV (encoding from the profile with charset-normalizer fallback, delimiter, quote char, header row index, skip rows) and XLSX via openpyxl (sheet by name or index, read-only mode, values not formulas). Both yield (row_number, {header: cell}) with the header normalised (strip, collapse whitespace, lower).
2. Extraction in backend/app/imports/extract.py: apply column_map (by header text or index), normalization_rules (reuse backend/app/matching/normalize.py from Prompt 11 for UPCs), quantity_semantics, price_semantics, availability_rules; produce a typed ExtractedRow with normalised vendor_sku, normalised_upc (and has_valid_checksum), description, quantity_available, unit_cost, currency, availability_status, pack_size.
3. Validation: missing required column → job FAILED before any row is written, with error_details naming the column; per row: invalid UPC (bad check digit or length) → WARNING with error_code UPC_INVALID (row still imports on vendor SKU); missing quantity or non-numeric quantity → ERROR QUANTITY_INVALID; negative quantity → ERROR; non-numeric price → WARNING PRICE_INVALID (imports with null cost); duplicate vendor SKU within the file → WARNING DUPLICATE_IN_FILE keeping the last occurrence; empty row → SKIPPED.
4. Job runner: process_import_job(session, job_id) runs the whole file in one transaction per 1,000 rows (savepoint per chunk), writes import_job_rows with raw and normalised values, updates import_jobs counters (total/processed/error/skipped), status stays RUNNING; add a current_stage enum column (PARSING, MATCHING, SNAPSHOTTING) to import_jobs by migration to show progress, because ImportJobStatus deliberately has only PENDING/RUNNING/COMPLETED/COMPLETED_WITH_ERRORS/FAILED/CANCELLED; audited. Hook it to run synchronously at upload for now behind a settings flag; the JobRunner from Prompt 13 can schedule it later.
5. Report: GET /api/v1/imports/{id}/report returns the counters plus a breakdown by error_code, and GET /api/v1/imports/{id}/rows?status=ERROR|WARNING|... pages the rows. Produce the brief's example wording in the response ("1,245 rows imported, 3 with invalid quantities…") as a summary string.
6. Tests with fixture files: the two brief layouts as CSV and XLSX; a latin-1 CSV; a semicolon-delimited CSV with decimal commas; a file with a missing required column; a file with every per-row problem above; a 20,000-row generated file to prove chunking and that memory stays flat (read-only openpyxl). Assert the report counters exactly.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: profile-driven CSV/XLSX parsing, row validation and import report". Do not begin any further work.
```

## Prompt 20 — The matching engine (AC-7) **[plan first]**

```
Implement architecture.md phase 7, the deterministic matching engine, satisfying AC-7 and CLAUDE.md section 5 exactly. Prompt 11 already built the UPC normaliser and applied the chain to Amazon listings; generalise that into one engine both callers use.

1. Pure module backend/app/matching/engine.py with no database access: evaluate(candidate: MatchInput, index: MatchIndex) -> MatchOutcome. MatchInput carries normalised_upc, catalog_item_number, vendor_id, normalised_vendor_sku, amazon_seller_sku, description. MatchIndex is an in-memory lookup built by a repository (upc → [product_id], catalog_item_number → product_id, (vendor_id, vendor_sku) → product_id for APPROVED vendor_products only, amazon_sku → product_id for APPROVED marketplace_listings only). MatchOutcome mirrors the existing MatchResult enum exactly: MATCHED(product_id, method, priority) | AMBIGUOUS(priority, candidates) | UNMATCHED(evaluations) | SUGGESTION_ONLY(candidates with scores, evaluations) — evaluations list every rule tried, in order, with its result, so the attribution is reproducible.
2. Rules in strict order 1–4 as CLAUDE.md 5.1; stop at the first rule that yields exactly one product; a rule yielding more than one product ends evaluation as AMBIGUOUS at that priority (never fall through to a weaker rule). Rule 5 (suggestion) runs only when rules 1–4 all yield zero: trigram similarity on description against product names using the existing pg_trgm index via the repository, producing candidates with scores, never a match.
3. Integration backend/app/services/matching.py: match_import_job(session, job_id) builds the index for the organisation and the job's vendor, runs evaluate over every OK/WARNING row, and for MATCHED rows sets import_job_rows.match_result/matched_by/match_priority/product_id (satisfying the deciding-rule CHECK), creates or updates the vendor_products row (mapping_status stays as-is for APPROVED; for rows matched by UPC or catalog number, create the vendor_products row with mapping_status PENDING and open a SUGGESTION_ONLY exception — per CLAUDE.md a vendor-SKU mapping becomes permanent only through approval); for AMBIGUOUS/UNMATCHED/SUGGESTION_ONLY open a product_mapping_exceptions row with the right reason, candidates and match_evaluations. current_stage → MATCHING done (status stays RUNNING until Prompt 22's snapshot step finishes); counters matched_rows/exception_rows; audited.
4. Determinism test: run the same job twice against the same mapping state and assert byte-identical outcomes and attributions.
5. Unit tests for engine.py with explicit fixture cases, each named for the rule: exact UPC; UPC matching two products → AMBIGUOUS at priority 1 even though the vendor SKU would have matched; exact catalog number; approved vendor SKU reused on second import; approved Amazon SKU; description identical to a product name with no identifiers → SUGGESTION_ONLY, never MATCHED; empty input → UNMATCHED with four evaluations recorded.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/architecture.md (the matching section) in the same commit. Commit with the message "feat: deterministic product matching engine and import matching step". Do not begin any further work.
```

## Prompt 21 — Exception workflow that creates permanent mappings (AC-8)

```
Implement architecture.md phase 8 satisfying AC-8: the queue where a human resolves what the engine could not.

1. Routes under /api/v1/exceptions (PURCHASING_MANAGER or ADMIN to resolve; VIEWER to read): GET list with filters (reason, status, vendor, import job, age), GET detail (candidates, evaluations, the raw row), POST /{id}/approve {product_id} → sets the vendor_products (or marketplace_listings) row to APPROVED with mapping_method MANUAL_APPROVAL, approver and timestamp; marks the exception APPROVED (ExceptionStatus has PENDING/APPROVED/REJECTED); updates the originating import_job_rows row to match_result MATCHED, matched_by MANUAL_APPROVAL, priority 5; POST /{id}/reject {note} → exception REJECTED, mapping stays UNMAPPED; POST /{id}/defer {until, note} keeps status PENDING and sets a deferred_until column added by migration (deferred items drop out of the default queue view until that time). Every action audited with before/after.
2. Supersession: approving a product for a vendor SKU that already has an APPROVED mapping to a different product requires an explicit {supersede: true} flag, marks the old mapping SUPERSEDED (never deleted), and records both in the audit — this is the "superseded only by an explicit, audited human action" rule.
3. Re-import test: import the fixture file → row lands in the queue → approve → import the same file again → the row matches at priority 3 with no new exception. This is Milestone 1 exit criterion #7; write it as an end-to-end integration test.
4. Frontend: replace frontend/src/app/exceptions/page.tsx with a queue screen (filters, detail drawer showing the raw row, candidates with scores and the rule evaluations, product search by catalog number / UPC / name, approve / reject / defer with a note). Copy the patterns from the vendors screen.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: mapping exception queue with permanent, supersedable approvals". Do not begin any further work.
```

## Prompt 22 — Inventory snapshots, availability events and the watchlist (AC-10, AC-11)

```
Implement architecture.md phase 9 satisfying AC-10 and AC-11 — the brief's "Product ABC is now available from Vendor B" flow.

1. After matching, write one vendor_inventory_snapshots row per MATCHED row (quantity, unit_cost, currency, availability_status, effective_at = file timestamp or job start, captured_at). Snapshots are append-only.
2. Diff: for each vendor_products row in the job, compare the new snapshot with the previous snapshot for the same vendor product (by captured_at); when availability flips, insert an availability_events row (BECAME_AVAILABLE or BECAME_UNAVAILABLE, previous_snapshot_id, current_snapshot_id) — the unique (current_snapshot_id, event_type) index prevents duplicates. A vendor product seen for the first time with quantity > 0 also raises BECAME_AVAILABLE. Then set the job status to COMPLETED, or COMPLETED_WITH_ERRORS when error_rows > 0.
3. Watchlist: routes under /api/v1/watchlist (add with desired_quantity, max_unit_cost, priority, reason; remove; list) — PURCHASING_MANAGER to write; audited. When an availability event is BECAME_AVAILABLE and the product is on the watchlist, link the event (oos_watchlist_id), move the watchlist row's current_status to IN_STOCK (OosStatus has IN_STOCK/OUT_OF_STOCK/UNKNOWN) with an oos_status_history row, and honour max_unit_cost (an event above the ceiling is linked but flagged, not silently dropped).
4. Availability feed: GET /api/v1/availability/events with filters (vendor, product, watchlist-only, since) and GET /api/v1/watchlist/{id}/history.
5. End-to-end test for exit criterion #9: watchlist a product; import a file where the vendor shows 0; import a second file where it shows 40 → exactly one BECAME_AVAILABLE event linked to the watchlist row, status IN_STOCK, one history row, audit entries; import the second file a third time → no new event.
6. Frontend: replace watchlist/page.tsx and availability/page.tsx with working screens.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat: vendor inventory snapshots, availability events and OOS watchlist". Do not begin any further work.
```

## Prompt 23 — Nineyard catalog sync (AC-2, AC-3) **[plan first]** — only after the client answers B1

```
Prerequisites (do not start without them): Nineyard credentials in .env, and written answers from the client or Nineyard to docs/phase1-status.md B1: which field is the Catalog Item Number, how Items relate to Skus, whether an updated-since filter exists, and how deletions appear. Paste those answers into docs/nineyard-field-mapping.md first.

Then implement architecture.md phase 3 satisfying AC-2 and AC-3:
1. Run the existing probe (`python -m app.cli.nineyard_probe`) against the real account and commit its sanitised output under docs/nineyard-probe-output/ so the field mapping is evidence-based.
2. Extend backend/app/integrations/nineyard/client.py with typed list_items(page/cursor) and list_skus(...) only — still read-only, keep the structural test.
3. Sync service backend/app/services/nineyard_sync.py: create a nineyard_sync_runs row; page through Items and Skus; retain every raw payload in source_records with payload_sha256 (skip unchanged); upsert products by catalog_item_number (create with a new UUID, update name/brand/attributes, mark absent-from-full-pull products as INACTIVE only if the deletion semantics from B1 say absence means deletion — otherwise never deactivate automatically); upsert product_identifiers of type UPC and AMAZON_SKU from the Sku records; upsert marketplace_listings for Amazon SKUs with mapping_status APPROVED and mapping_method AMAZON_SKU_MAPPING because Nineyard is the client's authoritative source; counters and audit as in the Amazon runs; schedule nightly via the JobRunner.
4. Routes: POST /api/v1/nineyard/sync (ADMIN, trigger MANUAL), GET /api/v1/nineyard/runs, GET /api/v1/products with search by catalog number / UPC / name and GET /api/v1/products/{id} showing identifiers, Amazon SKUs, vendor products and snapshots.
5. Tests: sync from recorded fixtures (create, update, unchanged, identifier conflict → exception queue with CONFLICTING_IDENTIFIER), idempotency, and that a second sync with an unchanged payload writes no source_records. Replace products/page.tsx with a working product search and detail screen.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/nineyard-integration.md in the same commit. Commit with the message "feat: Nineyard catalog synchronisation into products, identifiers and listings". Do not begin any further work.
```

## Prompt 24 — Remaining admin screens and the end-to-end test (AC-13)

```
Complete architecture.md phase 10 satisfying AC-13. Replace the last placeholders — imports/page.tsx (upload with drag-and-drop, job list, job detail with the report and row browser filtered by status, raw download), profiles/page.tsx (profile editor generated from the JSON Schemas of Prompt 17, with the validate-against-sample preview), audit/page.tsx (filter by entity, actor, action, date; before/after diff view) — and a home dashboard that shows real counts: open exceptions, watchlist items newly available in the last 7 days, last import per vendor, last Amazon and Nineyard sync status.

Add a Playwright end-to-end test (frontend/e2e/) that runs against the compose stack in CI: sign in → create vendor → create profile → upload the fixture CSV → see the report → open the exception → approve → re-upload → see it match automatically → add to watchlist → upload the "now available" fixture → see the availability event. This is the Milestone 1 exit-gate walk-through in docs/acceptance-criteria.md; wire it into the compose-smoke CI job.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat(web): imports, profiles, audit and dashboard screens; Playwright end-to-end". Do not begin any further work.
```

## Prompt 25 — Hardening and Milestone 1 exit

```
Complete architecture.md phase 11 and walk the Milestone-level exit gate in docs/acceptance-criteria.md.

1. Performance: generate a 50,000-row vendor CSV and a 50,000-row XLSX; import both; record wall time and peak memory; add an index or change chunking if the import exceeds 60 s or 500 MB; record the numbers in docs/phase1-status.md.
2. Query review: EXPLAIN ANALYZE the exceptions list, availability feed, product search and velocity view against the 50k dataset; fix anything doing a sequential scan on a large table.
3. Backup and restore rehearsal: pg_dump the database, restore into a fresh database, run alembic check and the integration suite against the restored copy; write docs/runbook.md covering backup, restore, rotating the JWT secret and Amazon/Nineyard credentials, re-running a failed sync, and clearing a stuck RUNNING run.
4. Seed script backend/app/cli/seed_demo.py producing a demo organisation with two vendors, two profiles, fixture imports and a watchlist, for client demos and CI.
5. Walk every item of the Milestone-level exit gate and every AC-0 through AC-13 criterion; for each, paste the command or test name that proves it. Anything unproven stays listed as open in docs/phase1-status.md — do not mark it done.
6. Re-check docs/architecture.md section 5 (superseded entity list) and either update it to the shipped schema or replace it with a pointer to docs/database-schema.md, so a reader never finds a contradiction.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md to declare Milestone 1 status honestly. Commit with the message "chore: Milestone 1 hardening, runbook, seed data and exit-gate evidence". Do not begin any further work.
```

---

# Stage 3.5 — Corrections found in the Part 2 review of 194643e (run before showing the client)

These fix everything rated BLOCKER or MAJOR in Part 2 of `PRMS-code-review-2026-09-16.md`. Stage 2.5 (13a–13g) is still owed as well. Order: 25a first (it decides whether CI is green), then 2.5, then 25b–25d.

## Prompt 25a — Make the compose-smoke CI job actually pass (BLOCKER B2)

```
The compose-smoke job in .github/workflows/ci.yml cannot pass as wired, so "verified in CI" is not true for the current head. Root cause: infra/docker-compose.yml passes an explicit `environment:` block to the api service with no `env_file:` and no DEV_AUTH_ENABLED; `--env-file .env` only drives interpolation, so the container never sees DEV_AUTH_ENABLED=true from .env.example, Settings.dev_auth_enabled stays False, and the seed step (`python -m app.cli.seed_dev`, seed_dev.py:116) exits with "DEV_AUTH_ENABLED must be true" before Playwright starts; POST /api/v1/auth/dev-token would 404 anyway.

1. In infra/docker-compose.yml pass through every variable the smoke walk-through needs with a safe default: DEV_AUTH_ENABLED: ${DEV_AUTH_ENABLED:-false}, AUTH_JWT_SECRET: ${AUTH_JWT_SECRET:-<the dev default the settings accept>}, EXPOSE_ERROR_DETAILS, IMPORT_PROCESS_ON_UPLOAD, and the AMAZON_* variables (defaulting to unset/false). Do not add `env_file:` wholesale — keep the explicit list so production cannot inherit dev flags by accident.
2. The `../storage:/storage` bind mount: the image runs as `appuser` (backend/Dockerfile:34-37) while a GitHub runner's checkout is uid 1001 / mode 755, so the first upload's mkdir will likely fail with EACCES. Fix it robustly (a named volume for /storage in the smoke job, or an entrypoint that chowns /storage when writable, or run the container with the host uid via `user: "${UID:-1000}:${GID:-1000}"`) and explain the choice in docs/deployment.md.
3. Add `docker compose --env-file .env.example -f infra/docker-compose.yml config -q` as a step in the compose-smoke job so the 8th local gate is also a CI gate.
4. Add `-rs` to the backend pytest step and make backend/tests/integration/conftest.py call pytest.fail (not skip) when the CI environment variable is set, so an unreachable PostgreSQL turns the job red instead of skipping 409 tests. Add a final step that fails if the pytest summary contains "skipped".
5. Push, watch the workflow, and paste the run URL and the per-job results for this exact commit. If compose-smoke still fails, paste the container logs and fix the next root cause — do not mark anything verified until all three jobs are green on this head.
6. Update docs/phase1-status.md: replace every "verified in CI" / "8 of 8 in GitHub Actions" statement with the run URL for this commit and the date; remove "main has not been pushed since 2026-09-14"; change the CI links in README.md and phase1-status.md from the cbfriedman URL to frostwebdev-dotcom.

When done: run `.\tasks.ps1 check` and paste the full results. Commit with the message "ci: compose-smoke passes end to end; compose config gate; fail on skipped tests". Do not begin any further work.
```

## Prompt 25b — Matching-stage robustness (MAJOR M1–M4) **[plan first]**

```
Four defects in the import pipeline's matching and snapshot stages:

1. No failure path. backend/app/services/matching.py (~line 151) and backend/app/services/snapshots.py (~line 139) call `.run()` bare, unlike import_processing.py:91-99 which catches, rolls back and fails the job. A crash strands the job RUNNING at MATCHING/SNAPSHOTTING forever, and because the partial unique index on import_jobs excludes only FAILED/CANCELLED and there is no cancel endpoint, the same file can never be re-imported. Wrap both stages in the same try/except → rollback → fail(FAIL_INTERNAL, ...) pattern with audit, add POST /api/v1/imports/{id}/cancel (DATA_OPERATOR, only for RUNNING/PENDING jobs, audited) so an operator can always free a file, and add tests: an injected exception in each stage leaves exactly one FAILED job, the upload route returns the failed job rather than 500, and re-uploading the bytes creates a new job.
2. Rejected suggestions are re-applied. reject() sets the vendor line to UNMAPPED (services/exceptions.py ~200-205), but on the next import _record_match sets line.product_id / mapping_status=PENDING / mapping_method (matching.py ~415-420) before _open_item detects the identical rejected evidence and returns without a queue item (~442-460). Result: a PENDING line pointing at the rejected product with nothing to resolve it. Move the rejected check before the line is modified: if the same reason and candidates were rejected, leave the line UNMAPPED, record previously_rejected on the row, and do not touch mapping columns. Test: reject → re-import same file → line still UNMAPPED, no new item, row annotated.
3. UPC-only vendor files never reach snapshots. ColumnMap allows upc without vendor_sku (imports/profile_rules.py ~92-93) but a vendor line exists only `if row.vendor_sku` (matching.py ~335, ~403-407) and the snapshot stage only takes rows with vendor_product_id (snapshots.py ~181), so availability detection is silently inert for such vendors. Decide and implement one of: (a) require vendor_sku in ColumnMap and reject profiles without it with a clear message, or (b) synthesise a vendor line keyed by the normalised UPC (vendor_sku = "UPC:<gtin>") so snapshots and events work. Prefer (b) — the brief's Vendor A layout (UPC | Description | Quantity | Cost) has no vendor SKU and must produce availability events. Record the choice in an ADR (next number is 0015 if 0014 is taken by the listings decision), and add an end-to-end test with the Vendor A layout: import at 0 → watch → import at 40 → one BECAME_AVAILABLE.
4. Priority 2 is unreachable. ColumnTarget has no catalog_item_number target, so import_job_rows.catalog_item_number is always NULL and rule 2 always reports "skipped". Add the target to ColumnTarget and extract_row, thread it into MatchInput, and add an engine test where only the catalog number matches (rule 2 fires, recorded as CATALOG_ITEM_NUMBER, priority 2) and one where a UPC hit beats a catalog-number hit.

Also fix the two small ones nearby: VendorUpdate.status must not bypass the deactivate rules (schemas/vendors.py ~107; either remove status from the PATCH schema or route it through deactivate/reactivate), and in-file dedupe (import_processing.py ~247-251) must keep the last VALID occurrence, not the last row regardless of status.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md and docs/acceptance-criteria.md (a criterion for cancel and for UPC-only vendors) in the same commit. Commit with the message "fix(imports): fail-safe matching and snapshot stages, cancel endpoint, rejected-suggestion guard, UPC-only vendor lines, catalog-number target". Do not begin any further work.
```

## Prompt 25c — Frontend hardening (MAJOR M5, M6)

```
1. The bearer token is persisted in localStorage (frontend/src/lib/auth-storage.ts). Keep it in memory only (module state inside AuthProvider), re-acquire on reload through the dev sign-in, and delete auth-storage.ts. When the production identity provider lands it will be an httpOnly cookie; leave a one-line comment saying so. On a 401, clear the in-memory token AND set the provider state to signed-out so the shell shows the sign-in immediately.
2. Collapse the two ApiError classes (lib/api-client.ts and lib/api.ts) into one in lib/api.ts and make ApiStatus.tsx use it; delete api-client.ts.
3. Add Vitest + @testing-library/react + zod (Prompt 16 asked for these; they were never added). Write at least eight component tests: sign-in form validation error, vendors list renders rows from a mocked client, vendor form shows an API validation error inline, profile column-map form rejects a map with neither upc nor vendor_sku, import report renders counters and the error-code breakdown, exception drawer shows candidates and rule evaluations and calls approve with the chosen product, watchlist add validates the product id, 401 from any query flips the app to signed-out. Add `npm test` to package.json and to the frontend CI job.
4. Playwright: set retries to 0 in CI so flakes are visible, and keep the trace-on-failure upload.

When done: run `.\tasks.ps1 check` and paste the full results. Update docs/phase1-status.md in the same commit. Commit with the message "feat(web): in-memory auth token, single ApiError, Vitest component tests". Do not begin any further work.
```

## Prompt 25d — Documentation sweep (MAJOR M8)

```
Fix every stale or contradictory statement found in review, in one commit, changing no code:

- README.md ~8-11 and ~182: "21 tables, one reversible migration … no vendor CRUD, file ingestion, or matching logic exists yet" → describe the current state (25 tables, 7 migrations, the import lifecycle, the Amazon ingestion, what is still open).
- CLAUDE.md ~58: "Storing an Amazon SKU is in scope; calling Amazon is not." → "Calling Amazon is limited to the read-only ingestion in ADR 0011."
- docs/phase1-status.md §3 ~63 and ~133: "21 tables" / "One revision … 21 enum types" → current numbers, or rewrite §3 as history with dates; remove every "main has not been pushed since 2026-09-14"; the Railway claim (§1, §6 S2) gets the service URL, deploy date and a health-check transcript, or is removed; fix the AC evidence file attributions for AC-0.2 (test_settings_secrets.py), AC-11.5 (test_import_processing.py) and AC-12.1 (test_availability_api.py); phase 4 "70 rule/reader/mapper tests" → the real count.
- docs/database-schema.md: ~11 enum/index counts to match the migrations (23 enums); ~463 AMAZON_SKU "has a marketplace_listing_id" → nullable; ER lines ~54-55 optionality (o| not ||) for PRODUCTS→MARKETPLACE_LISTINGS and MARKETPLACE_LISTINGS→PRODUCT_IDENTIFIERS; ~821 "152 database-backed tests" → current.
- docs/architecture.md: remove the superseded §5 entity list entirely (keep the banner and the planned-vs-shipped table) so "Count: 22 tables" at ~468 disappears.
- docs/deployment.md ~262: literal TAB in `.\tasks.ps1`.
- docs/decisions/0013-import-profile-rule-shapes.md: add the template's Alternatives table.
- docs/runbook.md ~60: explain the 408-vs-409 test-count difference on the restored copy or fix the cause.
- All CI/run links: cbfriedman URL → frostwebdev-dotcom.

Then run a consistency check: grep the docs for every number that also appears in docs/phase1-status.md §1 (tables, enums, indexes, checks, FKs, revisions, tests) and make them agree, listing each file:line you changed in the commit message body.

When done: run `.\tasks.ps1 check` and paste the full results. Commit with the message "docs: reconcile README, CLAUDE.md, status, schema and architecture docs with the shipped code". Do not begin any further work.
```

---

# When something goes wrong

Paste one of these instead of improvising.

**A gate fails:**
```
The gate output above shows a failure. Fix the root cause without weakening or skipping any test, type check or lint rule. If the failure is in a test that is genuinely wrong, explain why before changing it. Re-run `.\tasks.ps1 check` and paste the full results.
```

**Claude Code wants to widen scope:**
```
That is outside the current prompt and outside CLAUDE.md section 3. Do not build it. Note it as a one-line follow-up at the bottom of docs/phase1-status.md and continue with the current task only.
```

**A live API call fails (Amazon or Nineyard):**
```
Paste the exact request (with secrets redacted), the response status and body, and the retry log. Do not change credentials or settings. If the cause is a permission/role on the app, tell me exactly which role to ask the client to enable. If the cause is rate limiting, show me the current limit handling and propose the minimal change.
```

**Claude Code's summary claims something that is not in the diff:**
```
Run `git diff --stat` and `git status`, and list every file you changed with one line each on what changed. Then reconcile that list with your summary above and correct any claim that the diff does not support.
```
