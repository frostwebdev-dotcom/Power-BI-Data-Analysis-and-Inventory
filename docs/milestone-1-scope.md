# Milestone 1 — Scope

Status: **In delivery.** Scope amended by [ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md); progress in [phase1-status.md](phase1-status.md).
Last updated: 2026-09-14

---

## 1. Objective

Establish the data foundation and ingestion pipeline for the Purchasing &
Replenishment Management System: a canonical product catalog sourced from
Nineyard, a vendor registry, reliable vendor inventory ingestion from CSV/XLSX,
deterministic product matching with a human-controlled exception path, and
enough audit and administration surface to operate and verify all of it.

Milestone 1 deliberately produces **no purchasing decisions**. It produces
trustworthy, matched, auditable data that later milestones will reason over.

---

## 2. In scope

### 2.1 PostgreSQL database foundation
Schema, UUID primary keys, UTC timestamps, constraints and indexes, Alembic
migration baseline, seed/reference data, connection management, transaction
boundaries.

### 2.2 Nineyard API integration
An isolated client for the Nineyard API behind an anti-corruption layer:
authentication from environment variables, pagination, retry with backoff,
rate-limit handling, request/response logging, and raw response retention.
No Nineyard-shaped types leak into the domain model.

### 2.3 Nineyard product/catalog synchronization
Scheduled and on-demand sync that upserts the canonical product catalog keyed on
Nineyard Catalog Item Number, records a sync run with counts and outcome, retains
the raw payload per item, and detects added / changed / removed catalog items.

### 2.4 Vendor database
Vendors as first-class records: identity, code, status, currency, contact
details, lead-time defaults, and activation state. Full CRUD via the admin
interface, fully audited.

### 2.5 Vendor inventory imports (CSV and XLSX)
Upload or drop-in ingestion of vendor inventory/price files. The raw file is
persisted byte-for-byte with a checksum before any parsing occurs. Parsing is
streaming where practical, row-addressable, and resumable at the batch level.

### 2.6 Vendor-specific import profiles
Per-vendor, versioned configuration describing how that vendor's file is read:
format, encoding, delimiter, header row, sheet selection, column-to-field
mapping, value normalization rules, quantity/price semantics, and pack-size
handling. Profiles are **data, not code** — adding a vendor requires no deploy.

### 2.7 Deterministic product matching
The priority chain defined in [CLAUDE.md §5.1](../CLAUDE.md). Same inputs and
same mapping state always yield the same result, and the deciding rule is
recorded on every row.

### 2.8 Product-mapping exception workflow
A queue for every uncertain match. Reviewers see the source row, the candidates
with the reason each was suggested, and approve, reject, or defer. Approvals
create permanent mappings that feed priorities 3 and 4 on later imports.

### 2.9 Import validation and reporting
Structural, type, business-rule, and referential validation with per-row severity
(error / warning / info). Each batch produces a durable report: totals, outcome
breakdown, matched-by-rule counts, exception counts, and a downloadable error
listing tied to source row numbers.

### 2.10 OOS watchlist
Users mark products (or vendor lines) as watched. Watch entries are durable,
attributable, and deactivatable.

### 2.11 Availability transition detection
On each import, compare the new vendor inventory snapshot against the previous
one and emit an availability event when an item transitions
`unavailable → available` (and the reverse, for completeness). Watched items
surface these transitions prominently in the admin interface.

### 2.12 Basic audit logging
Append-only audit trail per [CLAUDE.md §6](../CLAUDE.md).

### 2.13 Minimal administration interface
Next.js application covering: vendors, import profiles, import upload + batch
status + report, exception queue review, product lookup, watchlist, availability
events, and audit log browsing. Function over polish.

### 2.14 Amazon SP-API read-only ingestion
Added by [ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md)
at the client's request, and bounded to exactly: LWA authentication; retrieval
of order lines sufficient for 7/14/30-day sales velocity; retrieval of FBA
inventory including inbound quantities, and FBM quantity; retrieval of listings
so Amazon seller SKUs can be mapped to the Nineyard Catalog Item Number / UPC
structure through the matching chain in §2.7; storage in PostgreSQL; scheduled
ingestion; error handling and logging; one read-only velocity endpoint and one
CLI. **Read-only throughout** — no write to Amazon, no buyer PII, no
Restricted Data Tokens.

---

## 3. Out of scope

The following are **not** built, stubbed, or depended upon in this milestone:

| Excluded | Note |
|---|---|
| Amazon SP-API beyond the read-only ingestion in §2.14 | No writes to Amazon, no buyer PII or Restricted Data Tokens, no replenishment math. See [ADR 0011](decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md). |
| ConnectBooks | No accounting integration. |
| Replenishment calculations | No reorder points, coverage, or demand math. |
| Profitability calculations | No margin/landed-cost math. |
| Budget optimization | No allocation or optimization. |
| RFQ and quote processing | No quote lifecycle. |
| Purchase-order generation | No POs of any kind. |
| Analyzer.tools | No integration. |
| Power BI | No datasets, gateways, or exports — despite the repository name. |
| Walmart integration | No Walmart channel. |
| General inbound email workflows | ADR 0019 admits bounded read-only vendor inventory intake from Microsoft 365. RFQs, replies, quotes, sending mail and arbitrary portal automation remain excluded. |

### Boundary clarifications

- **Amazon SKUs**: the `marketplace_listings` table and approved Amazon SKU
  mappings are in scope because match priority 4 requires them. They may be
  populated by the read-only SP-API ingestion (§2.14), by import, or by manual
  entry. Approval of a mapping is always a human action (§2.8).
- **Scheduling**: a job runner sufficient to trigger catalog sync and process
  imports is in scope. A general workflow engine is not.
- **Vendor inventory email intake**: authorized on 6 November 2026. Poll one
  configured Microsoft 365 mailbox/folder, route approved senders and filenames
  to pinned vendor profiles, retain/import new attachments or approved direct
  HTTPS downloads, and record duplicates, failures, review requirements and
  source freshness. A vendor sending no new file does not imply zero stock.
  Live activation requires the exact mailbox, scoped read access and real
  vendor sender/link rules. No mailbox access or connection is assumed.
- **Notifications**: availability events are recorded and displayed in-app.
  Email/SMS/push delivery is out of scope.
- **Auth**: minimal authentication and role separation sufficient to attribute
  audit entries and gate approvals. SSO/SAML/SCIM is out of scope.

---

## 4. Milestone 1 exit criteria (summary)

Full, testable criteria live in
[docs/acceptance-criteria.md](acceptance-criteria.md). In summary, Milestone 1 is
done when:

1. `docker compose up` yields a working Postgres + API + web stack from a clean
   checkout using only `.env` variables.
2. A Nineyard catalog sync populates products with Catalog Item Number as the
   business key and an internal UUID as the identity, with raw payloads retained.
3. A vendor and its import profile can be created entirely through the admin UI.
4. A CSV and an XLSX vendor file each import end to end, with the raw file
   retained unchanged and byte-identical to the upload.
5. Matching resolves rows through the documented priority chain, records the
   deciding rule, and routes every uncertain row to the exception queue.
6. No row is ever auto-matched on description alone.
7. An approved exception creates a permanent mapping that is used automatically
   on the next import of the same vendor SKU.
8. A batch report accurately reflects totals, outcomes, and errors.
9. Adding a product to the watchlist and then importing a file that flips it from
   unavailable to available produces a visible availability event.
10. Every mutation listed in CLAUDE.md §6 appears in the audit log with actor,
    before/after state, and UTC timestamp.
