# 0032. Saved replenishment evaluations from approved policies

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Extends:** ADRs 0022, 0029 and 0030

## Decision and scope

Connect the immutable current policy approval to the existing Amazon readiness
adapter and versioned integer quantity calculation. Add authenticated preview,
record, paginated history and detail APIs. Persist one immutable UUID snapshot
and its correlated audit event atomically. No client policy, source upload,
external call, live sync, frontend, merge or deployment is included.

Each physical pool is evaluated separately. Preserve the approved definition,
current versus approved mapping evidence, source run/snapshot references,
normalized demand/on-hand/inbound, exact calculation inputs, rule versions,
breakdown and blocking reasons. Never sum shared listing stock or pool order
quantities. Reusing a saved result does not establish current readiness.

Supplier availability/quantity and profitability are not yet integrated. State
that explicitly; supply UNKNOWN supplier availability to the existing calculator.
For a positive shortfall, retain required units and the uncapped arithmetic
quantity but block the order suggestion. A clean zero shortfall may report
NO_REORDER. Every response and stored snapshot is purchasing_eligible=false.
Unknown/late inbound and FBM inbound remain blocked by the readiness adapter.

## Review and persistence

All staff may preview/read. Operators/managers/admins may record with the
expected current approval UUID, preview evidence digest, reason, explicit
acknowledgement and client request UUID. Definitions cannot be supplied by the
caller. A later draft leaves the current approval in use; a replacement or
revocation rejects the old approval. Current active mapping and approval identity
must equal the manager-reviewed evidence; unrelated price/last-seen changes do
not silently revoke an approved policy.

Record re-evaluates sources and freshness under the policy stream lock, rejects
changed preview evidence, and copies server-derived inputs/results. Source reads
take shared row locks through the transaction; this is a bounded observation of
ingested data, not a reservation or a claim of synchronized real-time stock.
New source runs after an observation cannot rewrite its saved inputs. Source
snapshots reproduce the arithmetic; they are not a second raw-order archive.

The preview evidence digest excludes observation-clock timestamps (and the
synthetic window bounds when no purchase baseline exists). It includes source
identifiers, observed values, approval/mapping evidence and all readiness issues,
so a newly stale source or changed input requires a fresh preview. A separate
content digest includes the entire saved snapshot and observation time.

Exact retries of a successful request UUID return its original record, even if
sources/policy later change. Reusing the UUID with changed request content or a
different actor conflicts. PostgreSQL enforces ownership, current approval at
insert, snapshot identity, immutable history and the non-purchasing marker.
Downgrade refuses to discard saved evaluations.

## Acceptance

Test exact integer calculations and replay, approved versus newer-draft policy,
revocation/replacement, mapping/source/freshness changes, unknown versus zero
inbound, shared-stock behavior, tenant/role isolation, stale preview refusal,
retry conflicts, atomic audit rollback, database guards and concurrent retries.
Run backend unit/type/lint checks and full PostgreSQL/migration/Compose/browser
and Windows CI on the published source. Document the API and limitations.

Supplier constraints, verified inbound arrivals, profitability integration,
staff recommendation UI and three-real-SKU acceptance remain follow-ups. Gmail
stays paused and Milestone 1 acceptance stays open.
