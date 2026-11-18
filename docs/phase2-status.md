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

## Ninth deliverable: explicit purchasing policies and manager review

- ADR 0030 and [the policy guide](purchasing-policies.md) add authenticated policy
  draft/history/approval/revocation APIs with explicit quantity, Amazon readiness,
  vendor-unit and coverage assumptions. No client defaults or policies are seeded.
- A single immutable ledger retains full definitions and decisions. A later draft
  preserves prior approval; an explicit reviewed replacement switches it, and
  revocation clears it without deleting history. Only managers/admins review.
- Reviews recompute scoped identity and definition evidence under locks. Stale
  head/content/mapping reviews are rejected. Mutations and audit commit together;
  PostgreSQL guards preserve ownership, chain integrity and immutable snapshots.
- Migration a61117202604 creates the ledger but has not been deployed. Downgrade
  refuses to discard recorded history. No recommendations or current data are
  certified; every output remains purchasing-ineligible.
- Local contract/type/lint/migration-SQL checks and full PostgreSQL/browser CI
  are recorded against the source in draft PR #22. No policy UI, live client
  policy, financial upload, merge or deployment is included in this increment.

## Tenth deliverable: staff purchasing-policy editor

- ADR 0031 adds policy list/filtering, explicit product/supplier-SKU selection,
  blank-by-default policy fields, listing conversions and physical stock pools.
  Optional conversions/evidence stay unknown; no customer defaults are inferred.
- Current approval, editing head and paginated immutable history are distinct.
  Operators/managers can append drafts; only managers/admins review and record
  approval/revocation with complete server evidence, reason and acknowledgement.
- Stale edits retain unsaved fields. Historical/context changes and stale reviews
  clear acknowledgements; unsaved edits cannot be approved. Viewer controls are
  read-only and server permissions remain authoritative.
- Two bounded catalogue read improvements support readable supplier selection
  and complete marketplace-specific listing reads. Summaries add current display
  labels; the immutable definitions and approval evidence are unchanged.
- Backend/types/lint/build and full PostgreSQL/browser/layout evidence are recorded
  against the source in draft PR #22. No client policy, recommendation calculation,
  financial upload, migration, merge or deployment is included in this increment.

## Eleventh deliverable: saved replenishment evaluations

- ADR 0032 and [the evaluation guide](replenishment-recommendations.md) connect
  current manager approvals to Amazon readiness and exact per-pool quantity
  calculations, with scoped preview/record/history/detail APIs.
- Immutable snapshots preserve reviewed policy, current mapping evidence,
  run/snapshot references, normalized inputs, blockers and replayable arithmetic.
  New drafts preserve approval; revoked/replaced approvals are refused.
- Recording rechecks preview evidence. Reason/acknowledgement and request UUID
  support review and exact retries; snapshot and audit commit atomically.
  Migration a61117202605 preserves immutable history.
- Supplier availability/quantity and profitability are explicitly not integrated.
  Positive shortfalls retain required/uncapped quantities but no usable order
  suggestion. Unknown inbound stays blocked; clean zero shortfalls may show
  NO_REORDER. Every result remains purchasing-ineligible.
- Local and full PostgreSQL/migration/regression evidence are recorded against
  the source in draft PR #22. No new UI, client policy/financial upload,
  live sync, merge or deployment is included.

## Twelfth deliverable: supplier source validation

- ADR 0033 and [supplier evidence](supplier-readiness.md) add explicit optional
  profile/receipt-age/quantity/availability settings to staff policy drafts and
  manager review. Old definitions and saved v1 content/digests remain unchanged.
- Current-profile/constraint evidence is checked against approval. The newest
  vendor file must have a clean job, exact SKU identity, consistent staged row
  and snapshot, whole quantities, confirmed units and valid receipt chronology.
  Bad/missing/partial/tied/latest-profile changes never fall back to older stock.
- New v2 snapshots retain supplier evidence and exact vendor caps. Known zero,
  unknown quantity and conflicting availability remain distinct. Receipt age is
  explicitly a reviewed proxy; supplier effective time is not verified.
