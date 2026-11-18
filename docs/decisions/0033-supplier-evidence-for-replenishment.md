# 0033. Supplier evidence for saved replenishment evaluations

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Extends:** ADRs 0030–0032

## Decision

Add optional explicit supplier settings to purchasing-policy definitions and the
staff editor/review. Bind a particular vendor import-profile UUID, a maximum age,
source-quantity-to-base-unit factor, conversion/availability evidence and an
explicit FILE_RECEIPT_TIME freshness basis with confirmation. Missing settings
remain unknown. Defaults do not add business assumptions. Serialize absent
supplier settings exactly as before so old approval definitions/digests and saved
v1 evaluations remain unchanged and readable.

Manager review retains the bound profile's complete extraction rules, version,
vendor ownership and current vendor constraints. Source/profile/constraint changes
invalidate older approval evidence. Profile selection is bounded and explicit;
pack_size is not copied into a conversion automatically.

Read the latest vendor import job by creation time, without falling back to old
stock. Refuse ties, pending/running/failed/partial jobs, changed profiles, missing
latest-file SKU, duplicate/foreign/conflicting rows, invalid normalized values,
unconfirmed unit conversion and stale/future/impossible source chronology. Require
matching snapshot, staged row, file/vendor/profile/version and active approved
identity. Retain source UUIDs/digests and safe quantity/availability evidence;
never copy raw source rows or change original files. Read under shared row locks.

The importer stores quantity as stated. Apply the explicitly reviewed factor;
never infer it from a description, vendor order-unit factor or current pack size.
Case profiles must also agree with their frozen fixed/row pack evidence. Known
zero, unavailable, available-with-unknown-quantity and fractional/invalid quantities
retain distinct outcomes. Validate counts and source timestamp chronology.

FILE_RECEIPT_TIME checks age since the retained file was received, not the
supplier's actual data timestamp. Require explicit manager confirmation of that
proxy and expose the basis in every result. Uploading an old supplier file cannot
prove its actual age; operational acceptance must confirm that feed contract.

## Saved calculation version and limits

Policies with supplier settings produce replenishment-evaluation-v2 snapshots,
including supplier evidence plus independently checked Amazon data. Reuse exact
quantity-preview-v1 arithmetic and vendor capping. Preserve every v1 snapshot and
its original unknown-supplier behavior. Extend status filtering/schema guards to
REVIEW_REQUIRED, with downgrade refusing to discard v2 history or settings.

Quantity-ready positive results still require review because profitability is
not integrated. Vendor monetary minima and other commercial terms remain explicit
review reasons. Multiple positive pools share supplier stock and require allocation
review; do not sum per-pool capped suggestions into a purchase. All snapshots stay
purchasing_eligible=false, with no purchase execution or automatic acceptance.

## Acceptance and boundaries

Cover legacy definition/digest/record replay, explicit settings and permissions,
profile/constraint changes, real import provenance, newest bad/missing/tied feed,
scoping, quantity/availability conflicts, timestamp age/chronology, shared stock,
unit factors, cap/MOQ, stale preview/retry/audit history, migration roundtrip/drift
and editor/review desktop/mobile behavior. Publish source and run full CI.

No client policy/defaults, live file upload, source sync, financial acceptance,
merge or deployment is included. Gmail remains paused and M1 acceptance stays open.
