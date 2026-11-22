# 0034. Historical financial context and staff recommendation review

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Extends:** ADRs 0025–0028 and 0032–0033

## Decision and scope

Add an explicit optional financial report/binding selection to evaluation
preview/record requests. No report is chosen automatically. Use the current,
organization-scoped selected source and exact current binding revision (null
means original bindings). Replaced reports or changed binding heads require
another selection/preview. An unchanged legacy request retains its original
fingerprint and v1/v2 snapshot behavior.

Supplier/Amazon quantity calculations remain unchanged. A selection produces a
v3 immutable snapshot with historical reporting period/store/currency, original
file/profile digests, binding version, exact SKU rows, recorded/current mapping
evidence, source controls, warnings and Decimal money. Revalidate approved exact
SKU/product/ASIN and approval identities. Missing rows, costs or uncertain links
remain unknown; unallocated amounts are never distributed to a product.

Totals are shown only for a complete set of included known product listings.
Retain per-row source amounts and source-computed margin/ROI, but never convert
them into current unit costs, a forecast or profit on the suggested quantity.
Different financial/demand windows are visible review context, not prorated.
Report year/timezone, advertising coverage and cost warnings stay explicit.
All positive evaluations remain REVIEW_REQUIRED; all purchasing markers stay
false. Financial context does not resolve supplier commercial/allocation checks.

Acquire the existing financial marketplace lock while checking current source
and binding state. Snapshot and audit still commit atomically. New nullable UUID
foreign keys retain report/binding references; database guards enforce scoped
current references and v3 context identity. v1/v2 rows/digests are preserved.
Downgrade refuses to discard v3 history. Exact successful retries return their
original snapshot, including after a source/report changes.

## Staff screen

Add authenticated Recommendations navigation with product/policy selection,
explicit optional report selection, complete preview and reason/acknowledgement
before recording. Show quantity breakdowns, source dates, unknowns, blockers,
historical financial rows/controls and commercial/allocation review reasons.
All staff may read/preview; only operators/managers/admins record. History has
bounded product/policy/status filtering and immutable detail views.

Changing policy, approval, report or binding clears preview/acknowledgement.
Stale saves require another preview. An unconfirmed network save retains the
same request UUID/body for exact retry. Never render an unsafe JS integer as an
exact quantity; the screen refuses recording when quantities cannot be displayed
exactly. Money displays as decimal strings without browser arithmetic.

## Acceptance and boundaries

Cover explicit selection, foreign/replaced/stale sources, immutable binding
revisions, mapping/ASIN/approval changes, unknown/zero costs, missing/duplicate
rows, controls/coverage/unallocated warnings, exact totals and legacy replay.
Test scoped APIs, retries/audit rollback, insert/downgrade guards, role-aware
preview/save/history, context changes, ambiguous-save retry and responsive UI.
Run full PostgreSQL/migration/backend/frontend/browser/Windows CI and inspect
desktop/mobile artifacts on the published source.

No client file upload, financial-context confirmation, policy seeding, live sync,
purchase, merge or deployment is included. Gmail remains paused and M1 acceptance
remains open. Real-file context, commercial/allocation integration, vendor
comparison/dashboard work and three-real-SKU acceptance remain follow-ups.
