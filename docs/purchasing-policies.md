# Reviewed purchasing policies

ADR [0030](decisions/0030-reviewed-purchasing-policies.md) adds persisted explicit
assumptions and manager review to the Milestone 2 draft branch. It does not
create recommendations, establish current inventory or authorize purchases.
No real client policy has been seeded, approved, merged or deployed.

## Definition and responsibilities

A policy belongs to one organization, internal product UUID, vendor-product UUID
and US marketplace. The first revision UUID is its policy_id. Every subsequent
entry has its own immutable UUID, sequence, predecessor, full definition, hash,
actor, reason and UTC time. Definitions are versioned as purchasing-policy-v1.

Data operators, purchasing managers and administrators may save drafts. Only
purchasing managers and administrators may preview approval/revocation and record
those decisions. All authenticated staff roles, including Viewer, may read.
Foreign and missing policy/linked identity UUIDs produce the same 404.

| Explicit definition | Interpretation |
|---|---|
| quantity | Existing 7/14/30-day window, target, lead and safety days, MOQ and order multiple in base units |
| coverage_policy | target-plus-lead-plus-safety; target excludes the separately added lead and safety days |
| coverage_confirmation_reference | The client/manager reference confirming that coverage interpretation, or null while unknown |
| amazon | Complete readiness request: product/marketplace, shipped-only demand, maximum ages, listing conversions and named stock pools |
| inbound_policy | CONFIRMED_BEFORE_STOCKOUT_ONLY; this states how future arrival evidence must be treated, not that any current shipment meets it |
| vendor_base_units_per_order_unit | Confirmed positive integer conversion from one vendor order unit to the product base unit, or null |
| vendor_unit_confirmation_reference | Evidence reference for vendor units, or null |

The synthetic complete request is
[purchasing-policy-request.json](../backend/examples/purchasing-policy-request.json).
Its UUIDs, thresholds and references are fixtures, not client choices. Replace
all of them deliberately before an operational policy is proposed. Vendor lead
time, Product.pack_size and vendor pack_size are never silently adopted. The
quantity and Amazon windows must agree. Persisted MOQ and multiples are bounded
to one billion base units. Stock freshness bounds and listing factors retain
the readiness request's stricter limits.

A draft can preserve unknown confirmations as null. Approval requires active
product/vendor records, an active approved exact vendor-product mapping with
approval evidence, active approved known Amazon listings with ASIN and approval
evidence, complete listing/pool membership, conversion and distinct-stock
references, and confirmed coverage interpretation. Order multiples must be
compatible with the confirmed vendor order-unit conversion. Unknown identity
is never resolved using descriptions. Approval does not change any mapping.

## API workflow

All paths are under /api/v1 and require the existing authentication.

| Operation | Endpoint | Result |
|---|---|---|
| Save first draft | POST /purchasing-policies | New policy stream and DRAFT revision; body has vendor_product_id, definition, reason |
| List policies | GET /purchasing-policies | Paginated head and approved snapshot; optional product_id filter |
| Inspect policy | GET /purchasing-policies/{policy_id} | Editing head and current approved decision, or null approval |
| Read history | GET /purchasing-policies/{policy_id}/revisions | Full immutable definitions and reviews, newest first, paginated |
| Save next draft | POST /purchasing-policies/{policy_id}/revisions | definition, expected_head_id and reason; product/marketplace/vendor identity cannot change |
| Inspect review | POST /purchasing-policies/{policy_id}/review-preview?action=APPROVE | Current draft, full identity context, definition/context hashes and blocking reasons; no writes |
| Inspect revocation | POST /purchasing-policies/{policy_id}/review-preview?action=REVOKE | Currently approved definition, current head/context and hashes; no writes |
| Record decision | POST /purchasing-policies/{policy_id}/reviews | action, expected_head_id, reviewed_revision_id, definition_sha256, context_sha256, reason, acknowledge_review=true |

List/history default to 25 entries, maximum 100. Reasons are trimmed and require
10–1,000 characters. Extra fields and non-boolean acknowledgements are rejected.
The definition hash uses canonical JSON with deterministic listing/pool/member
order. No update/delete endpoint exists.

