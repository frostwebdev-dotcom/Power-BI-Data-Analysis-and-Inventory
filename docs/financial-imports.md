# Recorded financial imports

ADR 0025 adds database recording and staff APIs around the signed XLSX preview.
These financial tables are separate from vendor stock jobs; recording an import
does not change inventory, Amazon data, mappings or purchasing recommendations.
Every recorded row remains purchasing_eligible false.

## Reporting context and original bytes

The multipart upload contains file (an original XLSX) and profile_json (the
ConnectBooksXlsxProfile JSON described in connectbooks-xlsx-preview.md). Set the
client-confirmed reporting_year and reporting_timezone as well as month, USD,
marketplace and canonical/source store names. Missing year/timezone is rejected
before storage or database writes. File creation timestamps are not used.

Calendar-month bounds are converted from local midnight to UTC, with an exclusive
end. DST, leap years and December rollover are handled; ambiguous/nonexistent
midnight boundaries are rejected rather than guessed. The original October file
has not been persisted: its reporting year/timezone still need confirmation.

The storage backend retains unchanged source bytes before parsing. A parse or
database failure can leave an unreferenced retained object; it is not deleted.
The database records its URI, SHA-256, sanitized original filename and receipt
time. Raw download rechecks the digest. A replay may restore a missing raw object
from identical uploaded bytes, but never overwrites corrupted retained data.

## Staff API

All paths below are under /api/v1 and require authentication.

| Method and path | Access | Result |
|---|---|---|
| POST /financial-imports | DATA_OPERATOR or ADMIN | Retain, validate and record atomically; 201 new or 200 exact replay |
| GET /financial-imports | Existing read roles | Paginated organization history |
| GET /financial-imports/{id} | Existing read roles | Context, original pre-binding controls, linkage/review counts |
| GET /financial-imports/{id}/rows | Existing read roles | Paginated immutable rows; review_only and mapping_status filters |
| GET /financial-imports/{id}/raw | Existing read roles | Integrity-checked original XLSX download |

Page size is bounded at 200. Unknown and foreign-organization IDs return the
same 404 for detail, rows and raw access. No update/delete endpoint is provided.
Invalid profiles, duplicate JSON keys and unsupported files return safe error
codes without source cells. The staff UI is a later increment; these routes are
not deployed by the development change.

## Mapping and review

Only an exact, case-sensitive seller SKU in the same organization/marketplace,
on an active APPROVED Amazon listing with an active product, is linked. The
transaction takes shared locks on those mapping/product rows while capturing
priority 4, UUIDs, approver, approval time and original mapping method. A source
ASIN conflict prevents linking. Names/descriptions/ASIN similarity never select
a product. The existing mappings themselves are not modified.

Financial row mapping_status is LINKED, UNRESOLVED, UNALLOCATED, INVALID_SOURCE
or ASIN_CONFLICT. Non-linked rows and source warnings form the financial review
queue, accessible through row filters. Unitemized remains in accounting totals
without a seller SKU/product link. Zero or unknown cost warnings remain visible
even if a SKU has an approved mapping. Money uses NUMERIC(38,16); the complete
pre-binding source row is also retained as decimal-string JSON.

Batch status is REVIEW_REQUIRED when any row or source control requires review;
RECORDED means the supported arithmetic and linkage checks passed. It does not
approve economic cost coverage or buying. review_rows counts row-level review
only: batch-level issues such as advertising coverage or a footer mismatch are
in report_snapshot and can exist with review_rows zero. Consumers must show
batch status and those controls as well as the row queue.

## Replay, overlap and immutability

An organization plus original-byte SHA-256 identifies a replay. The whole
profile hash must match; changing month, timezone, store or coverage assertions
does not relabel an old record. Exact replay returns the original snapshot with
no second audit entry and no new product bindings, even if mappings changed.

Overlapping periods for an organization/marketplace are rejected. Current SKU
mapping supports one Amazon account in that namespace; varying the display store
name cannot bypass the overlap rule. Adjacent half-open months are allowed.
Concurrent requests serialize with a transaction lock, and database guards
independently check overlap, uploader ownership and approved link ownership.

A batch, all its rows and its audit entry commit together. Database triggers
block updates/deletes and later row appends; a deferred constraint checks stored
row/link/review counts against the complete row set. Referenced historical
products/listings/users cannot be deleted out from under snapshots. Changes to
live mappings do not rewrite historical evidence.

Replacement reports, re-binding after mapping approval and corrected cost data
need an explicit audited supersession/revision workflow, which is not part of
this increment. Do not work around a conflict by altering dates or store labels.
