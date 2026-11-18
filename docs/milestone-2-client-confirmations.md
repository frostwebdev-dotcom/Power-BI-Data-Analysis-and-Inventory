# Milestone 2 client confirmations

Status: request prepared; **not sent**. No reply, policy approval or sign-off is
implied by this template. The already supplied October workbook does not need
reformatting or replacement just to answer these questions.

## Short message to Pinchas

Hi Pinchas, I’m preparing the Milestone 2 review using your October ConnectBooks
file. I have USD, Item = Amazon SKU, and Brand Name Deals-Amazon as the store.
Could you confirm the reporting year and timezone, whether product costs and
advertising are complete or anything is missing, and three Amazon SKUs you’d
like us to validate first? For those items, we can review your demand window,
coverage, lead/safety days, pack/order units and supplier minimums/terms together.
Please confirm whether coverage already includes lead and safety. If they have
outstanding shipments, please include the complete quantities, shipment
references and expected arrival dates with timezone. Unknowns can stay marked
unknown; I’ll keep those gaps visible.

## Confirmation worksheet

Fill known facts only. Leave missing numbers blank and use Unconfirmed for
unresolved decisions. A confirmed zero needs a source/reference.

| Item | Client answer | Confirmation reference / date |
| --- | --- | --- |
| October reporting year | Unconfirmed | |
| Timezone used by ConnectBooks report | Unconfirmed | |
| Exact report/account/store and original file | Confirm against supplied original | |
| Product cost coverage and known omissions | Unconfirmed | |
| Advertising coverage and known omissions | Unconfirmed | |
| Freight, taxes or fees not included in report/cost | Unconfirmed | |
| Reviewer / purchasing manager | Unconfirmed | |

The developer converts the confirmed reporting timezone to a supported timezone
identifier and shows the resulting local/UTC period for review. The client does
not need to provide internal UUIDs or database fields.

| Per-item decision | Case A | Case B | Case C |
| --- | --- | --- | --- |
| Exact Amazon seller SKU | | | |
| Usual supplier and exact supplier SKU | | | |
| Other supplier for the same product, if available | | | |
| Demand window: 7, 14 or 30 days | | | |
| Shipped-only demand acceptable? | Unconfirmed | Unconfirmed | Unconfirmed |
| Target coverage days | | | |
| Does target already include lead / safety? | Unconfirmed | Unconfirmed | Unconfirmed |
| Supplier lead-time days | | | |
| Safety days | | | |
| Base unit definition (each, etc.) | | | |
| Units per Amazon selling pack | | | |
| Units per supplier stock count / order unit / priced unit | | | |
| Which listings share physical FBA / FBM stock? | Unconfirmed | Unconfirmed | Unconfirmed |
| Minimum order quantity and its scope | | | |
| Order multiple | | | |
| Minimum goods value, currency and its scope | | | |
| Supplier price tax treatment / discount / terms | Unconfirmed | Unconfirmed | Unconfirmed |
| Priority when pools share limited supplier stock | Unconfirmed | Unconfirmed | Unconfirmed |
| Acceptable sales / inventory / supplier receipt age | Unconfirmed | Unconfirmed | Unconfirmed |
| Supplier file receipt-age proxy acceptable? | Unconfirmed | Unconfirmed | Unconfirmed |
| Complete outstanding inbound total and its unit basis; explicit zero only if verified | | | |
| Shipment references, quantities/units and expected arrivals with timezone | | | |
| Position observation time and original evidence reference | | | |
| Complete position and no overlap with on-hand stock / another pool confirmed? | Unconfirmed | Unconfirmed | Unconfirmed |
| Manager validity/expiry decision, at most 31 days after observation | Unconfirmed | Unconfirmed | Unconfirmed |
| Evidence reference / confirmer | | | |

Stock, price and order units can differ. Do not infer one from the other or from
the product description. Lead time and safety are additive in the implemented
coverage formula; if the client's target includes them, resolve that policy
interpretation explicitly before manager approval. For supplier minimums, state
whether they apply to this SKU or a mixed basket. Current checks support explicit
SKU scopes only.

Reviewed inbound is implemented on the draft branch. FBA positions must reconcile
to native working/shipped/receiving totals after the approved conversion. FBM
requires an explicit complete manual position; no native FBM inbound feed is
implied. Dates alone do not prove completeness or authorize a confirmation.
The developer resolves source references, and a manager reviews the complete
position and expiry before an exact revision is selected for a calculation.
See [reviewed inbound](reviewed-inbound.md) and the
[acceptance walkthrough](milestone-2-acceptance.md).

After confirmation, the developer resolves exact scoped identities, source
files/profile versions and complete listing/pool sets, then presents the policy
for manager review. Completing this worksheet alone does not create an approved
policy, mark costs complete, authorize purchases or accept the milestone.
