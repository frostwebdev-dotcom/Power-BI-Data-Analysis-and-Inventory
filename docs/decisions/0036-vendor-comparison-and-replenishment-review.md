# 0036. Vendor comparison and saved replenishment review

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Extends:** ADRs 0030–0035

## Scope and plan

Add an authenticated, read-only vendor-comparison preview for one explicitly
selected product and 1–10 explicitly selected policy/current-approval pairs.
No vendor/policy/financial report is selected automatically. Preflight the entire
scoped selection, acquire policy locks in deterministic identity order, and reuse
existing server evaluations at one observation time. A foreign/missing/mismatched
selection fails the whole request; changed approvals or financial binding/source
heads require a fresh selection. No evaluation, policy, audit or order is written.
An optional explicitly selected financial report is passed unchanged to all
selected offers and remains historical context only.

Preserve full per-offer snapshots, current catalogue/vendor labels and source
references. Current reported/base-unit price is available only for v4 verified
price/supplier evidence with no policy-identity blocks. Never substitute a
historical cost. Base-price comparison requires identical complete approved
Amazon listing-to-base-unit projections and matching known tax-inclusion basis,
USD and at least two eligible prices. Missing prices remain unavailable; a
comparison of only the verified subset is explicitly partial. Differences in
unit/tax basis disable price ordering for the whole selection. Demand, coverage,
stock pools and commercial constraints can differ and remain visible; a goods
price comparison does not imply equivalent replenishment needs or landed costs.

Use exact Fraction arithmetic derived from Decimal price and the confirmed
factor for ordering; no rounded quotient determines order or ties. Return exact
numerator/denominator strings plus a labelled rounded display quotient. Default
display order is vendor name/code/SKU/UUID; a user can request base-price ordering
when comparable, with unavailable prices last and explicit fallback otherwise.
No preferred vendor, purchase winner, stock reservation, cross-product allocation
or budget optimization is produced. Saving an offer still uses a separate fresh
policy preview/reason/acknowledgement workflow.

## Historical review queues and dashboard

Add a separate read-only replenishment-review list and summary. SQL selects one
latest saved record per organization/policy by recorded_at descending then UUID
descending (UUID is only a deterministic presentation tie-break). It does not
recalculate every policy or certify source freshness. Join only scoped catalogue,
vendor and current non-draft policy-decision identities. Flag changed/revoked
approval; a newer unapproved draft preserves the active approval.

Support bounded pagination and literal escaped catalogue/product/vendor/SKU
search, product/vendor/policy/status/approval filters and known positive-shortfall
filter. Counts refer to saved records with current policy approval, with changed
approval counted separately. Never aggregate independent stock, quantities,
financial report versions or money. Show saved source cutoff, record time,
reasons, quantity/financial/commercial context and historical stockout risk only
when complete saved inputs prove it using exact integer lead+safety comparisons.
Unknown evidence stays unknown; historical risk is never called current urgency.

Integrate small historical review cards into the existing dashboard without
changing its Milestone 1 API. Add navigation, product lookup, selected-offer
comparison, complete evidence inspection and links to fresh evaluation/history.
Changing selection clears comparison/detail; stale/out-of-order responses cannot
replace a newer selection. All existing staff read roles can use these views;
recording remains governed by existing write roles. Keep decimal-string display
and unsafe-integer safeguards. No schema changes or new dependencies are needed.

## Acceptance and boundaries

Test exact fractions, rounded ties, unit/tax mismatches, unknown/zero prices,
partial comparisons, deterministic ordering/locks, explicit selection, scope,
roles, stale approvals/financial heads, no writes and complete source evidence.
Cover latest-record selection, preserved approvals across drafts, revoked/new
approvals, historical risk/unknowns, escaped search, filters, pagination, counts
and no cross-tenant/raw-source leakage. Exercise real PostgreSQL/API paths and
UI selection/filter/stale/loading/empty/role flows; inspect desktop/mobile
comparison, review queue and dashboard artifacts. Run local unit/type/lint/build
and full source-specific PostgreSQL/migration/browser/Windows CI on the draft.

No client file/policy/defaults, source sync, merge, deployment, purchase or client
message is included. Client confirmations and three-real-SKU operational
acceptance remain open. Gmail stays paused and M1 acceptance remains open.
