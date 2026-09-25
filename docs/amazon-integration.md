# Amazon SP-API Integration

Status: **Implementation complete; live validation pending.** Client, tables,
the three ingestions, listings→product mapping, scheduler, `amazon_poc` CLI,
and read-only velocity HTTP endpoint exist and are tested. Nothing has run
against the real seller account (blocking question **B8**).
Last updated: 2026-09-22

---

## 1. Why a wrapper, and why this shape

[ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)
admits a bounded, read-only SP-API ingestion into Milestone 1. The library
chosen to talk to Amazon, `python-amazon-sp-api` (MIT), exposes the whole
Selling Partner API — feeds, listings writes, pricing, orders, shipments,
several hundred operations — through a family of classes that share one
credential mechanism. Handing that library to application code would mean the
read-only bound rests on nobody ever calling the wrong method.

`app/integrations/amazon/` is the anti-corruption layer (ADR 0007, applied
here as it was to Nineyard). It wraps exactly the calls the POC needs, maps
every result into a frozen, typed DTO, translates the library's dozen
exception classes into five of our own, and owns the retry policy. Nothing
above it imports `sp_api`.

The library was inspected before the wrapper was written — signatures, the
credential provider, the response wrapper, the exception hierarchy — so the
calls below match what is installed (`python-amazon-sp-api` 2.1.23), not a
guess at it.

---

## 2. The boundary

### What the library needs, and where it gets it

The library authenticates with Login with Amazon: a long-lived **refresh
token** is exchanged for a short-lived access token, which it sends as the
`x-amz-access-token` header. It wants those credentials as a plain dict:

```python
{"refresh_token": ..., "lwa_app_id": ..., "lwa_client_secret": ...}
```

That dict is built in one private method, `AmazonClient._credentials()`,
from the `SecretStr` values on `AmazonConfig`, and passed straight to the
library constructor by `AmazonClient._api()`. It is not stored on the client,
not logged, and not returned by anything. A test asserts the library received
exactly those three keys and that no attribute of the client holds the plain
dict.

`AmazonConfig.from_settings(settings)` is the only way in. It refuses to build
if any of `AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`,
`AMAZON_LWA_REFRESH_TOKEN` or `AMAZON_SELLER_ID` is missing, naming the
missing ones; resolves `AMAZON_MARKETPLACE_ID` to the library's marketplace
entry; and refuses a marketplace that is not served from `AMAZON_REGION` (a
UK marketplace id with `AMAZON_REGION=NA` would otherwise be sent to the
North American endpoint and fail with an opaque 400).

### Two things the library does that the wrapper closes off

- **Environment override.** `sp_api.base.Client.__init__` reads
  `SP_API_DEFAULT_MARKETPLACE` from the environment and lets it **override an
  explicitly passed marketplace**. `AmazonClient` refuses to construct while
  that variable is set, so the marketplace can only come from `Settings`.
- **Credential discovery.** Left to itself the library will look for
  credentials in its own environment variables and config files. The wrapper
  always passes the dict explicitly, so nothing outside `Settings` is
  consulted.

### Secrets in logs

The redaction rules in `app/core/redaction.py` (extended for this work) mask
`refresh_token`, `client_secret`, `lwa_*` and `x-amz-access-token` by key;
mask the JSON form `"access_token": "..."` that an HTTP library would log from
the token endpoint; and mask the bare LWA token shape (`Atza|…`, `Atzr|…`)
wherever it appears. The library's own loggers (`sp_api.*`) go through the
stdlib path, which is redacted too. A test drives a request through the real
logging pipeline at `DEBUG` and asserts no secret value reaches the output.

---

## 3. The public surface — five reads, nothing else

`AmazonClient` exposes exactly these methods. A test asserts the set, and a
second test asserts no public method name starts with `create_`, `put_`,
`post_`, `update_`, `delete_` or `submit_` (other than `request_report`).
There is no generic `call(method, path)`, no `request`, `get`, `post` — no
way to reach any other library endpoint through this object.

