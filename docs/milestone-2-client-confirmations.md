# Milestone 2 client confirmations

Status: **partially answered — reporting year/timezone and three SKU selections received**.
The three selections and their mapping review are retained in the private intake
record outside git. Their business inputs and real-case acceptance remain open.
On 18 November 2026,
the owner relayed the client's reply: year 2026 and EST, explicitly UTC−05:00.
The follow-up message below is prepared only; it has not been sent. No policy
approval, real-file import or operational sign-off is implied. The supplied
October workbook does not need reformatting or replacement to answer the
remaining questions.

## Confirmed October reporting context

| Field | Confirmed or derived value | Evidence |
| --- | --- | --- |
| Reporting month / year | October 2026 | October previously confirmed; year supplied in the client reply relayed on 18 November 2026 |
| Client's timezone description | EST, UTC−05:00 | Explicit client reply relayed on 18 November 2026 |
| Profile timezone identifier | `Etc/GMT+5` | Verified with the installed timezone database and existing profile validator; fixed UTC−05:00 |
| Local reporting start, inclusive | `2026-10-01T00:00:00-05:00` | Calendar-month start in the confirmed offset |
| Local reporting end, exclusive | `2026-11-01T00:00:00-05:00` | Next calendar-month start in the confirmed offset |
| UTC reporting start, inclusive | `2026-10-01T05:00:00Z` | Verified by the existing `reporting_bounds` service |
| UTC reporting end, exclusive | `2026-11-01T05:00:00Z` | Verified by the existing `reporting_bounds` service |
| Period length | 31 days / 744 hours | Derived from those exact UTC bounds |

Use the client's explicit fixed offset. The profile's IANA name `Etc/GMT+5`
represents UTC−05:00; do not reverse its sign or substitute a regional timezone.
The reporting-context fragment for the existing source-specific profile is:

```json
{
  "reporting_month": 10,
  "reporting_year": 2026,
  "reporting_timezone": "Etc/GMT+5"
}
```

This is not a complete upload profile. Preserve/recheck the original worksheet,
source/canonical store, USD and marketplace fields. Advertising coverage remains
unconfirmed; keep its completeness assertion false until supported by a reply.
No client workbook was uploaded or financial record created by verifying dates.

## Follow-up message to Pinchas — not sent

Thanks for confirming October 2026, EST (UTC−05:00), and the three Amazon SKUs.
Could you confirm whether product costs and advertising are complete or anything
is missing? One selected item currently has inactive catalogue/listing status;
please confirm whether it is still intended for replenishment. We can review your
demand window, target coverage, lead/safety days, pack/order units and supplier
minimums/terms together. Please confirm whether target coverage already includes
lead and safety. If there are outstanding shipments, please include complete
quantities, shipment references and expected arrivals with timezone. Unknowns
can stay marked unknown; I’ll keep those gaps visible.

## Confirmation worksheet

Fill known facts only. Leave missing numbers blank and use Unconfirmed for
unresolved decisions. A confirmed zero needs a source/reference.

| Item | Client answer | Confirmation reference / date |
| --- | --- | --- |
| October reporting year | 2026 | Client reply relayed on 18 November 2026 |
| Timezone used by ConnectBooks report | EST, explicit UTC−05:00; profile `Etc/GMT+5` | Client reply relayed on 18 November 2026; conversion verified |
| Exact report/account/store and original file | Confirm against supplied original | |
| Product cost coverage and known omissions | Unconfirmed | |
| Advertising coverage and known omissions | Unconfirmed | |
| Freight, taxes or fees not included in report/cost | Unconfirmed | |
| Reviewer / purchasing manager | Unconfirmed | |

The confirmed local/UTC bounds above close the missing year/timezone input.
The three SKU selections have also been supplied separately. Remaining source,
coverage, per-SKU status/unit decisions and purchasing fields still need their own
evidence. The client does not need to provide internal UUIDs or database fields.

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
