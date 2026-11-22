# 0025. Record immutable, tenant-scoped financial imports

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to proceed with database imports and approved SKU linking
- **Supersedes / Superseded by:** Extends ADR 0024

## Decision and entities

Add financial_imports and financial_import_rows, separate from vendor stock
ingestion. Each has an internal UUID, organization ownership and UTC timestamps.
Retain the original XLSX through the existing write-once StorageBackend before
parsing. A batch, every source row, mapping snapshots and one audit record commit
together. Rejected parsing may leave retained bytes without a database record;
never delete those bytes automatically.

Persisted imports require an explicit reporting year and IANA timezone, in
addition to the already required month/currency/store/marketplace. Derive UTC
half-open calendar-month bounds; reject ambiguous or nonexistent local midnight
boundaries. File creation metadata is not a reporting date. The client's file
must remain a local preview until these missing confirmations arrive.

Use exact case-sensitive Item seller SKUs and existing active APPROVED Amazon
marketplace_listings only, joined to active products in the same organization
and marketplace. Record priority 4, listing/product UUIDs and the approval state
as an immutable snapshot. A conflicting source ASIN, invalid source row or
unapproved/inactive/missing mapping is a review item, never a guessed match.
Unitemized stays unallocated and never receives a product UUID. Source financial
warnings (including zero cost) survive even when a SKU mapping is approved.

Raw SHA-256 plus organization identifies an exact replay. The complete profile
hash must also match; otherwise reject relabeling the same file with new context.
An exact replay returns the original import and does not rebind rows or add an
audit entry. Reject overlapping periods within organization and marketplace;
the current listing namespace represents one Amazon account per organization.
Canonical/source store labels are metadata, not an extra namespace that can
bypass overlap checks. Concurrent requests serialize with a transaction-scoped
advisory lock; database insertion guards independently enforce the overlap rule.
Corrected/replacement reports require a later explicit supersession workflow.

Database guards reject updates/deletes of either snapshot table, foreign-tenant
references and row appends after the original recording transaction. A deferred
constraint verifies batch counters against the complete row set. Mapping changes
afterward do not rewrite historical approval evidence. Historical foreign keys
use RESTRICT. All stored rows explicitly remain ineligible for purchasing.

## API and acceptance

Add authenticated /api/v1/financial-imports upload, paginated history, batch
detail, paginated/filterable review rows and integrity-checked raw download.
DATA_OPERATOR (or ADMIN) can upload; existing read roles can inspect. Unknown
or foreign batch UUIDs return the same 404. Parsing/profile failures return safe
codes without echoing source cells. No staff UI, live upload, migration execution
against staging, Gmail activation or deployment is included in this increment.

Tests must use PostgreSQL to verify atomic auditing, replay/context conflicts,
overlap/concurrency guards, exact approved mapping and ASIN conflict behavior,
tenant isolation, immutable snapshots and raw integrity. Migration upgrade,
downgrade and model drift checks remain required. Use synthetic data; never
commit client exports or persisted financial output.
