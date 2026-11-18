# Milestone 2 client confirmations

Status: request prepared; **not sent**. No reply, policy approval or sign-off is
implied by this template. The already supplied October workbook does not need
reformatting or replacement just to answer these questions.

## Short message to Pinchas

Hi Pinchas, I’m preparing the Milestone 2 review using your October ConnectBooks
file. I have USD, Item = Amazon SKU, and Brand Name Deals-Amazon as the store.
Could you confirm the reporting year and the timezone used for that report,
whether its product costs and advertising are complete or anything is missing,
and three Amazon SKUs you’d like us to validate first? For those items, please
also share your usual demand window, target coverage, lead time, safety stock,
pack/order units and supplier minimums/terms, or we can review those together.
Please confirm whether target coverage already includes lead and safety days.
Anything unknown can stay marked unknown; I’ll keep those gaps visible.

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
| Known inbound and arrival evidence, if any | Unconfirmed | Unconfirmed | Unconfirmed |
| Evidence reference / confirmer | | | |

Stock, price and order units can differ. Do not infer one from the other or from
the product description. Lead time and safety are additive in the implemented
coverage formula; if the client's target includes them, resolve that policy
interpretation explicitly before manager approval. For supplier minimums, state
whether they apply to this SKU or a mixed basket. Current checks support explicit
SKU scopes only. Positive inbound/FBM arrival handling remains an adapter limit
even if the client supplies dates; see [acceptance limits](milestone-2-acceptance.md).

After confirmation, the developer resolves exact scoped identities, source
files/profile versions and complete listing/pool sets, then presents the policy
for manager review. Completing this worksheet alone does not create an approved
policy, mark costs complete, authorize purchases or accept the milestone.