- Positive quantities still require financial/commercial/allocation review, and
  every output remains purchasing-ineligible. No supplier stock is reserved or
  summed across independent pool suggestions. Migration a61117202606 preserves
  both versions and refuses destructive downgrade.
- Source-specific backend, PostgreSQL/migration, UI/build/browser and layout
  evidence is recorded in draft PR #22. No client policy/file, live source sync,
  merge or deployment is included.

## Thirteenth deliverable: historical financial context and recommendation review

- ADR 0034 adds explicit current report/binding selection to saved evaluations.
  v3 snapshots retain historical exact SKU amounts, mapping identities, period,
  profile/file provenance, controls, cost/coverage warnings and complete-set totals.
  Uncertain/missing rows stay unknown; unallocated amounts are never distributed.
- The staff Recommendations screen supports approved-policy selection, optional
  report selection, per-pool preview, reason/acknowledgement, audited save,
  bounded filtered history and immutable detail. Roles, context changes, stale
  saves, exact retries and unsafe browser integer displays are handled explicitly.
- Quantities remain unchanged; report amounts are historical context, not current
  costs or forecast profit. Every result remains purchasing-ineligible.
  Migration a61117202607 preserves v1/v2 and refuses destructive v3 downgrade.
- Source-specific local and full CI evidence is recorded in draft PR #22.
  No client data/policy, live sync, purchase, merge or deployment is included.

## Fourteenth deliverable: commercial checks and shared supplier stock

- ADR 0035 adds explicit optional price units/currency, SKU minimum scopes, manual
  terms references and pool-priority decisions to staff policy and manager review.
  Unknowns start blank; absent settings preserve legacy definitions and history.
- v4 snapshots preserve exact latest-source price evidence, combined finite-stock
  allocation by approved priority, per-pool ceilings, remaining stock and scoped
  minimum checks. Existing quantity minima remain per pool; supplier minimums
  are assessed separately. No automatic top-up or budget optimization is added.
- Unknown costs/stock/constraints stay unknown. Source discounts are replayed
  once, and Decimal goods money remains separate from landed cost, forecast
  profit and optional historical report context. No stock is reserved.
- Staff editor/review and preview/history/detail support the new evidence.
  Migration a61117202608 preserves older versions and refuses destructive
  commercial-history downgrade. Full source-specific validation is recorded in
  draft PR #22; no client policy/file, sync, purchase, merge or deployment is included.

## Fifteenth deliverable: vendor comparison and saved-review dashboard

- ADR 0036 and [the comparison/review guide](vendor-comparison-and-review.md)
  add explicit selected-policy comparison, a latest-saved-per-policy review queue
  and dashboard links. All staff read roles can use them; no offers or financial
  reports are selected automatically.
- One scoped observation reuses existing previews under deterministic policy
  locks. Verified current prices are ordered by exact rational values only when
  approved unit projections and stated tax treatment match. Missing prices and
  incompatible assumptions remain visible; price never means purchase approval.
- Bounded literal search and product/vendor/policy/status/approval/shortfall
  filters expose saved evidence. Latest non-draft decisions distinguish current
  approval from changed/revoked approval without certifying current inputs.
  Counts and stockout context are explicitly historical; no stock/money is summed.
- No migration or Milestone 1 dashboard API change is introduced. Source-specific
  local, PostgreSQL/browser CI and desktop/mobile layout evidence are recorded
  in draft PR #22. No client data/policy, sync, purchase, merge or deployment is included.

## Sixteenth deliverable: real-data acceptance preparation

- Added a [walkthrough](milestone-2-acceptance.md), client confirmation worksheet
  and blank three-SKU record. Existing client facts are preserved; unknown year,
  report timezone, cost/advertising coverage and purchasing inputs remain explicit.
- The scope matrix distinguishes implementation/CI evidence from unrun real-data
  acceptance. It records the current adapter's positive-FBA/FBM inbound limits
  and the distinction between saved dashboard counts and current urgency.
