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

## Remaining Milestone 2 work

- Amazon freshness, inventory-pool and pack normalization adapter.
- Real ConnectBooks export, mapping and period-aware profitability import.
- Persisted purchasing policy and audited recommendation snapshots.
- Tenant-scoped APIs and application dashboard/search/filtering.
- Vendor comparisons, cost/profit displays and real-data acceptance walkthrough.

No revised budget or delivery date is recorded as client-approved. Milestone 1
is not marked accepted by this development start.