| Method | Returns | What it does |
|---|---|---|
| `request_report(report_type, data_start, data_end, report_options=None)` | `ReportRequest` | Asks Amazon to generate a report for the marketplace. The one non-GET call SP-API needs for a read; it creates a queued export and nothing else. `data_start`/`data_end` must be timezone-aware UTC. |
| `get_report_status(report_id)` | `ReportStatus` | Where the report is in Amazon's queue. |
| `download_report_document(report_document_id)` | `bytes` | The finished document. The library downloads it, gunzips it if Amazon compressed it, and decodes it using the charset Amazon declared; that text is returned as **UTF-8 bytes** so callers see one encoding. |
| `fetch_report(report_type, data_start, data_end, report_options=None, *, poll_interval_s=15, timeout_s=1800)` | `bytes` | Request → poll until terminal → download. Raises `AmazonReportFailed` on `CANCELLED`, `FATAL`, `DONE` without a document, or timeout. |
| `iter_inventory_summaries(*, details=True, page_delay_s=0.0)` | `Iterator[InventorySummary]` | Every FBA inventory summary for the marketplace, following `nextToken` until exhausted. `details=True` requests the `inventoryDetails` block, which is where the inbound quantities live. `page_delay_s` is slept *between* pages — the retry policy reacts to throttling; the delay avoids it. |

Inventory traversal is buffered until every page succeeds. If Amazon rejects a
continuation token as invalid or expired, the client discards that incomplete
buffer and restarts once from page 1; callers therefore never receive duplicate
or partial pages.

Report types are passed as strings (the library's `ReportType` enum values,
e.g. `GET_MERCHANT_LISTINGS_ALL_DATA`,
`GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL`,
`GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`). Which reports the ingestion uses
is decided in the next step, not here.

---

## 4. The DTOs

All frozen dataclasses in `app/integrations/amazon/dtos.py`. Each has a
`from_payload` classmethod that names the SP-API field it reads, so the
mapping lives in one place and a renamed upstream field fails there, loudly.
No SP-API field name appears in `app/models/`.

| DTO | Fields | Source fields |
|---|---|---|
| `ReportRequest` | `report_id` | `createReport` → `reportId` |
| `ReportStatus` | `processing_status`, `report_document_id`; properties `is_terminal`, `is_done` | `getReport` → `processingStatus` (`IN_QUEUE` / `IN_PROGRESS` / `DONE` / `CANCELLED` / `FATAL`), `reportDocumentId` |
| `InventorySummary` | `seller_sku`, `asin`, `fnsku`, `condition`, `last_updated`, `total`, `fulfillable`, `inbound_working`, `inbound_shipped`, `inbound_receiving`, `reserved_total`, `unfulfillable_total`, `researching_total` | FBA Inventory v1 `InventorySummary` → `sellerSku`, `asin`, `fnSku`, `condition`, `lastUpdatedTime`, `totalQuantity`, and under `inventoryDetails`: `fulfillableQuantity`, `inboundWorkingQuantity`, `inboundShippedQuantity`, `inboundReceivingQuantity`, `reservedQuantity.totalReservedQuantity`, `unfulfillableQuantity.totalUnfulfillableQuantity`, `researchingQuantity.totalResearchingQuantity` |

Quantities are `int | None`. `None` means Amazon omitted the field — which it
does for the whole `inventoryDetails` block unless `details=true` was
requested — and is never silently turned into `0`. A boolean in a quantity
field is treated as absent, not as `1`. `seller_sku` and the report ids are
required; a payload without them raises `AmazonError` naming the missing key
and the keys that were present.

---

## 5. Error taxonomy

All in `app/integrations/amazon/errors.py`, all subclasses of `AmazonError`,
each with a `guidance` string for whoever reads the log or CLI output.