Review preview takes short transaction locks on the stream and shared locks on
existing identity records; request completion releases them. Recording reacquires
locks and recomputes the full definition/context. Changed head, target, hashes
or mapping context returns 409. No-op drafts are refused; after revocation, the
same definition can be resubmitted as a fresh draft and reviewed again.
A successful decision retried with the old head returns 409; reload history to
observe the first result. No duplicate review/audit is appended.

Every mutation and audit event commits together. Context snapshots retain scoped
mapping evidence without copying raw source payloads. Existing identity changes
are checked during review; later changes and concurrent new associations still
require the future live readiness check before any recommendation. Policy
approval is historical approval of assumptions, not perpetual mapping validity.

## Approval and history

The editing head is always the latest ledger entry. The latest non-draft decision
determines the approved snapshot. Saving a later draft leaves the preceding
approval in place. Approving the new current draft replaces that selection while
preserving every earlier definition and decision.

Revocation references/copies the currently approved decision, including when a
newer draft exists. It clears approval without deleting that draft from history.
The revocation becomes the editing head; the abandoned/pending draft can still
be found in history. Inactive products/vendors/mappings do not prevent revocation.

Every API response remains purchasing_eligible=false. Policy summaries also
have current_data_verified=false. No inventory or financial data is fetched by
review, no arrival is certified, and no quantity recommendation is calculated.
Before a future recommendation, recheck current mapping, complete sales coverage,
fresh stock, independent pools, units, vendor constraints and arrival timing.

## Persistence and verification

Migration a61117202604 creates one UUID-keyed organization-scoped ledger and two
insert/immutability triggers. PostgreSQL guards enforce identity tenancy,
root uniqueness, sequence and single successor, legal review transitions,
reviewed snapshot/digest copying, context ownership and no updates/deletes.
Application validation owns the complete typed definition. Downgrade refuses
to discard any recorded history; an empty schema roundtrips normally.

Unit tests cover strict explicit contracts, canonical hashing, acknowledgement,
roles and authentication. PostgreSQL tests exercise the complete lifecycle,
unknown/stale refusal, tenant isolation, atomic audit, illegal mutations,
concurrent approvals and protected downgrade. Exact-source CI evidence is kept
in draft PR #22. No live client policy or Amazon baseline is created by tests.

## Staff policy editor

ADR 0031 adds /purchasing-policies to the authenticated navigation. Product
search requires explicit selection. The supplier chooser shows linked vendor
names and exact SKUs; it does not create a mapping. New policy fields start
blank, including demand/coverage/inbound choices, numeric limits and evidence.
Conversions left blank stay null. The listing editor uses the complete scoped
Amazon US set (maximum 200); incomplete lookups block saving. Stock pools have
explicit membership, channel, one authoritative source and distinct-stock
references. No pack_size is copied into conversions.

The summary distinguishes the editing head from current approval. Paginated
history shows full recorded definitions, reasons and local-zone timestamps.
A historical definition can be used as a new draft against the latest head.
Editing holds its original expected head; stale saves preserve unsaved fields
until the operator explicitly closes/reopens the editor. Saved drafts do not
revoke the prior approval.

Manager review displays the complete server-returned definition, readable
mapping evidence and blockers before requiring a reason and acknowledgement.
It submits the server's exact version/content/context references. Rejected or
changed reviews and explicit context changes clear the acknowledgement; unsaved
edits cannot be approved. Viewer and operator controls follow their server roles.
All summaries retain purchasing_eligible=false and current_data_verified=false.

The product/vendor-product lookup is bounded, read-only and tenant-scoped;
optional marketplace filters on the existing listing lookup preserve unfiltered
history behavior. Policy summaries add current catalogue/vendor identity labels;
those display labels are not immutable historical approval evidence. No new
migration is added by the editor increment. Browser tests include mocked manager
flows, a real-API synthetic draft/revision, and desktop/mobile screenshot evidence.

[Saved replenishment evaluations](replenishment-recommendations.md) now consume
the current approval plus independently checked Amazon source data. Later drafts
do not replace approval; changed approval identity blocks evaluation. Supplier
availability/quantity and profitability integration remain follow-ups.
Client values, inbound arrival evidence and three-real-SKU acceptance remain
required. October financial year/timezone and coverage/cost review are still
unconfirmed; Gmail remains paused.
