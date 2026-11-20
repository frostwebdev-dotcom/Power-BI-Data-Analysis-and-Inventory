# Vendor comparison and saved replenishment review

ADR 0036 adds read-only comparison and discovery over the existing policy,
source-readiness and saved-evaluation services. All staff read roles can use
these screens. No comparison selects a winning offer or authorizes purchasing.

| Endpoint | Purpose |
| --- | --- |
| POST /api/v1/vendor-comparisons/preview | Observe 1–10 explicitly selected current policy approvals for one product |
| GET /api/v1/replenishment-review | Find the latest saved evaluation per policy with bounded search, filters and pagination |
| GET /api/v1/replenishment-review/summary | Count latest saved records by current approval relation and historical result |

## Compare selected offers

Choose a product, then explicitly select approved policies. Selections retain
their expected approval UUID across pages. Reload clears them. An optional
current financial report and its current binding revision are shared across
the selected offers; no report is selected by default. A changed approval or
binding requires refreshed choices. Choice changes clear captured results and
late responses from old selections cannot replace them.

The server validates the complete scoped selection, acquires policy locks in
deterministic order and reuses recommendation previews with one observation
time. A foreign, mismatched or stale choice fails the complete request. It does
not write an evaluation or audit event, call external providers or reserve stock.
Each offer retains its full versioned preview and source evidence. To record
an evaluation, follow its link and run the existing fresh review workflow.

Default ordering is vendor name. Explicit price ordering requires at least two
verified current v4 supplier prices, identical approved listing/base-unit
projections and common stated tax treatment. Unknown or legacy prices remain
unknown. With two or more comparable prices, missing-price offers follow the
known subset and the response is labelled partial. Unit/tax disagreement disables
price ordering and returns the vendor-name fallback with reasons.

Base price is reported USD price divided by approved base units per price unit.
Sorting compares exact rational values, independently of rounded display values.
The response retains numerator/denominator strings, a 28-significant-digit
Decimal display and an explicit rounding flag. The browser performs no money
arithmetic. A cheaper offer can still be blocked by availability or minimums.
Reported goods prices remain separate from landed costs and historical profit.

## Review saved evidence

The queue chooses one historical record per organization/policy by recorded UTC
time descending, with UUID as a deterministic presentation tie-break. It then
applies literal substring search, product/vendor/policy, saved status, approval
relation and known-positive-shortfall filters. Page size is bounded at 100.
Current catalogue labels assist search; saved quantities, reasons and provenance
remain immutable. Full details are linked from each row.

The default queue includes records whose saved approval is still the current
non-draft decision. A later draft preserves approval. Replacement or revocation
puts the record in the changed-approval group. This check does not revalidate
today's mappings, stock, prices, source freshness or shipment arrivals.

Positive shortfall means any saved pool has a known positive net shortfall;
missing calculations remain unknown. Historical stockout risk requires complete
saved quantity inputs and compares exact integer stock plus usable inbound
against demand over lead plus safety days. It describes the saved observation,
not current urgency. Dashboard cards count records, not pooled quantities, money
or orders, and link to the corresponding filtered queue. Milestone 1 dashboard
API behavior is unchanged.

No schema migration or purchasing default is introduced. All outputs remain
historical or unsaved observations with purchasing eligibility false. Real
commercial/source confirmations, landed costs, reporting context and the
three-real-SKU acceptance walkthrough remain open. Source-specific local,
PostgreSQL/browser CI and desktop/mobile layout evidence are recorded in draft
PR #22. No client policy/file, live sync, purchase, merge or deployment is included.

Use the [Milestone 2 acceptance walkthrough](milestone-2-acceptance.md) for the
real three-SKU review. Its coverage matrix preserves the difference between this
historical dashboard and current urgency, and records unsupported inbound cases
without substituting confirmed zero.
