# Milestone 2 status

Started 11 November 2026 by explicit owner instruction. Work is isolated from
the deployed Milestone 1 system. Gmail authorization/source setup remains paused.

## First deliverable

- Scope and formula examples documented in [milestone-2-scope.md](milestone-2-scope.md).
- ADR 0022 records the boundary and live-data dependencies.
- Quantity preview and local JSON-input CLI implemented, with the synthetic
  worked example returning 20 units/day, 280 required units and 210 suggested units.
- All 1,000 local unit tests passed, including 61 new quantity/CLI cases.
- Backend Ruff and strict mypy passed (216 source files). The CLI example ran
  successfully without a database or credentials.
- Code commit `d3e13b493cb3a41e2db1dd1121ac760df8911daf` passed
  [CI #139](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/43148423639):
  1,487 backend tests including PostgreSQL integration, 23 browser tests,
  backend/frontend lint and strict types, builds, migration/schema/Compose and
  Windows PowerShell/storage checks. Deployment was skipped as intended.
- [Draft PR #22](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/pull/22)
  contains the first module. Nothing has been merged or deployed.

## Second deliverable: profitability CSV preview

- ADR 0023 and [the preview guide](profitability-csv-preview.md) define explicit
  financial column mappings, UTC period bounds, currency, value/sign conventions
  and the versioned profit/margin/COGS-based ROI formulas.
- The local CLI and preview service are implemented. Missing costs remain unknown;
  duplicate SKUs, bad numeric values, mismatched currencies, ambiguous headers,
  malformed/ragged rows and duplicate JSON profile keys are reported or rejected.
- The shared reader has opt-in strict financial checks; vendor import defaults
  retain their existing row-width and quoting settings.
- Synthetic sample: 1,000 net revenue, 400 COGS and 220 other expenses produce
  380 profit, 38% margin and 95% ROI. This is not a verified ConnectBooks layout.
- All 1,067 local unit tests passed, including 67 new financial-preview cases.
  Ruff and strict mypy passed (220 source files), and the sample CLI ran.
- Current full PostgreSQL/Compose/browser verification is recorded in draft
  PR #22's checks and validation section; it is not inferred from local unit tests.
- There is no canonical product binding, persisted financial import, API/UI,
  real-file acceptance, merge or deployment for this increment.

## Third deliverable: signed ConnectBooks XLSX preview

- On 17 November the owner supplied the October XLSX and relayed confirmation
  of USD, Item as Amazon seller SKU, and the canonical/source store labels.
  The reporting year/timezone and advertising coverage remain explicitly
  unconfirmed, rather than being inferred from workbook creation metadata.
- ADR 0024 and [the Excel preview guide](connectbooks-xlsx-preview.md) document
  the separate signed-source contract. Header order can change; duplicated
  unused descriptive headers do not make mapped financial columns ambiguous.
- The local service and CLI preserve Decimal precision, refund/cost credits and
  signed fees, and independently reconcile profit, adjusted net sales and the
  final control totals. Fee subcategories are not deducted a second time.
- Unitemized remains unallocated, missing cost stays unknown, positive sales
  with zero reported cost requires review, and duplicate/non-text SKUs are blocked.
  All rows remain unresolved and ineligible for purchasing.
- Original October-file CLI validation passed locally: all supported row and
  footer controls reconciled within one cent; the expected unallocated and
  zero-cost review cases were retained. Original bytes were verified unchanged.
  No customer workbook, financial values or output JSON is committed.
- All 1,124 local unit tests passed, including 57 signed-XLSX cases. Backend
  Ruff formatting/lint and strict mypy passed (224 source files).
- Local and full-CI verification are recorded with the source commit in draft
  PR #22. This increment does not persist financial data or add staff API/UI,
  canonical SKU binding, merge or deployment.

## Remaining Milestone 2 work

- Amazon freshness, inventory-pool and pack normalization adapter.
- Complete reporting context, cost review, approved SKU binding and a persisted,
  period-aware profitability import using the received ConnectBooks layout.
- Persisted purchasing policy and audited recommendation snapshots.
- Tenant-scoped APIs and application dashboard/search/filtering.
- Vendor comparisons, cost/profit displays and real-data acceptance walkthrough.

No revised budget or delivery date is recorded as client-approved. Milestone 1
is not marked accepted by this development start.
