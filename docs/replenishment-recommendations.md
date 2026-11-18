# Saved replenishment evaluations

The original v1 contract below remains supported. Policies with explicitly
approved [supplier settings](supplier-readiness.md) now use v2 snapshots with
source evidence and vendor caps. History preserves both versions and adds the
REVIEW_REQUIRED status; positive results remain for review, not purchasing.

Milestone 2 draft-branch APIs connect the current manager-approved purchasing
policy to ingested Amazon readiness and the exact quantity-preview-v1 formula.
They retain historical evidence; they do not reserve inventory or place orders.
The staff recommendation screen and supplier/profitability adapters are follow-ups.

## Preview and record

All authenticated staff can preview with
`POST /api/v1/purchasing-policies/{policy_id}/recommendation-preview`:

```json
{"expected_approved_revision_id": "<current approval UUID>"}
```

Read the full server-returned snapshot and its evidence_sha256. Operators,
purchasing managers and admins can then record with
`POST /api/v1/purchasing-policies/{policy_id}/recommendations`:

```json
{
  "request_id": "<new client-generated UUID>",
  "expected_approved_revision_id": "<same current approval UUID>",
  "expected_evidence_sha256": "<preview evidence digest>",
  "reason": "Reviewed this calculation and its unresolved inputs.",
  "acknowledge_review": true
}
```

Callers cannot override quantities, mapping flags, policy or source freshness.
A newer draft preserves the current approval. Replacement/revocation requires
another preview against the new state. Changed active mapping/approval identity
blocks calculation until the policy is reviewed again. Price, description and
last-seen timestamps are not mapping approval evidence.

Recording independently reruns readiness and the formula under the policy stream
lock and shared source-row locks. Source/value/mapping changes and newly stale
inputs reject the old evidence digest with 409. A fresh blocked preview can be
recorded to document why no usable suggestion exists. No external source is called.

Retry an uncertain successful save with the same complete request and actor.
It returns the original UUID and observation without repeating the calculation
or audit, even after source/approval changes. Reusing a request UUID for different
content or a different actor returns 409. A new observation needs a new request
UUID and preview.

## Interpreting quantities

Each physical pool has its own normalized sales, on-hand and usable inbound,
full calculator input and exact result. Shared listing stock is read once from
the approved authoritative stock-source listing. Pool quantities are not summed
into a vendor purchase and supplier stock is not allocated across pools.

The formula uses approved target + lead + safety days, exact integer ceilings,
MOQ and order multiples. order_quantity_before_vendor_cap is arithmetic before
supplier validation. Supplier availability/quantity and profitability are marked
NOT_INTEGRATED; supplier availability is UNKNOWN, never inferred as in stock.
Positive shortfalls have a blocked order suggestion. A clean zero shortfall may
return NO_REORDER; it is still a historical observation, not a purchasing grant.

Readiness remains conservative: missing/old/failed runs, missing latest SKU,
unproven purchase-window coverage, incomplete mapping/unit/pool evidence,
positive inbound without arrival evidence and merchant inbound block inputs.
Unknown remains distinct from confirmed zero. See
[Amazon readiness](amazon-data-readiness.md).

## History and reproducibility

- `GET /api/v1/replenishment-recommendations` supports bounded page/page_size
  (maximum 100) and optional policy_id, product_id and status filters.
- `GET /api/v1/replenishment-recommendations/{identifier}` returns one immutable
  snapshot. Missing and foreign records both return 404; foreign filters expose
  no records.

Snapshots retain full approved definition/review evidence, current identity,
run/inventory-snapshot UUIDs, source windows/timestamps, normalized listing/pool
quantities, readiness issues and calculator input/result. Replay arithmetic with
calculate_replenishment(pool.inputs). This is a bounded source observation, not
synchronized live stock or a second raw-order archive. Amazon order identifiers
and raw orders are not copied.

evidence_sha256 includes source/calculation evidence but excludes the observation
clock; clock-derived window bounds are excluded only when no purchase baseline
exists. Freshness issues remain included. snapshot_sha256 includes the complete
saved JSON and observation time. Digests use sorted-key compact JSON and SHA-256.
Decimal ratios serialize as strings.

Migration a61117202605 adds one UUID/tenant-scoped append-only table. Snapshot and
audit commit atomically. Database guards enforce current approval ownership,
snapshot identity/content references, active actor, chronology and non-purchasing
markers. Updates/deletes are refused; downgrade refuses to discard evaluations.
Every read is historical and purchasing_eligible is always false.

No client financial file or policy is seeded, uploaded or calculated live.
Client policy/source confirmation, supplier constraints, financial coverage
and three-real-SKU acceptance remain required before operational use.

## Historical financial context and staff review (ADR 0034)

The authenticated **Recommendations** page supports explicit approved-policy
selection, preview, reason/acknowledgement, recording, filtered history and
immutable details. All staff can preview/read; operators, purchasing managers
and administrators can record. Changing policy/report/binding choices clears
review state. Conflicts require a new preview; an uncertain network save retains
its exact request UUID and body for retry. Quantities outside the browser's exact
integer display range are labelled unavailable and cannot be recorded there.

Preview/record requests optionally accept:

```json
"financial": {
  "import_id": "UUID of the explicitly selected current financial report",
  "expected_binding_revision_id": null
}
```

Null selects original bindings only when no binding revision exists; otherwise
provide the exact current binding UUID. No report is chosen automatically.
Replaced/foreign reports and changed binding heads are refused. Legacy requests
without this selection retain their v1/v2 shapes and retry fingerprints.

New v3 snapshots preserve the report's period, store, USD currency, raw/profile
digests, profile, binding version, exact SKU rows, recorded/current mapping
identity, source controls and warnings. Missing/duplicate/unverified links and
incomplete amounts remain unknown. Only a complete known product-listing set
receives totals; Unitemized amounts are never distributed. Money and ratios stay
exact decimal strings. The financial and demand windows are displayed separately.
Historical amounts are never treated as current unit costs or multiplied by the
suggested quantity to claim projected profit. Source cost/advertising warnings
remain visible. Commercial constraints and shared-stock allocation remain open.

Migration a61117202607 adds nullable report/binding foreign keys and scoped v3
insert guards, preserves old history, and refuses downgrade over v3 rows.
Snapshot and audit remain atomic; exact successful retries retain the original
observation after report/binding changes. Purchasing eligibility remains false.
