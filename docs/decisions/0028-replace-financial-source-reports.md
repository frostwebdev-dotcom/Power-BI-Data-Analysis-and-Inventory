# 0028. Reviewed replacement of a financial source report

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after audited SKU-link revisions
- **Supersedes / Superseded by:** Extends ADRs 0025–0027

## Decision

Allow DATA_OPERATOR/ADMIN to replace the current financial source report with a
corrected XLSX through a preview and explicit reasoned record action. Create a
new immutable financial_imports row and its complete financial rows. Add nullable
supersedes_import_id (composite tenant FK, unique), replacement_reason and
replacement_preview_sha256. Existing records stay unchanged. One parent can have
one successor, so a report lineage cannot fork. Original files, source rows,
binding revisions and audit history remain accessible at their original UUIDs.

Inherit the complete confirmed profile from the replaced report. This workflow
corrects source contents, not reporting dates, store/account context, worksheet
selection or coverage assertions. A different context requires a separate
explicit workflow. Exact source-file replay still returns its original record.

Preview retains original replacement bytes before parsing but writes no database
record or audit event. Show old/new control totals and exact differences, row
and approved-link/review counts, added/removed seller SKUs, batch warnings and
paginated candidate rows with source/mapping evidence. Money uses Decimal; no
browser financial arithmetic. Unknown costs remain unknown and all source and
mapping review rules apply. A preview is review evidence, not approval to buy.

Record requires identical bytes, preview digest, a 10–1000 character reason and
staff acknowledgement in the UI. Reparse/rebind under the existing marketplace
lock and compare a digest of source UUID/profile, latest source binding revision,
candidate source/control/approved-mapping evidence and a rule version. Reject a
stale proposal or replaced source. Commit successor, all rows and audit together.
An exact successful-request retry returns the same successor without a second
audit event, including when that successor has later been replaced itself.

The database independently verifies source ownership, same complete profile and
period/context, a latest predecessor, distinct file bytes, bounded reason/digest
and absence of overlap outside the predecessor ancestry. Root uploads retain
the original overlap guard. New binding revisions on superseded sources are
blocked in both service and database, while historical reads remain available.
API responses expose direct predecessor/successor UUIDs and history can filter
to current source heads. Consumers must select current heads before aggregating
money; history contains superseded versions. Binding revisions never copy money.

## Acceptance and boundaries

Test replacement atomicity/raw integrity, money corrections and unknown costs,
inherited context, stale mappings/binding heads, request replay, multi-version
lineage, concurrent replacements and initial uploads, scoped reads/roles,
database lineage/overlap guards, historical access and revision blocking. Verify
migration roundtrip/drift and browser preview/reason/acknowledgement, stale input
reset, successor navigation and viewer restrictions on desktop/mobile.

Downgrade refuses to discard existing replacement lineage; empty/scratch schema
roundtrips remain supported. No client report upload, live migration/deployment,
Gmail activation or purchasing integration is part of this increment. The October
report year/timezone remain unconfirmed and are never inferred.
