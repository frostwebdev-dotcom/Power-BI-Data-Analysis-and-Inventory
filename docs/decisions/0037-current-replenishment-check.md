# 0037. On-demand current replenishment checks

- **Status:** Accepted for this Milestone 2 increment
- **Date:** 2026-11-18
- **Extends:** ADRs 0029–0036

## Plan

Add an authenticated current-replenishment page and two additive read-only APIs:
bounded search of organization-scoped current approved policy choices, and a
fresh preview for 1–10 explicitly selected policy/expected-approval UUID pairs.
Choices may span products, but never imply an organization-wide scan. Preserve
the full selection visibly across candidate pages/filters. No policy or report
is chosen automatically. No historical financial report is selected by this
quantity/supplier check. No scheduler, provider request, write, migration,
dependency, client default, stock reservation or purchase is introduced.

Preflight the complete scoped selection, lock policy streams in deterministic
product/vendor-product/marketplace order and reuse existing recommendation
previews at one timezone-aware observation time. A missing/foreign choice or
changed expected approval fails the whole request; no partial result escapes.
Preserve each complete versioned preview and evidence digest for inspection.
Recording still requires the existing separate fresh review workflow.

## Independent signals

Compute urgency from the current preview's Amazon readiness pools and approved
quantity policy, independently of supplier-source readiness. Supplier faults
must not erase proven Amazon stockout risk. Policy identity/definition blocks,
incomplete Amazon readiness, missing pools or unknown inputs produce UNKNOWN;
do not fall back to saved evaluations. Reuse the exact quantity calculator with
Amazon-only input flags to obtain required units, net shortfall and uncapped
arithmetic. No supplier order quantity is inferred by this diagnostic.

For complete inputs: URGENT means positive shortfall and positive demand with
(on-hand + usable inbound) × window <= sold × (lead + safety). Otherwise a
positive shortfall is REPLENISH, and zero shortfall is NO_REORDER. Use exact
integers, including at the boundary; zero demand has unknown days of supply.
Return the exact rational supply-days numerator/denominator as strings, not
rounded ranking values. A policy's signal is UNKNOWN if any required pool is
unknown; otherwise URGENT if any pool is urgent, then REPLENISH, then NO_REORDER.

Opportunity is separate: REVIEW_OPPORTUNITY requires a known positive shortfall,
non-blocked evaluation, verified in-stock finite supplier evidence and v4
positive combined allocation with assessed current goods-price/units, reviewed
terms and met scoped minimums. Unknown/legacy/incomplete inputs are UNCONFIRMED;
known supplier unavailability, zero stock or unmet constraints are UNAVAILABLE.
No shortfall is NO_SHORTFALL. This is a review candidate, not landed-cost/profit
verification or buying permission. Partial finite allocation may still be a
review candidate when its assessed minimums pass; its shortfall stays visible.

## UI and verification

Show per-policy signals, independent counts for the explicitly checked set,
per-pool inputs/arithmetic/rational supply days, UTC-backed observation/cutoff,
supplier/commercial status and full evidence. Default stable urgency grouping
is presentation only; never rank products by unknown costs or combine their
stock/quantities/money. Display filters apply only to captured selected results.
Choice changes clear results; stale/out-of-order responses cannot replace a new
selection. All staff read roles can inspect; there is no save control here.
Add dashboard/navigation links without changing the existing historical API.

Test exact threshold/zero/large integers, unknown inputs and supplier-independent
urgency, opportunity qualification, bounds and forged request fields. Exercise
real PostgreSQL/API scoped choices, literal search/pagination, approval changes,
fresh source rechecks/no historical fallback, no writes/provider calls and roles.
Cover browser selection, filters, stale/late responses, evidence and desktop/mobile
layouts. Run appropriate local unit/type/lint/build checks and full draft CI.

Positive inbound arrival timing and FBM inbound support remain separate work.
Client reporting/purchasing confirmations, real three-SKU acceptance, Gmail and
Milestone 1 acceptance retain their pending/paused status. Do not merge or deploy.
