# ConnectBooks Excel profit-and-loss preview

ADR 0024 adds a local, non-persistent preview for the supplied signed USD XLSX
layout. This is separate from ADR 0023's positive-expense CSV contract. The
original file is read unchanged and its SHA-256 is returned. No data is saved to
PostgreSQL, no inventory changes and no recommendation or purchasing action runs.

## Run locally

From backend, activate the backend environment and run:

```bash
python -m app.cli.connectbooks_preview --profile /private/profile.json --xlsx /private/original.xlsx
```

PowerShell from backend:

```powershell
..\.venv\Scripts\python.exe -m app.cli.connectbooks_preview `
  --profile C:\private\profile.json `
  --xlsx C:\private\original.xlsx
```

Copy examples/connectbooks-preview-profile.json outside the repository and set
the exact source Store label separately from the canonical Amazon store name.
The example's store names are synthetic. Supply the explicitly confirmed month,
USD currency and marketplace. Leave year/timezone null until known; the preview
then reports the missing context without creating reporting timestamps.
Advertising coverage remains unconfirmed unless the operator has verified it.
Zero Ad Spend in an export does not establish that advertising costs are complete.

Output contains private financial data and identifiers. Keep it in the same
controlled location as the original workbook; do not commit it or the workbook.
Successful execution, including REVIEW_REQUIRED, exits 0. Invalid input/profile
or an unsupported file layout exits 2 with a safe error code. Profile size is
limited to 64 KiB and duplicate JSON keys are rejected.

## Source mapping and calculations

Required sheet name is explicit. Header row is worksheet row 1. Supported financial
headers must appear exactly once after normalization; unrelated duplicated
Category or Fulfillment Channel headers are allowed. Reordering columns does
not change the mapping. The output reports the selected source column letters.

| Source header | Meaning in this preview |
|---|---|
| Item | Exact text seller SKU, not a description-based product match |
| Item ID | Reported ASIN; remains unbound |
| Store | Must equal the explicit source Store label on every detail row |
| Sales Qty / Refund Qty / Total Units | Whole seller-SKU quantities; refund quantity is signed |
| Sales / Refunds / Adjustments / Net Sales | Signed period totals; net includes refunds and adjustments |
| Fees | Signed aggregate fees, including the source's shipping/fee breakdown |
| Cost | Signed period cost; returned-cost credits remain negative |
| Ad Spend | Reported period advertising cost; coverage is separately disclosed |
| Profit | Reported profit, independently checked against signed inputs |
| ROI / Profit Margin % | Reported fractional ratios, exposed as percent separately |

Computed profit = Net Sales + Fees - Cost - Ad Spend. Do not additionally deduct
Shipping Desk, Veeqo Shipping, Marketplace Commission or other fee-detail columns.
Net Sales is checked against Sales + Refunds + Adjustments. Computed margin uses
positive Net Sales; computed ROI uses positive Cost. Undefined denominators remain
null, even if the export reports a rounded zero ratio. Monetary precision is
retained until presentation; control totals have a one-cent rounding tolerance.
These are source reconciliation rules, not proof of complete economic costs,
tax treatment or an approved purchasing ROI definition.

Exactly one unlabeled final control row is required. All supported additive
fields are compared with the sum of detail, including unallocated amounts.
Missing/invalid detail or summary values produce incomplete controls, never zero.
Rows after the control row, missing required columns, ambiguous mapped headers,
duplicate cell coordinates or invalid row numbering are rejected.

## Review and identity handling

- Unitemized is an unallocated financial bucket with seller_sku null. Keep it in
  accounting totals and out of product matching and SKU recommendations.
- A positive Net Sales row with zero Cost gets ZERO_COST_WITH_POSITIVE_SALES.
  Preserve source profit for reconciliation, but do not assume the product was free.
- All duplicate seller SKUs are invalidated. Do not aggregate or choose one.
- A numeric, boolean or padded Item value is not silently converted into a SKU.
- Missing costs remain unknown; malformed numeric cells invalidate the row.
- Every SKU is UNRESOLVED and every row/report has purchasing_eligible false,
  regardless of arithmetic status. RECONCILED_PREVIEW only describes the checks.

The reader accepts the supplied Open XML layout with numeric detail cells and
numeric or formatted USD summary totals. It rejects formulas (including cached
values), unsupported XML declarations/encodings, external worksheet relationships,
encrypted packages and malformed archives. Limits: 8 MiB compressed, 64 MiB
expanded, 1,024 ZIP entries, 10,000 data rows plus header/control, 256 columns and
1,000,000 explicitly stored cells. It makes no external requests.

## Remaining integration

Before persistence, require a confirmed reporting year and timezone, approved
tenant-scoped seller-SKU bindings, complete cost review, raw-file storage,
overlap/replay rules and audited immutable financial snapshots. Staff API/UI and
links to replenishment remain later increments of the authorized Milestone 2 work.
