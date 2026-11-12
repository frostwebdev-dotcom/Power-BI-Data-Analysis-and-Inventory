# Milestone 2 financial CSV preview

This is a local, non-persistent preview under ADR 0023. It validates explicitly
mapped CSV period totals and calculates metrics. It is not a completed
ConnectBooks connection, production import profile, database import or purchase
recommendation. A real ConnectBooks export is still required before its actual
columns and financial semantics can be accepted.

## Reproduce the synthetic fixture

From `backend/`, with the backend environment active:

```bash
python -m app.cli.profitability_preview \
  --profile examples/profitability-preview-profile.json \
  --csv examples/profitability-preview.csv
```

PowerShell, from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m app.cli.profitability_preview `
  --profile .\examples\profitability-preview-profile.json `
  --csv .\examples\profitability-preview.csv
```

The fixture is synthetic and deliberately labeled accordingly. Its headers are
examples, not asserted ConnectBooks headers. The SKU `0000123` remains text.

| Period total input | Amount (USD) |
|---|---|
| Net revenue after refunds, excluding sales tax | 1,000.00 |
| Cost of goods sold | 400.00 |
| Amazon fees | 150.00 |
| Shipping | 50.00 |
| Advertising | 20.00 |
| Other costs | 0.00 |

For 10 seller-SKU units, the result is net profit **380.00**, margin **38%**,
ROI on COGS **95%**, revenue per seller-SKU unit **100.00**, cost per seller-SKU
unit **40.00**, and profit per seller-SKU unit **38.00**.

The output always states `preview_only: true`, `persistence_performed: false`
and `identity_resolution_performed: false`. Source SHA-256, profile version,
period, marketplace, currency, physical row numbers, parsed amounts and issue
codes make the preview reproducible. Product identities remain `UNRESOLVED`;
this never creates or approves mappings.

## Explicit profile contract

- `name`, positive integer `version`, and `source_name` identify the mapping.
- `marketplace_id` and uppercase three-letter `currency` describe this file.
- `period_start` and `period_end` require timezone offsets and normalize to UTC;
  start is inclusive and end exclusive. The whole file must describe that one
  period. Do not infer these dates from the download time or filename.
- `values_basis` must be `period_totals`; `revenue_basis` must be
  `net_after_refunds_excluding_tax`; `expense_basis` must be
  `positive_expense_totals`; `quantity_basis` must be `seller_sku_units`.
- `columns` maps canonical fields to actual header names. Seller SKU must be
  mapped. Other absent fields are reported as unknown, not guessed. Normalized
  header matching uses the shared reader's case/whitespace rules. Duplicate or
  blank headers, reused mapped columns and missing mapped headers are rejected.
- Supported numeric fields are `units_sold`, `net_revenue`, `cost_of_goods`,
  `amazon_fees`, `shipping`, `advertising` and `other_costs`. An optional mapped
  `currency` column must agree with the declared file currency on every row.
- `confirmed_zero_costs` explicitly names expenses known to be zero that have
  no mapped column. A mapped blank cell is still unknown. A field cannot be
  both mapped and confirmed zero. Confirm this with the data owner; absence of
  an expense column is not evidence of zero expense.
- Encoding, delimiter, decimal separator and grouping separator are explicit.
  Grouped numbers must have proper groups of three digits. At most 18 integer
  and 6 fractional digits are accepted. Currency symbols, percentages,
  scientific notation, parentheses and negative expense totals are unsupported
  in this first format; they require a reviewed mapping change, not guessing.
- `header_row_index` is zero-based and can explicitly skip report-title rows.

Each expense bucket must be non-overlapping. For example, do not map total
Amazon charges plus their shipping/advertising components as additional costs.
The operator must confirm the source report's basis before live ingestion.
SKU-unit amounts may describe multipacks; they are not automatically per-piece
vendor prices. Conversion belongs in the approved live-data adapter.

## Calculation and validation rules

```
net profit = net revenue - COGS - Amazon fees - shipping - advertising - other costs
margin percent = net profit / positive net revenue * 100
ROI percent = net profit / positive COGS * 100
per-SKU-unit amount = period total / positive units sold
```

These are versioned preview definitions and need confirmation against the real
ConnectBooks report and client's ROI definition. There is no currency conversion
or imputation. Negative revenue and losses remain negative. Missing expenses
make net profit, margin and ROI unknown. Zero/nonpositive denominators produce
undefined metrics with notices, never infinity or a fake zero.

All repeated seller SKUs in a file are invalidated. A hidden channel/account
dimension may explain a duplicate; do not sum or choose the first row. Invalid
numeric values, missing SKU or mismatched currency suppress all metrics for
that row. Missing units can still permit known period-level profit but cannot
produce per-unit figures. Blank rows are counted, not silently assigned values.

The reader's opt-in strict mode rejects ragged rows and malformed quoting instead
of padding/trimming financial data. Existing vendor-reader defaults are retained.
The limits are 8 MiB, 10,000 data/blank records and 256 headers; exceeding a limit
fails the batch rather than returning a misleading partial success. The profile
limit is 64 KiB, and duplicate JSON object keys are rejected.

Exit 0 means a preview report was produced, including `REVIEW_REQUIRED` reports;
exit 2 means a file/profile/batch validation error. Review row statuses and issue
codes rather than equating exit 0 with a production-ready import.

No database, network call, credentials, inventory changes or file writes occur.
Raw input bytes are hashed and remain unchanged. Persisted imports will require
raw-file retention, organization-scoped approved SKU bindings, period overlap
and replay policy, audit records, and a separately tested API/UI workflow.
