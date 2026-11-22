# 0029. Read-only Amazon data readiness and stock-pool normalization

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after financial source revisions
- **Supersedes / Superseded by:** Extends ADR 0022; leaves legacy velocity unchanged

## Decision

Add a tenant-scoped POST /amazon/replenishment-readiness that reads existing
PostgreSQL ingestion only. Evaluate known product listings against explicit
7/14/30-day demand, freshness limits, listing-to-base-unit conversions and named
FBA/FBM stock pools. Return normalized observations, source run/snapshot UUIDs,
window timestamps, mapping evidence and blocking reasons. Never calculate or
persist a purchase recommendation, mutate mappings, or call Amazon from this
endpoint. All output remains purchasing_eligible false. Request confirmations
are explicit preview assumptions, not a saved or purchasing-manager-approved policy.

The request contains no purchasing defaults. Discover every known listing for
the scoped product/marketplace and reject incomplete/foreign pool definitions.
Require an active product and approved active listings, explicit positive pack
conversions with confirmation references, and explicit distinct-stock references.
No description-based matching. Snapshot/order ASIN conflicts block use.
The first adapter explicitly supports the US marketplace/Amazon.com channel;
other marketplaces are rejected rather than inferred from a report's account ID.

Count demand per order/SKU once and convert sold listing units to base units.
Require an explicitly chosen shipped-only basis, known fulfillment channels,
and source provenance. A pool uses one declared stock-source listing; FBM
alias quantities are never summed. An FBA alias pool must share one nonblank
FNSKU/condition/conversion and consistent quantities; one FNSKU cannot be split
across allegedly distinct pools. Missing fields, coerced legacy zeros, missing
SKUs in the latest run, ambiguous tied runs, partial/failed/running runs and
future/stale timestamps block rather than falling back to older SKU positions.

Preserve raw quantity presence: use the retained FBA values, and retain the
matching merchant listing evidence on merged future snapshots. FBM-only rows'
artificial FBA zeros are not physical FBA stock. Positive inbound has no arrival
dates in the current schema and cannot be credited to a calculation; unknown
inbound remains unknown, including merchant inbound not ingested here.

## Purchase-window provenance

Amazon documents separate order-date and last-update reports. The current
last-update scheduler does not establish a purchase-date baseline by itself.
Add an opt-in sync-orders --order-date-baseline CLI mode and service keyword,
leaving scheduled/default report requests unchanged. No live command is run in
this increment. Persist run evidence version and actual report basis in existing
run JSON, plus a safe internal line marker identifying its source run and any
aggregated order/SKU case. Equal observations refresh only this retained raw
evidence when every stored business value agrees; ignored older/conflicting
observations cannot certify a new source. Existing parser/upsert quantities and
legacy velocity behavior remain unchanged. Parser raw retains its original
whitelist; the persistence marker contains no buyer fields or order IDs.

Readiness requires continuous clean order-date coverage for the selected window,
ending at the latest baseline's data cutoff (display its lag explicitly). An
update-only/legacy run is not proof of complete demand or zero sales. Aggregated
order/SKU lines without item-level certainty are blocked for affected SKUs.
Latest line/run evidence must be compatible with the evaluation time. No buyer
PII or Amazon order IDs are returned. Bound run/listing/order reads; exceeding a
bound is an explicit blocker, never silent truncation.

Primary reference: [Amazon Order Reports](https://developer-docs.amazon/sp-api/docs/report-type-values-order),
order-tracking report definitions checked on 17 November 2026.

## Acceptance and boundaries

Test strict request shapes, exact integer conversion, shared FBM/FBA deduplication,
missing/raw-null quantities, explicit zero, inbound timing, complete window
coverage and gaps, legacy/update-only/ambiguous order evidence, canceled/pending
exclusion, unknown channels, stale/future/partial/tied sources, scope/roles and
read-only behavior. Test baseline mode and evidence through real ingestion fakes.
Run full backend/PostgreSQL/migration/API and existing browser checks.

No frontend policy editor, persisted purchasing policy/recommendation, financial
integration, migration, deployment, Amazon writes, live sync or Gmail activation
is included. Real pack/pool/freshness policy and three-SKU acceptance still need
client confirmation. Financial October year/timezone remain unconfirmed.
