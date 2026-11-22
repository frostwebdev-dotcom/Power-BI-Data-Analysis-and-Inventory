# 0024. Preview the supplied ConnectBooks XLSX profit-and-loss layout

- **Status:** Accepted for the next bounded Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to proceed after supplying the October export
- **Supersedes / Superseded by:** Extends ADR 0023; does not change CSV semantics

## Context

The owner supplied an original Excel profit-and-loss export and relayed client
confirmation that the reporting month is October, currency is USD, Item is the
Amazon seller SKU, and the canonical store name is Brand Name Deals-Amazon.
The source Store label is Brand Name Deals. The year and reporting timezone
are not embedded in the file or explicitly confirmed. The second workbook
has reordered columns and additional fee categories compared with the first.

The existing CSV preview requires positive expense totals and globally unique
headers. This export instead contains signed fees, returned-cost credits,
duplicate unused descriptive headers, an Unitemized bucket and a final unlabeled
control-total row. Converting it directly into the old profile would lose meaning.

## Decision

Add a separate, non-persistent XLSX preview, typed profile and local CLI. Read
Open XML numeric values directly as Decimal; preserve the original bytes and
hash. Bound archive expansion, worksheet rows, columns and cells; reject formulas,
ambiguous mapped headers, unsupported packages and malformed cells. No new
dependency, database, API, scheduler or inventory mutation is introduced.

Map supported financial headers uniquely by normalized name, never by fixed
column letter or description matching. Duplicate unused descriptive headers
are allowed. Require the explicitly named sheet and source Store label; retain
the canonical store name separately. All SKU identities remain unresolved.

Use the source's signed arithmetic, without separately deducting fee details:

- Net Sales = Sales + Refunds + Adjustments.
- Profit = Net Sales + Fees - Cost - Ad Spend.
- Margin = computed profit / positive Net Sales * 100.
- ROI = computed profit / positive Cost * 100.

Retain reported ratios separately; these are rounded source values, not exact
recalculation controls. Source arithmetic is not proof that all economic costs,
tax treatment, advertising coverage or client ROI policy are complete.

Include Unitemized in financial reconciliation but never expose it as a seller
SKU. Reconcile all additive supported fields against exactly one final control
row to a one-cent tolerance. Missing or malformed amounts stay unknown and make
the affected totals incomplete. Duplicate SKUs are all invalidated, not combined.
Positive Net Sales with zero Cost requires cost review; preserve reported profit
but do not treat zero cost as proof of free goods. Negative refund quantities,
returned-cost credits, losses and fee credits retain their signs.

Year and timezone are optional preview context with explicit review issues when
absent; do not manufacture UTC reporting bounds from a file creation timestamp.
Every row and report explicitly remain ineligible for purchasing. Future persisted
imports require complete reporting bounds, tenant-scoped approved SKU bindings,
raw storage, overlap/replay rules, immutable snapshots and audit entries.

## Acceptance

Synthetic tests cover reordered and duplicate headers, signed refunds and costs,
fee-detail non-duplication, source/footer reconciliation, zero versus missing
cost, unallocated amounts, store mismatch, duplicate SKUs, text identifiers,
formula/error cells, malformed/oversized archives and safe CLI errors. Run the
original October workbook locally without committing it or its financial values.
Record test evidence and limitations in the status document and draft PR.

## Alternatives considered

- Reuse positive-expense CSV rules: would reject or invert valid refund credits.
- Use a fixed column offset: already contradicted by the two supplied exports.
- Bind Item to a product immediately: a client-confirmed SKU column does not
  replace tenant-scoped approved product mappings.
- Assume the current year/timezone: creates unsupported reporting boundaries.
