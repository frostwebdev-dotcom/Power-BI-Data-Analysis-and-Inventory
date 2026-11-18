# Supplier evidence in replenishment evaluations

Supplier-aware evaluations are opt-in through a new purchasing-policy draft and
manager approval. In Purchasing policies, enable **Include supplier source
checks**, choose the supplier inventory profile and enter the age limit,
freshness basis, source quantity conversion and evidence references explicitly.
Blank conversions/references can be retained in drafts but block approval.

| Setting | Meaning |
|---|---|
| Inventory profile | Exact versioned layout UUID belonging to the supplier |
| Maximum file receipt age | Age in seconds since PRMS received the retained file |
| Freshness basis | FILE_RECEIPT_TIME, explicitly confirmed by the manager |
| Base units per source quantity | Multiplier for the file's as-stated stock count |
| Quantity evidence | Confirmation of the source column's units |
| Availability evidence | Confirmation of the profile's quantity/status/listed interpretation |

Receipt time does **not** verify when the supplier prepared its data. The current
importer sets snapshot.effective_at to file.uploaded_at; every supplier result
labels that basis and supplier_effective_time_verified=false. Confirm that feed
contract before operational use. An old retained file cannot be renewed by
retrying its import or updating a vendor product's last-seen timestamp.

The API equivalent is an optional supplier object inside the existing complete
policy definition. All seven settings above are required in that object, with
explicit null permitted for unknown profile/conversion/evidence. Omit the object
to retain legacy behavior. Absent/null supplier objects serialize without a new
key, preserving old approval snapshots and content digests. New settings are
append-only policy revisions, never updates to an existing approval.

## Source checks

Evaluation reads only ingested PostgreSQL. It takes shared row locks, selects
the latest supplier import job by creation time and refuses an ambiguous tie.
There is no older-file fallback for a missing SKU, bad/incomplete run or different
profile. This deliberately conservative vendor-wide rule can block independent
supplier feeds; distinct feed partitioning is not inferred.

The job must be clean COMPLETED with consistent parsed/matched/skipped counts.
The file, profile/version, staged row and inventory snapshot must all belong to
the organization/vendor and exact approved product/supplier SKU. Missing,
duplicate, conflicting or foreign references block the result. Profile rules,
activity and supplier commercial constraints are included in manager review;
changes require another approval, even when the prior approval still exists.

Quantity/availability are cross-checked against staged normalized evidence and
the snapshot. Fractional, negative, coerced, missing-provenance and conflicting
quantities are refused. The importer stores quantities as stated: apply only the
reviewed source factor, never the vendor ordering factor or a description. Case
profiles also must agree with fixed/row pack evidence. Source and snapshot times
must follow receipt/start/completion chronology and must not be in the future.

## Outcomes and saved versions

| Evidence | Quantity result |
|---|---|
| Known available quantity and confirmed units/current source | Exact vendor cap and order-multiple/MOQ arithmetic |
| Available with unknown quantity | REVIEW_REQUIRED; potential quantity is uncapped and remains for review |
| Known unavailable / zero available | No usable positive suggestion |
| Stale, ambiguous, conflicting or unproven source | BLOCKED; unknown remains unknown |
| Clean zero shortfall | NO_REORDER |

Policies with supplier settings produce replenishment-evaluation-v2. The saved
snapshot includes supplier job/file/profile/row/snapshot references, file digest,
safe normalized quantity evidence, receipt-age basis, readiness issues and
replayable per-pool calculator inputs/results. No raw rows or storage credentials
are copied. Existing v1 records remain readable with their original unknown
supplier inputs; nothing enriches history retroactively.

A per-pool READY result means quantity arithmetic passed. Positive saved
evaluations remain REVIEW_REQUIRED because profitability is not integrated.
Supplier monetary minima and other commercial terms remain review reasons.
Multiple positive pools share supplier stock and need allocation review; their
separate capped suggestions must not be added into an order.

The same preview/record/retry/history API is used; status filtering adds
REVIEW_REQUIRED. Record recomputes supplier and Amazon evidence before accepting
the preview digest. Exact successful retries retain the original version and
observation. Migration a61117202606 accepts both snapshot versions and refuses a
downgrade that would discard supplier settings or v2 history. All outputs remain
purchasing_eligible=false. No source polling, financial import, purchase, merge
or deployment is included.