- No real case, client policy, import, source sync, deployment or sign-off was
  performed. The client message is prepared only and has not been sent. Completed
  records and real financial evidence belong in an approved private project location.
- Documentation checks and source-specific CI evidence are recorded in draft
  PR #22. Application behavior and schema are unchanged by this preparation.

## Seventeenth deliverable: current selected-policy replenishment check

- ADR 0037 and [the current-check guide](current-replenishment.md) add scoped
  current-approved policy search and an explicit 1–10-policy fresh check, linked
  from navigation/dashboard. No policy, report or client default is selected.
- Exact Amazon stockout urgency is independent of supplier readiness. Unknown
  required Amazon inputs stay unknown; an urgent item can have an unavailable
  supplier. Separate review opportunities require verified finite supply and
  complete assessed v4 allocation/price/minimum/terms evidence.
- Current check times, cutoffs, per-pool arithmetic and complete previews remain
  visible. Counts refer only to selected policies; no whole-catalogue claim,
  source sync, saved fallback, reservation or purchase is introduced. Source-
  specific local, full CI and desktop/mobile evidence is recorded in draft PR #22.
- Client confirmations, supported positive-FBA/FBM inbound arrival handling and
  real-data acceptance remain open. No client file/policy, merge or deployment occurred.

## Eighteenth deliverable: reviewed inbound arrival evidence

- ADR 0038 and [the guide](reviewed-inbound.md) add an immutable manager
  confirmation/revocation ledger tied to exact approval, inventory source and
  pack/pool context. FBA totals reconcile to native evidence; FBM permits an
  explicit complete manual position. Unknowns are never defaulted to zero.
- Explicit revision choices compose with recommendations, current checks,
  vendor comparisons and saved review. V5/V2 snapshots preserve native issues,
  provenance and per-arrival qualification. Existing v1–v4 shapes/retries remain.
- Exact ETA qualification credits arrivals strictly before stockout and within
  coverage. Expiry, missed ETAs or changed sources require review again. Scoped
  roles, atomic audits, exact retries and database guards protect the ledger.
- Local and PostgreSQL/browser CI evidence is recorded in draft PR #22. Real
  client evidence/policies, acceptance, merge and deployment remain outstanding.
  No mailbox, provider sync, inventory mutation or purchase occurs.

## Nineteenth deliverable: three-SKU acceptance handoff update

- Updated the walkthrough, unsent client confirmation request and blank real-case
  record to match implemented reviewed inbound and the verified application
  baseline `34d05b0` (CI #221: 2,299 backend / 92 browser tests).
- Intake gates keep report year/timezone, three exact SKUs, purchasing decisions,
  complete position evidence and execution environment explicitly unconfirmed.
  Added current revision/source references, native/declared/credited quantities,
  exact capture-anchored arrival reconciliation and condition coverage records.
- Known zero, unknown and excluded credit remain distinct. Expected blocker
  behavior and operational readiness are separate decisions; missing real-case
  coverage is NOT EXERCISED. No real record, client message, data action, policy
  approval, deployment or milestone sign-off was performed by this preparation.
- This increment changes documentation only. Validation is recorded in draft
  PR #22; the application, schema and existing test cases are unchanged.

## Remaining Milestone 2 work

- Client-approved Amazon pack/pool/freshness policy, operational acceptance of
  reviewed FBA/FBM positions and real supplier-aware recommendations.
- Client reporting year/timezone, coverage assertions and cost review before
  real-file operational acceptance.
  The database/API and staff interface are implemented on the draft branch.
- Client confirmation and operational acceptance of SKU commercial scopes,
  pool priority, price conversions, landed-cost inputs and historical financial context.
- Operational acceptance of the implemented scoped APIs, dashboard, search,
  vendor comparisons and cost/profit context with real data.
- Real-data acceptance of the implemented on-demand current urgency/opportunity
  workflow. Historical saved counts alone remain insufficient evidence.
- Three-real-SKU acceptance walkthrough with client-approved source/policy inputs.

No revised budget or delivery date is recorded as client-approved. Milestone 1
is not marked accepted by this development start.
