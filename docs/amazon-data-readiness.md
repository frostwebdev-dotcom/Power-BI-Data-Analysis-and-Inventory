# Amazon input readiness

ADR [0029](decisions/0029-amazon-data-readiness.md) adds a read-only evidence
check before the existing explicit-input replenishment calculator is connected
to ingested Amazon data. It does not calculate a purchase quantity, save policy,
approve mappings, call Amazon, or make purchases.

## API

`POST /api/v1/amazon/replenishment-readiness` accepts any authenticated staff role,
including Viewer. The product and all database reads are scoped to the actor's
organization. A foreign or missing product returns the same 404; malformed
requests return 422 and callers without a staff role receive 403.

The complete synthetic request is in
[amazon-readiness-request.json](../backend/examples/amazon-readiness-request.json).
Replace fixture UUIDs with existing scoped product/listing UUIDs. Every choice
is explicit; fixture thresholds and confirmation text are not client policy.
No live request or baseline sync was executed as part of this increment.

| Field | Meaning |
|---|---|
| product_id | Internal product UUID; never Catalog Item Number or description |
| marketplace_id / sales_channel | First adapter supports ATVPDKIKX0DER / Amazon.com only |
| demand_basis | FULLY_SHIPPED_ONLY, explicitly selected |
| velocity_window_days | Integer 7, 14 or 30; measured by purchase date |
| orders_max_age_seconds | Maximum lag from baseline data cutoff to evaluation |
| inventory_max_age_seconds | Maximum age of inventory run and each snapshot |
| listing_units | Every known product listing, positive integer base-unit conversion and confirmation reference, or explicit null for unknown |
| stock_pools | Named FBA/FBM pool, member listing UUIDs, one stock-source member and distinct-stock confirmation reference |

The product must be active. Every known listing must have an active approved
mapping, active listing status, recorded approval evidence, matching order/stock
ASINs and confirmed conversion. Product pack_size is never inferred as a listing
conversion. Each listing belongs to at most one pool per fulfillment channel.
Incomplete, foreign or unknown configurations block readiness. Unmapped SKUs
not yet associated with this product are outside the response's explicitly
named known_product_listings scope; readiness is not catalog completeness proof.

## Sales evidence and baseline mode

The default scheduler and CLI continue requesting the last-update report.
That report alone cannot prove complete purchase-window demand or zero sales.
A new opt-in CLI flag requests Amazon's order-date report instead:

```powershell
# Run only when deliberately performing an authorized live ingestion.
# This example documents the option; it is not executed by readiness.
.\.venv\Scripts\python.exe -m app.cli.amazon_poc sync-orders --days 30 --order-date-baseline
```

Run this from the repository root in the documented Amazon CLI environment.
The service also accepts report_basis=ORDER_DATE; LAST_UPDATE stays the default.
The real report type/basis/version is recorded in run metadata. Persisted lines
carry a safe source-run marker and aggregation flag. Identical observations
refresh evidence without changing business values; ignored older or conflicting
observations retain their previous marker and cannot certify a new source.
The parser's original raw column whitelist and legacy velocity behavior remain.

Readiness needs continuous clean order-date coverage over [start, cutoff),
ending at the greatest valid baseline end. Adjacent intervals cover the window;
a one-second gap blocks. It displays both timestamps and evaluation time, so
cutoff lag is visible. A baseline cannot claim data past its own completion.
Lines omitted by a newer baseline covering their purchase date stay uncertain.
Update observations may refresh lines after a baseline, but their source,
window and timestamps must agree. Legacy or aggregated order/SKU lines without
item-level certainty block affected listing demand. A missing, failed, partial,
running, future or tied latest run blocks; a clean older run is not substituted.

Demand counts only order-status Shipped plus item-status Shipped. Known pending,
canceled, unshipped and partially shipped orders are excluded under this explicit
basis. Unknown status/channel/ASIN/provenance blocks rather than becoming zero.
Amazon fulfillment maps to FBA and Merchant to FBM. Other known sales channels
are excluded; missing sales channel is uncertain. No buyer fields or order IDs
are returned. At most 200 listings, 500 runs per job and 50,000 order lines are
read; exceeding any limit produces a blocker rather than a partial ready result.

## Stock and units

Sales are converted from listing units to base units exactly once. A stock pool
uses only its declared stock-source listing. Shared merchant aliases are not
summed: for example, an authoritative 100 single units plus an alias advertising
16 six-packs still yields 100 units when the single listing is the chosen source.
The request reference explicitly asserts the pool's physical independence;
this assertion is not persisted or independently verified by Amazon.

FBA aliases must share nonblank FNSKU and condition, the same conversion,
quantities and capture time. The same FNSKU/condition cannot appear in two
allegedly independent FBA pools. Distinct merchant pools require explicit client
confirmation because listing stock does not establish warehouse identity.
FBA and FBM remain separate; there is no summed product position in this API.

Only snapshots from the latest scoped inventory run are used. Missing SKU,
invalid/future/stale capture time, source mismatch or ASIN conflict blocks.
Retained raw FBA values distinguish explicit zero from quantities previously
coerced to zero for storage. FBM-only rows' artificial FBA zeros are not stock.
Future merged FBA/FBM snapshots retain their merchant listing quantity evidence.

Observed inbound is reported separately from usable inbound. Explicit zero FBA
inbound is known zero. Positive inbound has no confirmed arrival dates in the
current schema, so usable inbound stays null and readiness blocks. Merchant
inbound is not ingested and also stays null. Missing fields are never zero.

## Result and remaining work

The response echoes its validated request, rule version, UTC evaluation/window,
source run and snapshot UUIDs, listing approval evidence, normalized pool
observations and stable blocking codes. Status is DATA_AVAILABLE only when all
required evidence is available; otherwise BLOCKED. A pool's
quantity_input_ready is an evidence diagnostic, not a purchasing authorization.
Every response has readiness_only=true, purchasing_eligible=false and
confirmations_persisted=false. Nothing is written or audited by this read.

This increment has no policy editor or recommendation persistence and does not
connect financial reports to purchases. It still needs confirmed client
freshness/pack/pool rules, inbound arrival evidence (including merchant inbound),
persisted reviewed policy, integration and the three-real-SKU walkthrough.
The October financial report still needs year/timezone and coverage/cost review.
Gmail remains paused. Nothing is merged or deployed by this increment.

Unit and PostgreSQL tests cover strict assumptions, exact conversions, unknown
versus zero, shared pools, baseline gaps/omissions, ambiguous sources, limits,
roles/isolation and absence of writes. Ingestion tests verify actual baseline
report selection, equal replay and ignored conflicting source markers. Exact
source CI evidence is recorded in draft PR #22.
