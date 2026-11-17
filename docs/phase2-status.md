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

## Fourth deliverable: recorded financial imports and approved SKU links

- ADR 0025 and [the import guide](financial-imports.md) define two immutable,
  organization-scoped financial tables, the migration, storage/replay policy,
  period overlap checks and authenticated staff APIs.
- Original bytes are retained before parsing. Batch, row snapshots, exact
  approved SKU links and an audit event are committed together. Unknown and
  conflicting mappings remain review items; source cost warnings are preserved.
- Recorded imports require explicit year/timezone; they do not infer the missing
  reporting context from the October workbook. No client financial data has been
  persisted to a live database, and the migration has not been deployed.
- Database integration, concurrency, immutability, API isolation and full-suite
  evidence are recorded against the source commit in draft PR #22.
- All 1,153 local unit tests passed; Ruff and strict mypy passed (231 source
  files), and offline migration SQL generation passed. Local PostgreSQL is not
  running, so the 23 new database tests require the PR's PostgreSQL CI job.

## Fifth deliverable: staff financial import and review screen

- ADR 0026 adds /financial-imports to the authenticated navigation. It includes
  role-aware upload, paginated history, original downloads, batch controls and
  warnings, filtered review rows and a source/mapping detail drawer.
- Report month/year/timezone start blank. USD, worksheet/store context and the
  advertising-coverage assertion are visible; unsupported files are refused.
- Monetary strings, including scientific Decimal representations, are displayed
  without binary floating-point conversion or browser financial calculations.
  Missing values, zero cost and unallocated rows retain their distinctions.
- Type checking, lint and the production build passed locally. All 1,153 backend
  unit tests, Ruff and strict mypy passed. Browser startup is restricted locally;
  the PR CI runs nine new UI cases (including a real-API synthetic upload/replay)
  and saves synthetic desktop/mobile screenshots for visual review.
- Full source-commit CI and layout evidence are recorded in draft PR #22. No
  owner workbook has been uploaded, and this change is not merged or deployed.

## Sixth deliverable: reviewed SKU-link revisions

- ADR 0027 defines append-only revision headers and binding rows, separate from
  immutable original money, raw files and source warnings. Existing approvals
  can be applied through a no-write preview followed by a reasoned staff action.
- Recording checks the expected latest revision and full binding digest under
  transaction/mapping locks. Stale/no-op proposals are rejected; a chain cannot
  fork. All revised bindings and the audit event commit together.
- API and staff history show original and revised links explicitly. Changing
  revision resets row filters/pagination; viewers cannot preview or record.
- Validation for this source is recorded in draft PR #22 after local checks and
  PostgreSQL/browser CI. No client report upload, merge or deployment is included.

## Seventh deliverable: reviewed corrected source reports

- ADR 0028 adds immutable predecessor/successor source lineage, inherited
  reporting context and preview/reason/acknowledgement before correction.
- Preview shows exact total changes, reconciliation checks, added/removed SKUs
  and row/batch warnings. Save rechecks source/binding state and records the
  new report, full row set and audit atomically. Exact retries are idempotent.
- Current-only history avoids double counting versions. Historical money/raw
  files and bindings remain readable; superseded sources cannot be rebound.
- Local unit, type/lint/build and migration-SQL evidence, and full PostgreSQL/
  browser/desktop/mobile verification, are recorded in draft PR #22 for its
  current source. No client report upload, merge or deployment is included.

## Eighth deliverable: Amazon input-readiness evidence

- ADR 0029 and [the readiness guide](amazon-data-readiness.md) add a scoped
  read-only API over ingested PostgreSQL. Explicit freshness limits, approved
  mappings, listing-to-base-unit conversions and named FBA/FBM stock pools are
  evaluated without purchasing defaults, external calls or database writes.
- One stock-source listing supplies each pool's quantity. Shared aliases are
  not summed; unknown/coerced quantities stay unknown and inbound without
  arrival evidence cannot be credited. Missing latest-run SKUs do not fall back.
- A new opt-in order-date baseline CLI mode and safe per-run/line provenance
  establish complete purchase-window evidence. The default scheduler/report
  and legacy velocity behavior remain unchanged. No live baseline was run.
- Strict request/conversion tests, PostgreSQL freshness/coverage/pool/isolation
  tests, ingestion provenance tests and full CI evidence are recorded against
  the source in draft PR #22. All outputs remain purchasing-ineligible and
  request confirmations are not persisted as approved policy.
- There is no new UI, migration, live client upload, merge or deployment.

## Remaining Milestone 2 work

- Confirmed Amazon pack/pool/freshness policy, inbound arrival evidence (including
  merchant inbound), integrated recommendations and three-real-SKU acceptance.
- Client reporting year/timezone, coverage assertions and cost review before
  real-file operational acceptance.
  The database/API and staff interface are implemented on the draft branch.
- Persisted purchasing policy and audited recommendation snapshots.
- Tenant-scoped APIs and application dashboard/search/filtering.
- Vendor comparisons, cost/profit displays and real-data acceptance walkthrough.

No revised budget or delivery date is recorded as client-approved. Milestone 1
is not marked accepted by this development start.
