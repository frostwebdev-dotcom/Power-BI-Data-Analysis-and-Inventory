# CLAUDE.md — Purchasing & Replenishment Management System

Operating rules for any agent or contributor working in this repository.
These rules are binding. When a request conflicts with them, raise the conflict
before writing code.

---

## 1. What this project is

A production Purchasing and Replenishment Management System. Work is delivered in
milestones. **The repository is currently in Milestone 1.**

Authoritative scope documents:

- [docs/milestone-1-scope.md](docs/milestone-1-scope.md) — what is in and out
- [docs/architecture.md](docs/architecture.md) — structure, stack, entities, plan
- [docs/acceptance-criteria.md](docs/acceptance-criteria.md) — definition of done
- [docs/phase1-status.md](docs/phase1-status.md) — current progress
- [docs/decisions/](docs/decisions/) — architecture decision records (ADRs)

---

## 2. Milestone 1 scope (IN)

1. PostgreSQL database foundation
2. Nineyard API integration
3. Nineyard product/catalog synchronization
4. Vendor database
5. Vendor inventory imports for CSV and XLSX
6. Vendor-specific import profiles
7. Deterministic product matching
8. Product-mapping exception workflow
9. Import validation and reporting
10. OOS (out-of-stock) watchlist
11. Detection of previously unavailable products that become available
12. Basic audit logging
13. Minimal administration interface
14. Amazon SP-API read-only ingestion (ADR 0011)
15. Read-only Microsoft 365/Gmail vendor inventory email intake (ADRs 0019/0020),
    authorized on 6 November 2026 and adapted to the client's supplied Gmail
    mailbox: attachments and approved direct download links only.

## 3. Explicitly OUT of Milestone 1

Do not build, stub, scaffold, or add dependencies for any of the following:

- Amazon SP-API beyond the read-only ingestion defined in ADR 0011 (no writes, no PII, no replenishment math)
- ConnectBooks
- Replenishment calculations
- Profitability calculations
- Budget optimization
- RFQ and quote processing
- Purchase-order generation
- Analyzer.tools
- Power BI
- Walmart integration

Data structures that later milestones will need (for example, the Amazon SKU
table) may exist **only** where a Milestone 1 rule requires them — see §5.
Storing an Amazon SKU is in scope; calling Amazon is not.

---

## 4. Required architecture

| Concern | Requirement |
|---|---|
| Frontend | Next.js, React, TypeScript |
| Backend | Python 3.12+, FastAPI, Pydantic |
| ORM | SQLAlchemy 2 (typed, `Mapped[...]` style) |
| Migrations | Alembic |
| Database | PostgreSQL |
| Backend testing | pytest |
| Frontend testing/lint | standard Next.js tooling |
| Local services | Docker Compose |
| API format | versioned REST under `/api/v1` |
| Primary keys | UUID |
| Time | store UTC timestamps |
| Secrets | environment variables only |
| Raw imported files | retained unchanged |
| Important data changes | recorded in audit logs |

### Non-negotiable conventions

- **Every** persisted entity has an immutable internal UUID primary key.
  Business keys (Catalog Item Number, vendor SKU, UPC) are separate columns with
  their own constraints. Never use a business key as a foreign key target.
- All timestamps are `TIMESTAMPTZ` and are written in UTC. Never store naive
  local time. Convert to the user's timezone at the presentation layer only.
- No secret, token, connection string, or credential is ever committed. Config
  is read from environment variables through a single typed settings object.
- Every route lives under `/api/v1/...`. Breaking changes require `/api/v2`,
  not silent edits.
- Schema changes ship as Alembic migrations. No `create_all()` in any
  environment other than throwaway test fixtures.
- Raw uploaded files are written once to storage and never modified, re-encoded,
  normalized in place, or deleted as part of normal processing.

---

## 5. Product identity rules

These rules govern correctness of the entire system. They may not be relaxed for
convenience.

1. The **Nineyard Catalog Item Number** is the primary business reference for a
   product.
2. The database **must also** carry an immutable internal UUID for every product.
   The UUID is the join target everywhere; the Catalog Item Number is a unique
   business attribute.
3. One Catalog Item may have **multiple Amazon SKUs**. Model this as
   one-to-many. Never assume a single SKU per product.

### 5.1 Product matching priority

Matching is evaluated strictly in this order. The first rule that produces a
single unambiguous result wins, and the rule that fired is recorded.

| Priority | Rule | Automatic? |
|---|---|---|
| 1 | Exact normalized UPC | Yes |
| 2 | Exact Nineyard Catalog Item Number | Yes |
| 3 | Previously approved vendor SKU mapping | Yes |
| 4 | Previously approved Amazon SKU mapping | Yes |
| 5 | Controlled match suggestion | **No — requires human approval** |

### 5.2 Hard constraints

- **A product description must never be the sole basis for an automatic match.**
  Description similarity may only ever contribute to a priority-5 *suggestion*
  that a human approves.
- **Any uncertain match enters the exception queue.** "Uncertain" includes: no
  rule fired, a rule matched more than one product, or only a priority-5
  suggestion is available. Never guess. Never silently pick the first candidate.
- **Approved mappings are stored permanently.** An approved vendor-SKU or
  Amazon-SKU mapping is durable, auditable, and reused by priorities 3 and 4 on
  every subsequent import. Approvals are not recomputed, expired, or overwritten
  by later automated runs; they are superseded only by an explicit, audited
  human action.
- Matching is **deterministic**: identical input against identical mapping state
  must always produce the identical outcome and the identical rule attribution.

---

## 6. Audit logging

Record an audit entry for every important data change, including at minimum:

- creation, modification, deactivation of vendors and import profiles
- approval, rejection, or supersession of any product mapping
- resolution of any exception-queue item
- import batch lifecycle transitions
- watchlist add/remove
- any administrative override of an automated outcome

Each entry captures actor, action, entity type, entity UUID, before/after state,
UTC timestamp, and a request correlation id. Audit rows are append-only.

---

## 7. Working agreements

- **Plan before code.** Scope, entities, and acceptance criteria are documented
  first and kept current.
- **Do not expand scope.** If something in §3 seems necessary, stop and ask.
- **Record decisions.** Any architecturally significant choice gets an ADR in
  [docs/decisions/](docs/decisions/) using the template there.
- **Keep status honest.** [docs/phase1-status.md](docs/phase1-status.md) reflects
  reality, not intent. Report failures with their output.
- **Tests accompany behavior.** Matching, normalization, parsing, and validation
  are covered by unit tests with explicit fixture cases, including the negative
  cases (ambiguous match, bad check digit, malformed row).
- Existing work is never deleted or overwritten without explicit instruction.