| Exception | Raised when | Retried? |
|---|---|---|
| `AmazonConfigurationError` | Credentials missing, unknown marketplace id, marketplace not in `AMAZON_REGION`, `SP_API_DEFAULT_MARKETPLACE` set, or the library reports missing credentials | No |
| `AmazonAuthError` | Login with Amazon rejected the refresh token or client credentials (`sp_api.auth.exceptions.AuthorizationError`) | **Never** — the same credentials give the same answer |
| `AmazonRateLimited` | 429 on every attempt. Carries `retry_after_seconds` if Amazon sent one | Was retried; this is the exhausted result |
| `AmazonTransientError` | 500 / 503 / 504 or an HTTP transport failure on every attempt. Carries `status_code` for HTTP responses | Was retried; this is the exhausted result |
| `AmazonReportFailed` | `fetch_report` saw `CANCELLED` or `FATAL`, `DONE` without a document id, or ran out of time. Carries `report_id`, `processing_status`, `timed_out` | No |
| `AmazonError` (base) | Any other definite answer — a 400 for a bad report type, a 404, a payload without a required field | No |

The library's exception for each status is caught inside the client; none of
them, and none of the library's response types, cross the boundary.

---

## 6. Retry policy

Applied to every library call by `AmazonClient._call_response`:

- **Retried:** the library's `SellingApiRequestThrottledException` (429),
  `SellingApiServerException` (500), `SellingApiTemporarilyUnavailableException`
  (503), `SellingApiGatewayTimeoutException` (504), and `httpx.TransportError`
  failures such as a remote disconnect.
- **Not retried:** everything else. An LWA failure, a 400, a 403, a 404, a 409
  are definite answers; repeating them only repeats the mistake more slowly.
- **Attempts:** `AMAZON_MAX_ATTEMPTS` (default 5) in total, so at most four
  retries. No sleep after the final failure.
- **Delay:** Amazon's `Retry-After` header if present, otherwise
  `2^(attempt-1)` seconds multiplied by jitter in `[0.5, 1.0)`. Either way
  capped at `MAX_BACKOFF_SECONDS` (60). Jitter matters even for one client:
  without it, retries after a throttling burst synchronise onto the same
  instants.
- **Pagination and retries compose:** a throttled page of inventory summaries
  is re-requested with the same `nextToken`, so a retry never skips or repeats
  a page. Amazon's tokens expire after 30 seconds, so inventory page attempts
  are capped at 20 seconds even when `AMAZON_TIMEOUT_SECONDS` is higher. If
  Amazon still invalidates a token, one buffered traversal restart prevents
  partial or duplicate snapshots.
- **Polling is not retrying:** `fetch_report` waits `poll_interval_s` between
  status checks regardless of outcome; the retry policy applies to each
  individual status call inside that loop.

`sleep` and the clock are injectable, so the tests cover backoff growth, the
cap, `Retry-After`, exhaustion, and the polling timeout without waiting.

---

## 7. Orders ingestion

`app/services/amazon_orders.py` is the first consumer of the client. One
call, `run_orders_sync(session, organization_id, window_start, window_end,
trigger_type, triggered_by_user_id=None, client=None)`, performs one run and
returns its closed `AmazonSyncRun`.

### The run, step by step

1. **Validate the window.** Timezone-aware UTC, end after start, end not in
   the future, and at most `MAX_WINDOW_DAYS` (30) long — Amazon's limit for
   this report, refused here with a message that says so.
   `trailing_window(days=30)` gives the scheduler `[now - 30d, now]`.
2. **Claim the slot.** A `RUNNING` row is inserted and committed in its own
   transaction before anything else happens. The partial unique index
   `uq_amazon_sync_runs_running_job` is the lock: a second attempt raises
   `OrdersSyncAlreadyRunning` and fetches nothing.
3. **Fetch.** `client.fetch_report("GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL", …)`
   — request, poll, download, through the read-only client.
4. **Parse** (below).
5. **Upsert and close, in one transaction.** Lines are upserted, the run is
   set to `COMPLETED` (or `COMPLETED_WITH_ERRORS` when any row was
   rejected) with its five counts, and an audit event
   `amazon.orders_sync.completed` is recorded with actor type `SYSTEM` — all
   in the same transaction (ADR 0006).
