# 0035. Commercial checks and shared supplier stock

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Extends:** ADRs 0030–0034

## Plan and decision

Add optional manager-reviewed commercial settings to the existing purchasing
policy. Absence retains byte-identical legacy policy serialization and v1/v2/v3
snapshots. New settings require supplier source checks, explicit USD currency,
base units per reported price unit and its reference, and a complete explicit
pool-priority sequence with its allocation reference. No priority is inferred
from pool order, UUID, sales, channel, urgency or profitability. Draft unknowns
stay null. Minimum-quantity/value scope and their reference, plus a manual terms
review reference, can remain unconfirmed and must remain visible.

Scope is one product / supplier SKU / evaluation. Do not allocate between
products, vendors, concurrent evaluations or orders; do not reserve stock or
optimize a budget. Existing independent per-pool arithmetic is retained as
replayable ceilings, labelled as preceding shared allocation. In the approved
priority order, allocate each ceiling from the same finite supplier stock,
rounding down to the approved multiple and withholding a positive allocation
below the policy's per-pool minimum. Unused stock and unmet requests remain
visible. Unknown stock or invalid source/policy/Amazon evidence cannot yield a
known positive combined allocation. Confirmed zero demand stays distinct.

Re-read the already-validated supplier row/snapshot under existing locks.
Require exact current file/job/row/snapshot identity, replayed profile extraction,
matching normalized/staged/snapshot Decimal price and currency, and no price
warnings. Confirm whole price units and case evidence; a fractional source-unit
price is never rounded silently. Zero remains zero with a review warning.
Store source/profile/discount/tax-inclusion evidence. No ConnectBooks historical
price or cost substitutes for a current supplier price.

Only a known combined allocation and verified price produce an exact goods-line
amount. Use integer price-unit counts and an explicit high-precision Decimal
context. This amount excludes unreported freight/tax/other charges and is not
landed cost or a profit forecast. Evaluate vendor minimum quantity only when
explicitly confirmed as supplier order units for this SKU, and minimum value
only when confirmed against this SKU's goods line at reported price. Null
minimums are unknown; confirmed zero is distinct. No mixed-basket assumption,
free-form term parsing, currency conversion or automatic top-up is permitted.
Unmet confirmed minimums block; missing scope/price/terms stay review-required.

New v4 snapshots preserve allocation rows, limits, arithmetic, commercial checks,
source evidence, optional historical financial context and unresolved reasons.
All positive outcomes remain review-required or blocked, purchasing_eligible and
stock_reserved remain false. Legacy successful retries retain their original
observation. A migration extends version/ownership guards and refuses to discard
v4 history or commercial policy settings; table/enum/trigger counts stay fixed.

## Staff workflow and acceptance

Extend blank-by-default policy editor, definition and manager review with the
new settings and explicit priority controls. Extend recommendation preview and
saved detail with a separate combined allocation and commercial-check section;
independent ceilings must never be presented as an aggregate purchase. Preserve
role restrictions, stale-preview invalidation, exact retries, and safe integer /
Decimal-string display. Add desktop/mobile screenshots.

Cover exact and boundary allocation, reversed priorities, unknown/zero stock,
multiple/MOQ handling, huge integers, missing/zero/discounted/case/mismatched or
coerced prices, currencies, minimum scopes and null/zero limits, stale source /
policy/terms, legacy serialization/replay, atomic audit, API roles/tenant scope,
v4 insert/downgrade guards, UI roundtrips and responsive layouts. Run existing
local unit/type/lint/build checks and full PostgreSQL/migration/browser/Windows
CI on the published draft source; inspect final layout artifacts.

No client policy/file/defaults, live sync, purchase, merge or deployment is
included. Client commercial decisions, real-file financial context, comparison /
dashboard integration and three-real-SKU acceptance remain open. Gmail stays
paused and M1 acceptance remains open.
