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
midnight boundaries are rejected rather than guessed. For the original October
report, the client has now confirmed 2026 and EST with an explicit UTC−05:00
fixed offset. Use `Etc/GMT+5`; the existing profile validator and reporting-bounds
service produce `2026-10-01T05:00:00Z` inclusive to `2026-11-01T05:00:00Z` exclusive.
See the [recorded context](milestone-2-client-confirmations.md#confirmed-october-reporting-context).
The original report has not been persisted by this metadata verification.

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
| GET /financial-imports/{id}/binding-revisions | Existing read roles | Paginated immutable SKU-link revision history |
| POST /financial-imports/{id}/binding-revisions/preview | DATA_OPERATOR or ADMIN | No-write comparison against current approved mappings; paginated changed rows |
| POST /financial-imports/{id}/binding-revisions | DATA_OPERATOR or ADMIN | Record the reviewed proposal with expected head, digest and reason |
| POST /financial-imports/{id}/replacement-preview | DATA_OPERATOR or ADMIN | Retain/preview a corrected XLSX using the source profile; no database record |
| POST /financial-imports/{id}/replacements | DATA_OPERATOR or ADMIN | Record a reviewed corrected source; 201 new or 200 exact successful-request replay |

Page size is bounded at 200. Unknown and foreign-organization IDs return the
same 404 for detail, rows and raw access. No update/delete endpoint is provided.
Invalid profiles, duplicate JSON keys and unsupported files return safe error
codes without source cells. The staff page at /financial-imports (ADR 0026)
provides upload, history, totals, row review and original downloads on the same
draft branch; neither the page nor the routes are deployed by this change.

## Staff upload and review

Financial imports appears under Ingestion. DATA_OPERATOR/ADMIN see the upload
form; read-only users see recorded history and detail. Enter the source/canonical
store labels, worksheet, marketplace and reporting month/year/timezone. Dates
and zone are not inferred from the browser or filename. Only USD XLSX exports
up to 8 MiB are supported. Advertising completeness is unchecked until confirmed.

Batch detail shows source totals, differences and batch warnings even when the
row issue count is zero. Filter rows by review requirement and mapping status;
changing a filter resets pagination. Details preserve reported/calculated ratios,
unknown amounts, exact decimal precision, source row numbers and mapping evidence.
The original download is authenticated. There are no edit, delete, mapping
approval or purchasing controls. A duplicate opens the existing record; an
overlap error explains that a replacement workflow is still required.

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

## Reviewed SKU-link revisions

ADR 0027 adds a separate append-only revision chain for product links. Original
import rows and amounts remain unchanged. Preview compares every original row
against the latest recorded binding snapshot and the current approved mappings.
It returns expected_revision_id (null for original links), bindings_sha256, full
counts and a paginated list of changes. Preview writes no revision or audit event.

To record, submit those exact head/digest values and a reason of 10-1000
characters. The service locks the marketplace, recalculates against current
approvals, and rejects stale heads, changed proposals and no-ops. All binding
rows and a financial_import.bindings_revised audit entry commit together. A
concurrent save cannot fork the chain. Repeating a request after it succeeds
returns a stale-head conflict; refresh history to inspect the saved revision.

Only existing approved mappings are applied. A link can be gained, lost or
become conflicting when mapping state changes; no product is guessed or mapping
approved here. Every source cost/validation warning survives, Unitemized remains
unallocated, and batch-level warnings keep the revision in review even if its
row-review count is zero. Revisions contain no new financial amounts and remain
ineligible for purchasing. Database guards enforce full coverage, ancestry,
tenant ownership, original transaction insertion and immutable rows/headers.

The staff detail initially shows original links. Select a recorded revision to
inspect its counts, actor, reason and rows; original import counts are labelled
separately. Changing revision resets row filters, pagination and the drawer.
The rows API accepts binding_revision_id and projects original financial amounts
with that revision's binding evidence. Invalid/foreign revision IDs return 404.
Operators preview current links, inspect changes, give a reason and acknowledge
the proposal before saving. A stale proposal is discarded and must be previewed
again. Viewers can inspect history but cannot preview or record revisions.

## Corrected source report replacement

ADR 0028 adds a reasoned preview/record workflow for a corrected XLSX. It creates
a new immutable import with supersedes_import_id, replacement_reason and the
reviewed replacement_preview_sha256. The previous source, all original rows,
raw file and link revisions remain unchanged and accessible. A unique predecessor
constraint and insertion guards prohibit branching. Only the current source head
can be replaced. Reporting periods may overlap inside this explicit ancestry;
ordinary uploads still cannot bypass the original overlap rule.

The full source profile is inherited, including confirmed year/month/timezone,
store/account, currency, worksheet and coverage assertions. This workflow cannot
relabel dates, change the account or change a coverage assertion. Use it for
corrected source contents; context corrections require a separate explicit flow.

POST replacement-preview with multipart file and optional page/page_size. Preview
retains the corrected bytes before parsing but creates no batch or audit event.
It verifies retained original-file integrity, compares old/new report totals
with exact differences, shows replacement detail/control checks, added/removed
seller-SKU counts, full row/link/review counts, batch warnings and candidate rows.
Missing costs/amounts remain unknown; Unitemized stays unallocated. A review
status is permitted but never grants eligibility for purchasing.

POST replacements with the same file/name, preview_sha256 and a reason of
10-1000 characters. Recording reparses and rebinds under the marketplace lock,
checks the source's current binding head and complete proposal digest, then
commits the successor, every row and audit event atomically. Changed bytes,
approvals or source binding head invalidate the preview. A replaced predecessor
cannot receive another successor. An exact successful-request retry returns its
original successor with duplicate true and no second audit event, even if a later
replacement exists. File bytes already recorded elsewhere cannot be reused as
a new version. Stored raw bytes are never overwritten or deleted.

Import responses expose direct supersedes_import_id and replaced_by_import_id;
the latter is derived from immutable successor records. Financial history retains
all versions by default. current_only=true selects source heads and must be used
before aggregating financial amounts; do not sum history across replacements.
The staff screen identifies historical sources, links previous/next reports and
offers a current-only history filter. It requires preview, a reason and explicit
acknowledgement before recording, and discards a changed/stale proposal. Viewers
inspect history but cannot preview/record. Changing the file clears its preview.

New SKU-binding revisions on replaced sources are blocked by both service and
database; existing revisions remain readable. Use the corrected source for new
link reviews. Database downgrade refuses to discard existing replacement lineage;
empty scratch-schema upgrade/downgrade roundtrips remain supported.

This work remains on the draft branch. No client report has been uploaded or
live migration executed. The original October year/timezone input is now resolved
as recorded above; actual file acceptance and cost/advertising review remain open.
Replacements do not approve cost completeness or enable purchasing.