6. **On any failure**, the run is closed as `FAILED` in a fresh transaction
   with the exception class and message in `error_details`, audited as
   `amazon.orders_sync.failed`, and the exception is re-raised. This is a
   `try/except/finally`: a run row can end `RUNNING` only if the process
   dies between the claim and the `finally`.

### Parsing the report

The flat file is read with the `csv` module (tab delimiter), decoded as
UTF-8 (BOM tolerated) with a fallback to Latin-1. Twelve columns are
required — `amazon-order-id`, `purchase-date`, `last-updated-date`,
`order-status`, `fulfillment-channel`, `sales-channel`, `sku`, `asin`,
`item-status`, `quantity`, `currency`, `item-price` — and a report missing
any of them fails the run with the missing names in the message.

**The `ship-*` columns are never read.** The retained `raw` dict is built
from the required-column list, not from the row, so `ship-city`,
`ship-state`, `ship-postal-code` and `ship-country` (and `product-name`)
cannot reach the database by accident. A unit test asserts `raw` holds
exactly the twelve columns; a schema test asserts the table has no
PII-shaped column.

Rows are **aggregated per `(amazon-order-id, sku)`** — the report has no
order-item id, and one order can list the same SKU twice. Quantities are
summed; statuses, price and timestamps come from the line with the latest
`last-updated-date`. Timestamps are parsed as ISO 8601 and normalised to
UTC. A row that cannot be interpreted (a non-integer quantity, a blank SKU,
an unparseable date) is counted in `rows_failed` and described in
`error_details.rows` with its row number; the rest of the report still
loads. A price without a currency is dropped rather than stored, because
the table's check forbids the pair.

### The upsert

`INSERT … ON CONFLICT (organization_id, amazon_order_id, seller_sku) DO
UPDATE`, in batches of 500, with two rules enforced in SQL *and* counted in
Python:

- data columns change only when the incoming `last_updated_at` is **newer**
  than the stored one (`CASE WHEN excluded.last_updated_at >
  amazon_order_lines.last_updated_at …`) — re-running an old window cannot
  regress a line;
- `last_seen_sync_run_id` is set on every line the report contained,
  changed or not; `first_seen_sync_run_id` is never touched after insert.

Counts (`rows_created` / `rows_updated` / `rows_unchanged`) come from a
read of the existing keys in the same transaction; the one-`RUNNING`-run
index is what makes that read safe.

### A defect this work found in the foundation

`app/db/transaction.py` had `session.commit()` in an `else` branch. A
constraint violation raised *by the commit itself* — an object added in the
body and first flushed at commit time — was therefore never rolled back,
and the session was left in a pending-rollback state that failed every
later statement. The orders sync hit it on its second call. `commit()` now
sits inside the `try` in both `transaction()` and `savepoint()`, and a
regression test in `test_transactions.py` covers the commit-time path.
Phase 2's vendor CRUD would have found the same defect on its first
duplicate code.

---

## 8. Inventory ingestion

`app/services/amazon_inventory.py` — `run_inventory_sync(session,
organization_id, trigger_type, triggered_by_user_id=None, client=None,
listings_sink=None)` — has the same run/transaction/audit shape as the
orders sync (the shared pieces now live in `app/services/amazon_runs.py`:
`open_run`, `close_run`, `fail_run`). Job type `FBA_INVENTORY`; audit
actions `amazon.inventory_sync.completed` / `.failed`.

### Two reads, one snapshot per SKU

1. **FBA Inventory** — `iter_inventory_summaries(details=True,
   page_delay_s=AMAZON_INVENTORY_PAGE_DELAY_S)`. The default delay of 0.6 s
   between pages keeps a 450-SKU account (≈ 9 pages of 50) under the
   endpoint's ~2 requests/second without waiting for a 429 to say so.
