# Milestone 2 — Amazon analytics, profitability and replenishment

Owner instruction, 11 November 2026: "I would like to start Milestone 2 first."
Gmail vendor-intake setup remains paused and Milestone 1 acceptance remains open.
This authorizes development, not a revised client price, deadline or acceptance.
The September proposal contains superseded dates and proposed prices; none is
silently carried into a new commitment here.

## Deliverables

1. Reuse and validate existing read-only Amazon ingestion, SKU mapping, 7/14/30-day
   velocity, FBA/FBM inventory, inbound inventory and source freshness evidence.
2. Import a real ConnectBooks CSV with explicit column and reporting-period
   mappings. Associate price, costs and fees with approved product/SKU identities;
   report profit, margin and ROI without treating unknown costs as zero.
3. Explainable replenishment previews with configurable velocity window, target
   coverage, lead time, safety days, MOQ, order multiples and vendor constraints.
4. Vendor offer comparison with known unit costs, known/unknown availability and
   traceable source timestamps. Unknown available quantity remains unknown.
5. Urgent replenishment, replenishment opportunities and newly available products
   in the application, with search/filtering and reproducible calculation inputs.
6. A real-data walkthrough and acceptance record after the full flow is wired.

Budget optimization, RFQs, outgoing email, quote processing, purchase orders,
Analyzer.tools, Power BI and Walmart remain outside Milestone 2. There are no
automatic purchases or writes to Amazon in this milestone.

## First deliverable: isolated quantity-preview foundation

ADR [0022](decisions/0022-start-milestone-2-replenishment-preview.md) starts with a
pure, non-persistent calculation and a local JSON-input CLI. It does not query
customer data, change inventory, run on a schedule, or publish an API or UI.
It can be completed without Gmail or a ConnectBooks export. It does not claim
the whole replenishment engine or Milestone 2 is complete.

All quantities must be in the same confirmed base unit. The future data adapter
must prove approved identity mappings, stock normalization, freshness and
non-overlapping inventory pools before passing readiness flags. In particular,
shared FBM stock must not be summed across multiple listings and multipack sales
must not be compared to individual pieces without a confirmed conversion.

### Formula for this preview

Every policy field is explicit; there are no customer-wide purchasing defaults.
The preview uses the **additive coverage policy**:

```
daily demand = units sold / selected window (7, 14 or 30 days)
coverage days = target days + vendor lead-time days + safety days
required units = ceiling(units sold * coverage days / window days)
shortfall = max(0, required units - confirmed on hand - inbound)
order multiple rounding = round max(shortfall, MOQ) up to an order multiple
```

MOQ and rounding only apply when shortfall is positive. Known vendor stock caps
the preview down to an order multiple; a cap below MOQ cannot produce a valid
purchase quantity. Partial stock and unknown vendor quantity require review.
Unknown vendor availability, unapproved mappings, unconfirmed units, stale data
and missing demand/on-hand/inbound block a usable suggestion. Unknown values
are never converted to zero. Zero demand has no days-of-supply estimate.

Adding lead time to target coverage is an explicit first-preview policy, not a
claim that the client has approved this interpretation. Before live purchasing
recommendations, confirm whether target days already includes lead time and
safety days. Inbound timing and separate shared inventory pools must also be
resolved in the live adapter; subtracting all inbound does not prove that it
arrives before stockout.

### Acceptance examples

| Inputs | Expected result |
|---|---|
| 280 sold / 14 days, 50 on hand, 20 inbound, 14 target, 0 lead, 0 safety | 20/day; 280 required; 210 shortfall; 210-unit preview |
| Same demand, 2 lead days, 1 safety day | 17 coverage days; 340 required; 270 shortfall |
| 1 sold / 7 days, 8 coverage days, zero stock | 2 required units, with no floating-point off-by-one |
| Positive shortfall, MOQ 100, order multiple 12 | Round to a valid multiple meeting MOQ |
| Known vendor stock below MOQ or below one order multiple | Block suggestion |
| Vendor says in stock but supplies no quantity | Keep quantity unknown; mark preview for review |
| Missing or stale inputs / unapproved mapping / units unconfirmed | Block suggestion with explicit reasons |

Tests must also cover zero demand, overstock, inbound exceeding demand, negative
and malformed input, JSON reproducibility, and no mutation of inputs.

### Reproduce the synthetic example

This uses synthetic, explicit inputs, not a live customer inventory query. The
readiness flags in the example are fixture assumptions only. From `backend/`,
with the backend environment active:

```bash
python -m app.cli.replenishment_preview < examples/replenishment-preview.json
```

PowerShell, also from `backend/`:

```powershell
Get-Content -Raw .\examples\replenishment-preview.json |
  ..\.venv\Scripts\python.exe -m app.cli.replenishment_preview
```

The output includes `preview_only: true`, `data_source: explicit_inputs`, the
complete validated request, the rule version, calculation breakdown, status and
reasons. The example must return `READY` and `suggested_units: 210`. A blocked
or review-required calculation is still a successful preview execution (exit 0);
invalid or oversized JSON exits 2. Neither result is approval to place an order.
Decimal display ratios are serialized as strings; integer quantities remain
JSON numbers. No credentials, network connection or database are required.

## Dependencies before the next deliverables

- A real ConnectBooks CSV, with reporting dates, currency and per-unit versus
  period-total cost semantics. Do not fabricate a production import mapping.
- Purchasing-manager decisions on target coverage, velocity window, lead time,
  safety stock, pending-order demand and acceptable inbound timing.
- Confirmed listing/pack conversion and shared-stock rules.
- A freshness policy and regression evidence for three real SKUs.
- Original vendor sender addresses and a working unattended Gmail grant before
  claiming live vendor availability is automatically refreshed.

Missing profitability is shown as unavailable; quantities are previews requiring
review until these dependencies and the complete integration are accepted.
