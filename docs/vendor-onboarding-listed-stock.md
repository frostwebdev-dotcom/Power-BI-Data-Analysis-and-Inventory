# Vendor onboarding: listed stock and legacy Excel

## Confirmed business rules

The client confirmed Bracha for the October 21 inventory workbook, ET for
the ET export, and Universal for the Universal export. The supplied fragrance
lists indicate availability by inclusion. Prices and any provided quantities
are per individual piece. Missing quantities must remain unknown. Ordering
by full boxes does not change the price or stock unit to cases. QKD's existing
12 percent deduction is unchanged; no discount is inferred for other vendors.

## Plan

1. Add an explicit `listed` availability profile mode. Existing profiles keep
   their quantity/status rules and strict validation. Only listed profiles may
   omit the quantity mapping or leave quantities blank. Numeric quantities,
   including zero, are retained as stated; availability follows the configured
   inclusion rule. A bounded quantity such as `240+` remains unknown with a
   warning and its original text is preserved. Malformed or negative quantities
   still fail validation.
2. Accept `.xls` uploads and read legacy Excel by its file signature under the
   existing Excel (`XLSX`) profile setting, including files with misleading
   extensions. Keep original bytes, sheet selection and row numbers unchanged.
3. Validate actual vendor files against data-only per-vendor profiles. Preserve
   item identifiers as text and keep ambiguous UPCs in the exception workflow.
   For files without supplier SKUs, an opt-in UPC-derived vendor line key uses
   only a valid checksummed UPC and is explicitly marked in the row's provenance.
4. Prepare a private onboarding manifest and validation report with source
   checksums. Do not publish customer workbooks or pricing to the repository.
5. Apply only confirmed, unambiguous source/vendor pairs in staging after the
   feature is deployed and the application is signed in. Retain the TJS hold;
   conflicting received file contents must not be imported under a guessed vendor.

## Acceptance checks

- A listed price-list row with no quantity imports as AVAILABLE with a null
  quantity and can create an availability snapshot/watchlist event.
- Default quantity profiles still reject missing, malformed and negative counts.
- Case/each semantics and source price discounts remain unchanged.
- Both Excel encodings work through the same preview and processing reader;
  malformed files, missing sheets and wrong headers remain explicit failures.
- Upload/download retains identical bytes for `.xls`, as for CSV/XLSX.
- Frontend file pickers accept the supported extensions and the profile editor
  explains the Excel choice.
- Run parser/rule regression tests, strict lint/type checks and repository CI.

## Current source holds

The received TJS workbook is byte-identical to the received MTZ workbook. The
received Universal-named workbook identifies ET internally, while another
attachment identifies Universal internally. Those conflicts require reconciliation
before assigning inventory to a vendor. Other confirmed files can proceed.
