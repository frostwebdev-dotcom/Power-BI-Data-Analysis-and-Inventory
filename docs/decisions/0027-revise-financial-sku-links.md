# 0027. Revise financial SKU links without rewriting source records

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after the financial review screen
- **Supersedes / Superseded by:** Extends ADRs 0025 and 0026

## Decision and scope

Add explicit SKU-link revisions for an existing financial import. The source
batch, its financial rows, reporting context, original bytes and original mapping
evidence remain immutable. Two append-only UUID/tenant-scoped tables store a
revision header and one binding snapshot per original financial row. Revisions
form a single ordered chain; only its latest head can be superseded. This is a
revision of product links, not a replacement financial report or cost correction.

DATA_OPERATOR/ADMIN first request a preview against the currently approved
Amazon mappings. Preview writes nothing. It returns the expected chain head,
an exact digest of proposed bindings, counts and paginated changed-row evidence.
Recording requires that head and digest plus a nonblank reason. Recompute under
the same marketplace transaction lock and approved-mapping shared locks; reject
a stale head, changed mapping evidence or a no-op. Never apply unseen changes.
Append all bindings and an audit event atomically. Concurrent submissions cannot
fork the chain. A retry against an old head is a conflict, not a silent rebind.

Reuse exact case-sensitive seller SKU, same organization/marketplace, active
approved listing and active product checks. Invalid rows and ASIN conflicts
never link. Unitemized remains unallocated. Source cost/arithmetic warnings and
batch issues survive every revision. No mapping is approved or changed by this
workflow, and every record remains ineligible for purchasing.

Foreign keys and insertion guards enforce revision/source ownership, full row
coverage, sequential ancestry, active actor ownership, approved links and original
transaction-only row insertion. Updates/deletes are rejected. History identifies
actor, reason, creation time and predecessor. Money is read from original rows;
new binding rows contain no financial amounts.

## API/UI and acceptance

Add read-only paginated revision history, a write-role preview endpoint and a
write-role record endpoint under /financial-imports/{id}/binding-revisions.
Preview changed rows are paginated (maximum 200). The rows endpoint accepts an
optional revision UUID and returns original money with that revision's mapping
and review evidence. Unknown/foreign import and revision IDs return 404.

The staff screen starts on original links and explicitly labels the selected
revision. Staff can inspect older revisions, preview changed links, provide a
reason and record the reviewed proposal. A stale proposal requires a new preview.
Selected-row pagination/filter state resets when changing revision. Viewers see
history only. Original import counts are labelled as original; selected revision
counts and status appear separately, including batch-level review warnings.

Tests cover unchanged source evidence/money/raw bytes, gained/lost/conflicting
links, no-op and stale proposals, chain/concurrency protection, scoped reads and
roles, immutable/full revisions, rollback with audit, historical selection and
browser preview/record/errors. Run migration roundtrip/drift and existing suites.
Use synthetic data. No deployment, client workbook upload, Gmail activation,
corrected-report replacement or purchasing integration is part of this increment.
