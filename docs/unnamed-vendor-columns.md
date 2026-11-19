# Explicitly mapped unnamed vendor columns

Status: merged in PR #23 at `6a33741c9d6a99c7673d1cf16e6dc5e7af840d41` and
deployed by [release #229](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/44435083353).
All 1,436 backend and 23 browser release checks passed. The live HAZ source uses
the reviewed seven-column v2 profile. Gmail authorization and manual/scheduled
mailbox checks succeeded; polling and HAZ are enabled. First-email/file and
duplicate replay acceptance remain pending.

## Problem and scope

A vendor workbook can have a populated condition column after its last named
header. The profile explicitly maps that column by its zero-based index, but
the previous reader trimmed its blank header and then discarded the row cells.
Distinct conditions can consequently derive the same offer key.

Correct the shared vendor reader and profile-to-reader adapter in Milestone 1.
Retain only trailing header positions explicitly mapped to non-ignored fields,
and only positions actually present in the source header record. Do not invent
headers, widen a missing source column, infer a mapping or modify raw files.
Default readers and profiles that do not map an unnamed column keep their
current behavior. No schema, dependency or Milestone 2 change is required.

## Acceptance checks

- Synthetic XLSX and CSV previews preserve explicitly mapped unnamed cells;
  ordinary trailing padding and unmapped extra cells are still discarded.
- Absent required columns remain errors. An entirely blank header is refused.
- Two otherwise identical synthetic offers with different conditions have
  different derived keys and retain shared-barcode review requirements.
- Preview and persisted import use the same width rule. Old six-column pins
  refuse the corrected seven-column layout until a new profile version is
  reviewed and pinned; old files, jobs, snapshots and profile versions are not
  rewritten or re-keyed.
- A private real-workbook probe reports structural/count evidence only. Client
  originals, addresses, financial values and credentials are not committed.

## Activation sequence

Verify CI, deploy the isolated Milestone 1 correction, validate the original
workbook and create a new header-pinned profile version. Review its conditions,
duplicate/shared-barcode warnings and source routing before enabling HAZ.
Separately verify the Gmail grant and one authorized email arrival/replay before
claiming unattended imports work. A prepared source remains paused meanwhile;
old emails are not backfilled merely by creating the future-mail rule.

## Verification so far

The private supplied workbook probe retains conditions on all 717 product rows
and derives 717 distinct offer keys. It reports 703 OK rows, 14 warning rows
(eight shared-barcode review warnings and six invalid-barcode warnings), and
87 skipped formatting/section rows. It has no file-level mapping issue under
the corrected seven-column pin. This is a read-only parser probe, not a live
import, approved mapping, successful mailbox check or client acceptance.

The live HAZ attachment source now uses the newly validated v2 profile and is
enabled for new matching emails. The refreshed grant passed the exact-mailbox
check in the deployed container. A manual poll and a later scheduled poll both
completed successfully, with no deliveries yet. No existing inventory file,
historical offer key or old profile was rewritten; no new file import or replay
acceptance is inferred from those successful empty mailbox checks.
