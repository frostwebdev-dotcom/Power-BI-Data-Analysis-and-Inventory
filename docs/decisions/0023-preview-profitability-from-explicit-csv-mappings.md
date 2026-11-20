# 0023. Preview profitability from explicitly mapped CSV period totals

- **Status:** Accepted for the second bounded Milestone 2 implementation
- **Date:** 2026-11-11
- **Deciders:** Owner instruction to continue Milestone 2
- **Supersedes / Superseded by:** None; extends ADR 0022's isolated preview boundary

## Context

The first quantity preview is tested in draft PR #22. The owner requested the
next step, but no real ConnectBooks CSV or approved cost definition has been
provided. A guessed production column mapping could silently confuse unit costs
with period totals or count refunded revenue twice.

## Decision

Add a non-persistent CSV profitability preview and local CLI. Reuse the existing
text-preserving reader with opt-in strict row-width and CSV-quoting checks;
vendor import behavior retains its current defaults. A separate typed financial
profile supplies actual column names, encoding, delimiter, decimal/grouping
format, UTC reporting bounds, currency, marketplace and explicit amount bases.
This is not a vendor-stock profile and must never update inventory.

The first supported format is one row per seller SKU, one reporting period,
one marketplace and one currency. Values are period totals; net revenue is
already after refunds and excludes sales tax. Expenses are positive totals.
Missing costs remain unknown unless a specific expense is explicitly confirmed
zero. Mapping two financial fields to one column is rejected to avoid double
counting. Every repeated SKU is flagged, with no automatic first-row selection
or aggregation. Product identities are not resolved in this preview.

Arithmetic uses Decimal and an explicit context. Preview formulas:

- Net profit = net revenue minus COGS, Amazon fees, shipping, advertising and
  other costs. Any unknown component makes net profit unknown.
- Margin percent = profit / positive net revenue * 100; undefined otherwise.
- ROI percent = profit / positive COGS * 100; undefined otherwise.
- Per-unit figures divide period totals by positive units sold only.

These definitions are disclosed and versioned, not treated as client-approved
ConnectBooks semantics. Currency conversion, per-unit input costs, transaction
detail, automatic period selection and overlapping-period merges are excluded.
The source bytes are hashed and never rewritten. No database, API/UI, scheduler,
new dependency or deployment is introduced.

## Alternatives considered

| Option | Why not |
|---|---|
| Assume ConnectBooks column names | There is no real export to establish them. |
| Treat blank costs as zero | Overstates profitability and ROI. |
| Feed financial rows into vendor inventory ingestion | Financial and stock semantics differ; risks changing availability. |
| Aggregate duplicate SKUs | The missing discriminator may be period, channel or account. |

## Consequences

**Positive** — file validation and conservative profitability arithmetic can be
tested before the real export arrives. Financial input stays separate from stock.

**Negative / cost** — this is a preview, not a completed ConnectBooks import.
It deliberately rejects unsupported formats and requires explicit mapping.

**Follow-ups** — inspect a real export, confirm dates/currency/cost semantics
and ROI definition, approve product/SKU bindings, add raw-file persistence,
tenant-scoped period snapshots, duplicate/replay handling, auditing and API/UI.