2. **The merchant listings report** — `GET_MERCHANT_LISTINGS_ALL_DATA` via
   `fetch_report`. FBA Inventory knows nothing about merchant-fulfilled
   stock; this report's `fulfillment-channel` column does. Eight columns are
   read: `seller-sku`, `quantity`, `fulfillment-channel`, `asin1`,
   `product-id`, `product-id-type`, `item-name`, `status`. Rows whose
   channel is `DEFAULT` are FBM and their `quantity` is the FBM quantity; a
   blank quantity stays `None`; an unknown channel value is kept verbatim
   and **not** treated as FBM. `product-id-type` is mapped 1 → ASIN,
   2 → ISBN, 3 → UPC, 4 → EAN for the listings mapping that follows.

The two are merged per seller SKU into one `AmazonInventorySnapshot` with
`captured_at` = the run's start time:

- every FBA summary produces a row; an omitted quantity becomes `0`
  explicitly, and `raw` keeps the API's own values (`None` preserved) so a
  coerced zero is traceable;
- an FBM listing for a SKU FBA returned sets `fbm_quantity` on that row;
- an FBM listing for a SKU FBA did **not** return produces an FBM-only row
  — zero FBA quantities, the FBM quantity, `raw.source = "listings_report"`;
- an FBM-only listing with no `asin1` cannot satisfy the table's `NOT NULL`
  ASIN and is counted as a failed row with a reason rather than invented.

**Snapshots are append-only.** Every run inserts its own rows and never
touches an earlier run's; `uq_amazon_inventory_snapshots_run_sku` makes a
duplicate within a run impossible, and a duplicate FBA summary is counted as
failed rather than allowed to trip it. Counts: `rows_seen` = summaries +
listings rows, `rows_created` = snapshots written, `rows_failed` = listings
rows rejected + merge failures, `error_details.fbm_only` = how many SKUs
came only from the report.

The same parsed listings rows are then handed to the listings→product
mapping (§9) **inside the same transaction** as the snapshots — one report
fetch serves both, and a run either lands both or neither. The mapping's
counts are recorded on the run under `error_details.listings_mapping`.

---

## 9. Listings → product mapping

`app/services/amazon_listings.py` is the **first matching code in the
repository**. It populates `marketplace_listings` from the merchant listings
rows and resolves each seller SKU to a product using the CLAUDE.md §5.1
priority chain and nothing else. §5.2 governs every line of it:

- a listing resolves through **one rule at a time**, in a fixed order,
  stopping at the first rule that yields exactly one product; every rule
  tried and what it returned is recorded in `match_evaluations`;
- a rule that returns **more than one** product is ambiguous — the listing
  is queued, no candidate is picked, and no weaker rule is tried;
- **`item-name` is never an input.** It is stored in `raw` for display and
  nothing in the resolver reads it; a test proves two listings with the same
  name and different UPCs resolve to different products, and that a listing
  with only a name is unmatched;
- an **APPROVED** listing is permanent: the mapping runs update its
  `asin`, `listing_status` and `raw` and touch nothing else, even when the
  catalog now says something different.

### The rules, in the order they run

| Step | Rule | Input | Result when exactly one product |
|---|---|---|---|
| 1 | **Priority 4 — `AMAZON_SKU_MAPPING`** | A `product_identifiers` row of type `AMAZON_SKU` whose value is the seller SKU (trimmed, case preserved). This is the catalog source — a future Nineyard `/api/Skus` sync — saying "this SKU is this item". | `mapping_status = APPROVED`, `mapping_method = AMAZON_SKU_MAPPING`, approver = the organization's **system user**, `approved_at = now`; audited as `amazon.listing.mapping_approved`. An open queue item for the listing is resolved `APPROVED` by the system user and audited. |
| 2 | **Priority 1 — `UPC`** | The listing's `product-id` when `product-id-type` is 3 (UPC) or 4 (EAN), normalised by `app/matching/normalize.py` to GTIN-14 with the check digit verified, against `UPC`/`EAN`/`GTIN` identifiers whose own check digit did not fail. | A **suggestion, not an approval**: `mapping_status = PENDING` with `product_id` = the candidate and `mapping_method = UPC`, plus a queue item with reason `SUGGESTION_ONLY`. An Amazon-SKU link inferred from a UPC still needs a person (§5.1 row 5). |
| — | Otherwise | | `mapping_status = UNMAPPED`, `product_id` null, and a queue item: `AMBIGUOUS_MATCH` when a rule returned several products, `CONFLICTING_IDENTIFIER` when priority 4 and priority 1 each returned one product and they differ, `NO_MATCH` when nothing fired. |

