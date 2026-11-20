# 0030. Versioned purchasing policies with explicit manager review

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after Amazon readiness
- **Extends:** ADRs 0022 and 0029

## Decision and scope

Persist explicit purchasing-policy definitions and manager decisions through a
staff API. A policy is scoped to one product UUID, vendor-product UUID and US
marketplace. The definition contains the existing quantity policy and Amazon
readiness assumptions, plus explicit additive-coverage and inbound-handling
contracts and a coverage confirmation reference. It supplies no client defaults.
Saving a draft does not approve its assumptions or prove current source data.
No recommendation, Amazon call, live sync, financial upload, policy seeding,
frontend, merge or deployment is included in this increment.

Use one append-only purchasing_policy_revisions ledger. The first UUID also
identifies the policy's stream. Each DRAFT, APPROVED or REVOKED entry carries its
own UUID, organization/product/vendor-product/marketplace, sequence, predecessor,
full definition snapshot, digest, actor, reason and UTC recording time. Reviews
also reference the reviewed revision. The chain cannot fork; the first entry is
unique for its product/vendor-product/marketplace. No row is updated or deleted.

The latest entry is the editing head. The latest non-draft decision determines
the approved definition: APPROVED selects its snapshot; REVOKED clears approval.
A later draft preserves the previous approval until another explicit review.
Approval may only copy the current draft. Revocation copies and references the
currently approved decision, even if a newer draft exists. Approving a replacement
switches policy without changing prior history. Approval is permission to use
reviewed assumptions in a future integration, never permission to place an order.

Data operators, purchasing managers and admins may record drafts. Only purchasing
managers/admins may approve or revoke; all staff may read paginated history.
Every mutation requires a reason and commits with a correlated audit event.
Validate the actor belongs to the active organization. Missing and foreign UUIDs
return the same 404. All revisions and linked identity reads are tenant-scoped.

## Review and concurrency

Every edit supplies its expected head UUID. Review first returns the full target
definition, its digest, head, current decision and a digest of current product,
vendor-product/vendor and Amazon listing mapping evidence. Approval requires an
active product/vendor, an active approved exact vendor-product link, active
approved known Amazon listings, a complete listing configuration, confirmed unit
conversions, distinct-pool references and coverage interpretation. Mapping
approval UUIDs/timestamps are evidence; descriptions and pack_size are not used
for identity or conversion. Unknown draft assumptions may be recorded but block
approval. No current inventory or inbound arrival is certified by policy review.

Record recomputes under shared identity and exclusive stream locks. A changed
head, definition, mapping evidence or review target rejects the stale action.
Approval/revocation needs an explicit acknowledgement. Revocation remains
available when identity mappings are inactive. Successful-request retries that
use an old head return 409, avoiding duplicate decisions; callers reload history.

PostgreSQL guards enforce immutable rows, tenant ownership, canonical root keys,
contiguous sequence/one successor, legal transitions and byte-equivalent JSON
snapshot/digest copying for reviews. Application validation owns the complete
typed policy contract; the database also requires object snapshots, valid
SHA-256 syntax and a non-purchasing marker. A new migration creates the ledger;
downgrade refuses to discard recorded policy decisions.

## Acceptance

Test strict explicit schema, unknown versus confirmed assumptions, deterministic
hashes, role boundaries, tenant/identity isolation, full draft/approve/new-draft/
replace/revoke history, stale head/digest/mapping refusals, legal transitions,
immutability, atomic audit rollback and concurrent writers. Run full PostgreSQL,
migration roundtrip/drift, backend, existing browser/build and Windows checks.
Document the API and current approval versus history semantics.

Client values, real inbound arrival evidence, operational source freshness and
three-real-SKU acceptance are still required before integrated purchasing
recommendations. October year/timezone and financial coverage/cost review remain
unconfirmed. Gmail stays paused; Milestone 1 is not accepted by this change.
