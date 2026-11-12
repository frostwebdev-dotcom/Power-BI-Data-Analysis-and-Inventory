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
  successfully without a database or credentials. Full PostgreSQL/browser CI
  remains pending; no integration-test success is inferred from unit tests.
- Draft PR preparation is in progress. Nothing has been merged or deployed.

## Remaining Milestone 2 work

- Amazon freshness, inventory-pool and pack normalization adapter.
- Real ConnectBooks export, mapping and period-aware profitability import.
- Persisted purchasing policy and audited recommendation snapshots.
- Tenant-scoped APIs and application dashboard/search/filtering.
- Vendor comparisons, cost/profit displays and real-data acceptance walkthrough.

No revised budget or delivery date is recorded as client-approved. Milestone 1
is not marked accepted by this development start.
