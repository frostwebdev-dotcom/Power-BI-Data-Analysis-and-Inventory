# 0031. Staff purchasing-policy editor and review screen

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after policy APIs
- **Extends:** ADR 0030

## Decision

Add /purchasing-policies to the authenticated application. Staff browse policies,
inspect the editing head versus currently approved snapshot, read paginated full
history, and explicitly select a historical definition. Operators/managers/admins
can create or edit drafts; only managers/admins preview and record approval or
revocation. Viewer controls remain read-only; server roles stay authoritative.

Use the existing product search/explicit-selection component. Add a bounded,
tenant-scoped product/vendor-product lookup showing vendor name/code, exact SKU,
activity and mapping evidence. Extend policy summaries with current identity
labels for readable history. Filter the existing product-listing lookup to the
chosen marketplace; fetch at most 200 known listings and refuse an incomplete
lookup. No new database table or migration is needed. Lookup never creates or
approves a mapping or matches on descriptions.

A new policy starts without business values, conversions, stock pools or evidence
references. Users explicitly select US marketplace, shipped-only demand, additive
coverage and arrival handling. Required quantities/freshness limits must be whole
integers; empty optional conversions/references become null, never zero or false
confirmation. Unit and pool editors expose exact listing identities, stock source,
channel, membership and evidence. Product/vendor pack_size is not inferred.
No recommendation or financial calculation runs in the browser.

Editing appends a draft against the head captured when editing started. A stale
save preserves unsaved fields, reports conflict and requires an explicit reload.
Selecting another policy/product/history definition or opening an editor clears
any review acknowledgement. Manager preview displays the complete server-returned
definition, mapping evidence and human-readable blockers. Recording uses exactly
its head/target/content/context digests, requires a reason and acknowledgement,
and clears a rejected or superseded preview. Unsaved edits cannot be approved.
Revocation identifies the currently approved version even when a later draft
exists. Display timestamps with their local zone; approval is policy-assumption
review, not current stock certification or purchase authorization.

## Acceptance and boundaries

Test blank inputs and explicit payloads, unknown/null conversions, pool membership
and authoritative stock source, draft/history/current-approval separation, role
controls, reason/acknowledgement gates, stale edit/review handling, context changes,
pagination, empty/error states and narrow-screen layout. Include a real-API
synthetic draft/read workflow, plus mocked full manager decision UI cases. Save
and visually inspect synthetic desktop/mobile editor/review screenshots from CI.
Run backend, PostgreSQL/migration, frontend types/lint/build and browser checks.

No client policy seeding, live Amazon sync, financial upload, recommendation
persistence/integration, merge or deployment is included. Client pack/pool/freshness
policy, arrival evidence and three-real-SKU acceptance remain outstanding. October
year/timezone and cost/coverage confirmation remain open; Gmail stays paused.