Priority 4 is evaluated first because it is the direct evidence; priority 1
is evaluated as well, always, so the attribution is complete. A bad check
digit never reaches the catalog — the rule is recorded as `skipped` with the
reason. `REJECTED` and `SUPERSEDED` listings are left alone (re-running the
chain would re-queue a rejected suggestion unchanged, AC-8.5); phase 8 owns
what happens next for those.

### What lands in the database

- `marketplace_listings`: one row per `(organization, AMAZON, marketplace_id,
  seller_sku)`, upserted with `asin`, `listing_status` (Active → `ACTIVE`,
  Inactive/Incomplete → `INACTIVE`, else `UNKNOWN`) and `raw` — the eight
  retained columns of the report row.
- `product_mapping_exceptions`: **one open item per listing** (partial unique
  index). A re-run refreshes the open item's reason, candidates and
  evaluations in place rather than adding a second.
  `candidates.products[]` lists every product any rule returned with the rule,
  priority and identifier that produced it; `match_evaluations.rules[]` is the
  full ordered trace.
- `audit_events`: `amazon.listing.mapping_approved` (before/after of the
  listing), `amazon.listing.exception_opened`, and
  `amazon.listing.exception_resolved` — all `SYSTEM`, labelled
  `amazon-listings-mapping`.
- `users`: the per-organization system user `system@prms.internal`, created
  on first use, no password, no roles — it can be named as an approver and
  can never sign in.

### Schema corrections this needed

Migration `3de5c4e5def0` ([database-schema.md](database-schema.md)): a
listing may now exist without a product (with the same two checks
`vendor_products` has), an exception may be about a listing instead of a
vendor, and an `AMAZON_SKU` identifier may exist before its listing does.

### Normalisation

`app/matching/normalize.py` is shared with the vendor import (Track B) and
implements AC-7.6: strip whitespace and separators, digits only, restore the
leading zero on an 11-digit UPC-A, expand UPC-E, verify the GTIN check
digit, and compare in canonical zero-padded GTIN-14. Values differing only in
leading zeros compare equal; a bad check digit is reported and disqualifies
the value from automatic matching; a spreadsheet's `1.23457E+11` is unusable
rather than "close enough".

---

## 10. Scheduling, run recovery, and the CLI

### The scheduler boundary

`app/jobs/runner.py` defines `JobRunner` — `schedule(job_id, func,
interval_minutes | cron)`, `start()`, `shutdown()` — and one implementation,
`APSchedulerRunner`: APScheduler 3.x, an in-process `BackgroundScheduler`,
no broker, no job store. Every job is registered with `max_instances=1`
(a slow run is never overlapped by the next tick), `coalesce=True` (missed
ticks collapse into one run) and `misfire_grace_time=3600`. Tests drive the
jobs through a fake runner and never start a thread.

The scheduler starts in the FastAPI lifespan **only when `AMAZON_ENABLED` is
true and never under `APP_ENV=test`**, and it is in-process: it belongs in
exactly one process. Several API replicas means moving it to the worker.

### The jobs

`app/jobs/amazon.py` — three plain callables, each opening its own session
through `session_scope()`, running as `SCHEDULED`, and catching everything so
one failure never stops the scheduler (the run row already carries the
detail):

| Job | Default interval | What it does |
|---|---|---|
| `amazon.orders` | 24 h | `AMAZON_ORDERS_WINDOW_DAYS` (35) split into ≤30-day report requests, oldest first — `[now-35d, now-5d]` then `[now-5d, now]`. Overlap between runs is harmless: the upsert only ever moves a line forward. |
| `amazon.inventory` | 60 min | FBA summaries + listings report → snapshots and mapping (§8, §9). |
| `amazon.listings` | 24 h, registered after inventory | The listings report alone → mapping (`run_listings_sync`, job type `LISTINGS_REPORT`). Exists so catalog changes are re-evaluated daily without an inventory pull. |

