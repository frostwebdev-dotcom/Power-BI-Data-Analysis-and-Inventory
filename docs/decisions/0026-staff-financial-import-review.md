# 0026. Staff financial upload and review screen

- **Status:** Accepted for the next Milestone 2 increment
- **Date:** 2026-11-17
- **Deciders:** Owner instruction to continue after the financial database/API layer
- **Supersedes / Superseded by:** Extends ADR 0025

## Decision

Add Financial imports to the authenticated navigation. The page uses ADR 0025's
existing API for paginated history, upload, batch detail, review rows and original
file download. DATA_OPERATOR and ADMIN can upload; read roles inspect existing
records. No database or backend calculation changes are needed.

The upload form asks for the source workbook, canonical/source store labels,
worksheet, marketplace and report month/year/timezone. Reporting year/timezone
start blank; do not infer them from today's date, the filename or browser zone.
USD is the supported currency. Advertising completeness is an explicit unchecked
assertion; leaving it unchecked preserves the batch warning. Reject empty,
oversized or non-XLSX files before sending; the API remains authoritative.

Detail shows the confirmed report period, row/link/review counts, every financial
control and batch-level issue, plus paginated/filterable row review. Batch-level
review must remain visible when row review count is zero. Financial strings are
formatted without Number conversion or browser arithmetic, preserving precision
and unknown values. Unitemized is never displayed as a linked SKU. A row drawer
shows source amounts, reported/computed ratios, mapping evidence and issues.

Reuse the authenticated API client, query cache and presentation components.
No secrets, raw JSON editing, file parsing or financial calculation belongs in
the screen. Downloads use the authenticated raw endpoint. Duplicate upload opens
the existing record with a clear notice; overlap errors explain the replacement
workflow limitation. There are no edit/delete, mapping approval or buying actions.

## Acceptance

Browser checks cover upload context without guessed dates, multipart submission,
successful and duplicate records, batch warnings with zero row issues, unknown
amounts and signed precision, review pagination/filter reset, viewer restrictions,
download/error handling and a synthetic upload against the real API. Verify
desktop/mobile layouts. Existing backend and browser regression checks must pass.
Do not upload the owner's workbook or deploy the draft branch as part of this work.
