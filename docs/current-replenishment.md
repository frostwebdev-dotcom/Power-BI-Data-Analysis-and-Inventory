# Current replenishment check

ADR 0037 adds an on-demand check at `/replenishment-now`. All staff read roles,
including Viewer, can search current approved policy choices, explicitly select
1–10 of them and check fresh ingested evidence. The dashboard and navigation
link to this page. The existing saved-review dashboard and its API remain
historical and unchanged.

| API | Behavior |
| --- | --- |
| GET /api/v1/replenishment-now/policies | Current approved decisions only; bounded page/page_size (maximum 100), literal q, optional product_id/vendor_id |
| POST /api/v1/replenishment-now/preview | Choices contain policy_id and expected_approved_revision_id; 1–10 distinct policies; no client-supplied calculations or financial default |

The complete selection is organization-scoped and preflighted. Policy locks are
acquired in stable product/vendor-product/marketplace order; existing previews
use one aware observation time. A foreign/missing choice returns 404 and a changed
approval returns 409 for the whole request. A later draft preserves the current
approval. There is no partial response, saved-evaluation fallback, source sync,
policy mutation or audit/evaluation write. No migration or dependency is added.

## Stockout urgency

Urgency uses verified Amazon readiness pools and the approved quantity policy.
It is independent of supplier file/stock/price readiness: unavailable supply does
not erase proven demand or stockout risk. Identity/policy blocks, incomplete
Amazon readiness or any required unknown pool keep the policy signal UNKNOWN.
Per-pool evidence remains visible. A missing pool set is also unknown.

The versioned rule uses exact integer arithmetic:

| Signal | Complete current evidence required |
| --- | --- |
| URGENT | Positive shortfall and positive sold units, with (on hand + usable inbound) × window <= sold × (lead + safety) |
| REPLENISH | A positive shortfall without the urgent condition |
| NO_REORDER | All required pools have zero shortfall |
| UNKNOWN | Any required policy/Amazon/pool input is unavailable or unverified |

Required units and shortfall reuse the existing exact calculator and approved
target + lead + safety policy. Supply days include usable inbound and are shown
as exact numerator/denominator strings; zero demand has unknown supply days.
No rounded daily rate or browser arithmetic decides urgency. At policy level,
any unknown pool takes precedence; otherwise any urgent pool selects URGENT,
then positive shortfall selects REPLENISH, otherwise NO_REORDER.

## Supplier review opportunities

This is an independent diagnostic. REVIEW_OPPORTUNITY requires known positive
shortfall, a non-blocked evaluation, verified in-stock finite supplier quantity,
and v4 positive combined allocation with assessed current goods price/units,
reviewed terms and met scoped minimums. A valid partial allocation can qualify;
its unfilled request and remaining stock evidence stay in the complete preview.
Zero reported price retains a review warning and does not qualify automatically.

Known out-of-stock/zero supply, unmet minimums or zero usable allocation are
UNAVAILABLE. Unknown quantity, stale/missing supplier evidence, legacy policy
without commercial checks or incomplete commercial evidence are UNCONFIRMED.
Known zero shortfall is NO_SHORTFALL. All results remain purchasing-ineligible;
review opportunity does not verify landed costs, forecast profit or reserve stock.

## Staff workflow and interpretation

Policy choice search/product filters and pagination are bounded lookups; none
evaluates a policy automatically. Selections and their expected approval UUIDs
stay visible across those lookups. Reload clears them. No financial report is
selected by this current quantity/supplier check.

Check results show the observation time, demand cutoff, per-pool quantities and
exact ratio, separate urgency/supplier signals, source/evaluation/commercial
status and complete versioned evidence. Filters affect only the captured checked
set and make no new request. Run the check again to revalidate current sources
and freshness. Changing the selected set clears the result; late responses from
older selections are ignored. Changed approvals require new explicit choices.

Counts refer to checked policies, including different vendor offers for the same
product. They are neither unique-product counts nor organization-wide totals.
No inventory, quantities or money are summed across products or offers. The
default urgency grouping is a stable presentation order, not a vendor winner,
budget allocation or recommended purchase. Recording requires a separate fresh
evaluation/review using the existing Recommendations workflow.

The observation reads already-ingested sources; it does not establish continuous
live monitoring. Unsupported positive-FBA arrival timing and unknown FBM inbound
continue to block readiness. Client reporting/purchasing confirmations and the
real three-SKU [acceptance walkthrough](milestone-2-acceptance.md) remain pending.
No client file/policy, Gmail setup, source sync, purchase, merge or deployment is
performed by this increment. Full validation is recorded against source in PR #22.