Before each job opens its run it **recovers stale runs**: any `RUNNING` row
of its own job type whose `started_at` is older than
`AMAZON_RUN_TIMEOUT_MINUTES` (120) is closed as `FAILED` with
`error_message = "timed out"`, audited as `amazon.run.timed_out`. A process
that died between claiming the slot and its `finally` would otherwise block
that job type forever.

Credentials are one set per deployment, so the jobs need to know which
tenant to write into: `AMAZON_ORGANIZATION_SLUG` names it, and may be
omitted while exactly one active organization exists.

### The CLI

`python -m app.cli.amazon_poc` — same shape as `nineyard_probe`: argparse,
`configure_logging`, fixed exit codes (`0` ok, `1` a run `FAILED`, `2`
configuration or credentials, `3` unexpected).

| Subcommand | Does |
|---|---|
| `auth` | LWA exchange through `AmazonClient.check_credentials()`; prints the token's expiry and **nothing else** — the token is reduced to a hash prefix before it leaves the client. |
| `sync-orders [--days N]` | The orders job's windows, as `MANUAL` runs. |
| `sync-inventory` / `sync-listings` | One run each. |
| `run` | Orders, inventory, listings, in order; stops at the first failure. |
| `velocity [--level sku\|product] [--top N]` | The aligned table from `app/services/amazon_velocity.py`. |

Each sync command recovers stale runs first, prints one line per run with
status and counts, and exits `1` if any run ended `FAILED`.

### Velocity

`app/services/amazon_velocity.py` combines, per seller SKU: units ordered in
the trailing 7 / 14 / 30 days from `amazon_order_lines` (cancelled lines
excluded), the latest `amazon_inventory_snapshots` row (fulfillable, FBM,
inbound = working + shipped + receiving), and the listing's mapping with the
product's Catalog Item Number and UPC. Days of supply is `(fulfillable +
FBM) / (units_14 / 14)`, and is blank rather than infinite when nothing
sold. `--level product` merges a product's SKUs into one row. No
replenishment quantity is computed anywhere (ADR 0011).

The same result is available to authenticated read-only users over HTTP:

```http
GET /api/v1/amazon/velocity?level=product&top=25
Authorization: Bearer <application token>
```

`level` is `sku` (default) or `product`; `top` is optional and limited to
1–1000. The response includes the 7/14/30-day unit totals, average daily
14-day velocity, fulfillable/FBM/inbound quantities, on-hand units, days of
supply, and the current mapping state. It reads only the PostgreSQL copy and
does not call or write to Amazon.

---

## 11. What is still unproven

The client has run only against fakes. Until B8 is answered and the probe
step runs it against the real account, these remain assumptions:

- that the seller's SP-API application has the **Inventory and Order
  Tracking** and **Product Listing** roles, without which the inventory and
  listings calls return 403;
- the actual latency of report generation for this seller, which sets a
  realistic `timeout_s`;
- the real throttling behaviour under a first 30-day backfill of order
  lines, and whether `Retry-After` is sent;
- the charset Amazon declares for this seller's flat-file reports (the
  library decodes with a fallback of ISO-8859-1 if none is declared);
- the exact column set and timestamp format of the live orders report — the
  parser's fixture follows Amazon's documentation, and the required twelve
  columns are checked on every run, but the report has not been pulled;
- the same for the merchant listings report, and whether its
  `fulfillment-channel` values on this account are exactly `DEFAULT` and
  `AMAZON_NA`;
- the real page count and latency of FBA Inventory for this account, which
  is what `AMAZON_INVENTORY_PAGE_DELAY_S` should be tuned against;
- how long a 30-day orders report takes to generate for this seller, which
  sets whether the 24-hour orders interval and the 2-hour run timeout are
  right.

Each is recorded when observed, in the same way
[nineyard-field-mapping.md](nineyard-field-mapping.md) records Nineyard
findings.
