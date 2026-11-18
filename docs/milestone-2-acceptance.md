# Milestone 2 acceptance walkthrough

Status: **PREPARATION ONLY — real-data walkthrough NOT RUN; client sign-off PENDING.**

This runbook covers the authorized [Milestone 2 scope](milestone-2-scope.md).
It does not accept Milestone 1, authorize a deployment/source sync or enable
Gmail. The application changes are on draft PR #22. Their verified application
baseline source is `34d05b053b0f7da907e0eb5c5cd98f44a4ef677a`, with
[CI #221](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/44310886846)
passing 2,299 backend and 92 browser tests. It includes current checks and reviewed
inbound positions. Validation for later documentation changes is maintained in
[PR #22](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/pull/22). Automated synthetic tests are separate from
client operational acceptance. Recheck the actual test environment/source when
the walkthrough is scheduled; no hosted Milestone 2 deployment is evidenced here.

Complete the [client confirmations](milestone-2-client-confirmations.md) first.
Copy the [blank acceptance record](milestone-2-acceptance-record.md) into an
approved private project location for real identifiers, financial values and
evidence. Do not commit the completed copy or client files to this repository.

## Known context and prerequisites

| Input | Current evidence | Required action |
| --- | --- | --- |
| Reporting month | Client said October | Preserve October; do not infer a year |
| Currency | Client confirmed USD | Preserve USD |
| Item field | Client confirmed it is Amazon SKU | Check exact SKU against approved scoped mappings |
| Store display label | Client said Brand Name Deals-Amazon | Preserve this label; recheck the workbook's exact source Store cells separately |
| Reporting year/timezone | Client confirmed 2026 and EST, explicit UTC−05:00 | Use profile `Etc/GMT+5`; retain the relayed reply and verified boundaries in the client worksheet |
| October 2026 UTC period | `2026-10-01T05:00:00Z` inclusive to `2026-11-01T05:00:00Z` exclusive | Derived from the explicit fixed offset; verify exact source profile before import |
| Product cost / advertising coverage | Unconfirmed | Record what is included, excluded or unknown; zero does not establish completeness |
| Three real Amazon SKUs | Not selected for this walkthrough | Client selects representative cases; collect exact source/identity evidence |
| Purchasing/source/commercial policy | No client-approved values evidenced for these cases | Obtain per-product/supplier decisions and manager review |
| Real-data acceptance environment | Not established by this record | Identify source commit, organization, authorized operators and test environment before any data action |

Year/timezone were confirmed in the client reply relayed on 18 November 2026;
see the [verified reporting context](milestone-2-client-confirmations.md#confirmed-october-reporting-context).
This resolves those metadata inputs without recording a real report. Other
cost/coverage unknowns may remain explicitly disclosed review items; they must
not be relabelled complete merely to obtain a passing result. Advertising
coverage is unchecked unless verified. Follow [financial imports](financial-imports.md)
for profile identity, immutable original bytes and correction/replacement rules.

## Before scheduling the client review

| Gate | What must be available | Current state in this preparation |
| --- | --- | --- |
| Report identity | Client-confirmed October year/timezone and the exact original file/account/store | 2026 and fixed UTC−05:00 confirmed; verify the exact original file/account/source Store before import |
| Case selection | Three exact seller SKUs selected by the client, with source evidence and complete related listing/pool sets | Not selected |
| Purchasing decisions | Client-confirmed units, stock pools, coverage, source-age limits and applicable supplier/commercial settings, followed by manager approval | No approved real-case values evidenced |
| Inbound use, when needed | Complete outstanding position, original evidence, observation/expiry, per-shipment quantities and timezone-aware ETAs, manager review and exact selected revision | No real position recorded |
| Execution context | Environment and exact application source, organization, reviewer/roles and the data actions in scope | Not established; the draft branch is not a hosted deployment |

Preparation can continue without those answers, but missing data must not be
replaced with examples. The confirmed financial reporting context is recorded
above; quantity-only checks do not need an optional financial report, but they still
need their own verified inputs and approved policy. Preserve any remaining
cost/advertising unknowns as review items. The developer fills internal UUIDs
from actual application evidence; the client supplies business facts.

## Scope coverage and honest limits

| Deliverable | Implemented evidence | Operational acceptance still needed |
| --- | --- | --- |
| Amazon demand, inventory and readiness | Read-only scoped ingestion/readiness; explicit shipped-only order-date coverage, conversions and physical pools | Fresh complete source proof and approved policy for each selected product |
| ConnectBooks profitability | Signed XLSX preview, original-file retention, financial review/binding/replacement UI and historical context | Use the confirmed 2026/fixed UTC−05:00 period; reconcile the real report and review cost/advertising coverage and exact links |
| Replenishment | Exact per-pool arithmetic, manager-approved policies, source checks, immutable evaluations and finite supplier allocation | Independently reconcile real inputs/results and verify expected blockers |
| Reviewed inbound | Exact source-bound complete FBA/manual FBM positions and versioned ETA credit | Reconcile real shipments, units and timely/late/expired/missed cases; manager evidence and explicit selection |
| Vendor comparison | Explicit selected policies; qualified exact current goods-price ordering; complete per-offer evidence | Two comparable supplier offers for the same product where available; preserve unknown/incompatible offers |
| Dashboard/search/availability | Searchable saved-review queue, separate on-demand current selected-policy urgency/supplier check, and existing availability feed | Verify fresh selected-policy results, queue/feed evidence and scope; historical counts are not current urgency |
| Real walkthrough/sign-off | This procedure and blank record | Run cases, retain evidence, resolve deviations and obtain client decision |

Positive FBA inbound requires an explicitly selected manager-reviewed complete
position reconciled to native quantities. Native FBM inbound is not ingested;
a complete manual positive/zero position can be reviewed separately. Follow
[reviewed inbound](reviewed-inbound.md), retain original evidence and exercise
ETA qualification, expiry, missed arrivals and source changes. Dates alone do not
confirm completeness. Unknown quantities remain blocked; never enter zero or
modify saved inputs to bypass missing evidence. Unselected FBA zero is supported
only when native sources prove zero.

The saved dashboard is not a live urgency certification. Use the separate
[current replenishment check](current-replenishment.md) for 1–10 explicitly
selected approved policies and retain its time/cutoff and full evidence. Its
signals describe that selected observation, not catalogue-wide continuous
monitoring. Current-view operational acceptance remains pending until exercised
with real evidence; saved counts do not substitute for that check.

Every saved/compared evaluation remains `purchasing_eligible=false`; stock is not
reserved and no purchase order is generated. Complete landed cost and projected
profit are not established by current reported supplier goods prices or by a
historical financial report.

## Choose three real cases

Use case labels A, B and C, then classify the actual evidence. Suggested coverage
is one positive shortfall with verifiable inputs, one clean zero shortfall, and
one pack/shared-stock or unknown/stale/inbound case. These are coverage goals,
not assumed client values. If the selected cases cannot exercise a goal, mark
it NOT EXERCISED and retain it as a gap. A blocked case can pass its expected
blocking check; three blocked cases do not prove usable positive recommendations.

Three Amazon SKUs do not necessarily mean three products or three independent
stock pools. For every selected SKU collect the complete known listing set of
its canonical product, approved listing/vendor mappings and physical stock-source
membership. Aliases must not duplicate stock. Financial product totals also
require the complete known listing set, which may exceed the three selected SKUs.
Do not manufacture a second supplier or a new mapping for comparison coverage.

## Walkthrough sequence

| Step | Reviewer action | Evidence / expected result |
| --- | --- | --- |
| 1. Establish the test context | Record exact source commit, environment, organization, reviewer, roles and UTC observation time | Confirm the intended Milestone 2 code is present; leave deployment/access actions outside this record until authorized |
| 2. Verify the originals | Identify exact ConnectBooks report and supplier files; retain names, SHA-256, receipt times and profile versions privately | Source bytes remain unchanged; original downloads match recorded digests |
| 3. Review the financial report | Enter confirmed year/month/timezone, exact source/canonical store and marketplace; review all batch controls and selected rows | Correct half-open UTC period, signed money, visible unknowns/zero-cost warnings, Unitemized unallocated; no double deduction of fee details |
| 4. Review identity and units | Check exact SKUs, product/listing/vendor UUIDs, ASINs and mapping approval evidence; confirm every conversion and pool | No description-only match; complete listing/pool membership; source, order and price units are separate decisions |
| 5. Establish source readiness | Inspect already-ingested baseline/order/inventory and supplier job/file/row/snapshot evidence | Clean complete current sources within approved limits; no older fallback; unknown inbound remains blocked |
| 6. Review purchasing policy | Enter client-confirmed window/coverage/lead/safety/minimum/multiple/freshness/supplier settings; manager reviews definition/context | Record exact approval UUID and evidence references; a draft is not approval; no copied fixture defaults |
| 7. Review inbound, if required | After policy approval, load the exact stock source, review the complete position and manager confirmation; explicitly select its current revision on each policy check/evaluation/comparison | Retain native, declared and credited quantities separately; v5 evaluation/v2 readiness retains original issues, revision/source digests and per-arrival credit; no selection retains native behavior |
| 8. Preview and reconcile | Run the explicit current policy check for urgency/supplier review; separately select an optional current report/binding in Recommendations when historical financial context is required | Compare each pool, urgency boundary and v4/v5 allocation with independent arithmetic below; retain all reasons, including expected blocks |
| 9. Compare vendors | Explicitly select available policies for the same product; inspect reported/current base price, tax basis, receipt age, stock and checks | Default vendor order; exact price order only for compatible evidence; a cheaper blocked offer remains blocked; comparison is unsaved |
| 10. Record an evaluation | Through the existing fresh evaluation workflow, review evidence, reason and acknowledgement; if recording is in scope, save with a new request UUID | Snapshot and audit are atomic; changed evidence requires a new preview; uncertain network save uses the exact original UUID/body for retry |
| 11. Review history and dashboard | Open immutable detail and latest-saved queue; exercise product/vendor/status/approval/search filters and corresponding dashboard links | Historical counts agree with the latest record per policy; later drafts preserve approval, changed/revoked approvals are flagged; no stock/money totals inferred |
| 12. Review availability | Inspect existing availability-feed events and their original source/import evidence for a selected item, if an event exists | No event is fabricated; absent applicable history is NOT EXERCISED, not proof of a refresh or restock |
| 13. Record the client decision | Complete observed/expected values, evidence references, deviations and reviewer/client disposition | Accept only exercised, reconciled checks; full Milestone 2 sign-off stays pending while required scope/data gaps remain |

This sequence describes later authorized data actions; preparation has performed
none of them on client systems. Readiness/preview/comparison do not fetch new
source data. If baseline/import work is needed, record its target/scope separately
and follow the existing ingestion runbooks. Gmail remains paused; an authorized
manual original-file import does not prove unattended email intake works.

## Independent reconciliation

| Check | Expected calculation or invariant |
| --- | --- |
| Net sales | Sales + Refunds + Adjustments, preserving source signs |
| Source profit | Net Sales + Fees - Cost - Ad Spend; fee-detail columns are not deducted again |
| Margin / cost-based ROI | Profit / positive Net Sales and Profit / positive Cost, respectively; percent display multiplies by 100; undefined denominators remain unknown |
| Batch controls | Full report detail including Unitemized reconciles to the original footer within the supported one-cent tolerance; never sum only the three test rows as the report total |
| Required units | ceiling(sold base units × (target + lead + safety days) / window days); confirm target excludes the separately added lead and safety |
| FBA complete inbound | (native working + shipped + receiving) × approved source-listing base-unit factor = declared total = sum of arrival quantities; apply conversion once |
| Manual FBM complete inbound | Record an explicitly reviewed complete position in base units; confirmed zero requires total 0 with no arrivals; native observed inbound remains unknown |
| Arrival qualification | Follow the exact capture-anchored sequence below; the declared complete total is not automatically the usable amount |
| Net shortfall | max(0, required - known on hand - usable inbound), separately for each physical pool |
| Before supplier cap | If shortfall is positive, round max(shortfall, approved per-pool minimum) upward to the approved order multiple; otherwise zero |
| Finite supplier stock | v4/v5 use one product/supplier-SKU stock count in explicit approved pool priority; allocations respect multiples/minima, never exceed available stock and show unmet quantities |
| Supplier order units / goods value | Combined allocated base units / approved base units per order unit; goods value uses exact current reported price and approved price-unit conversion; source discount applies once |
| Supplier minima | Check only confirmed SKU-level quantity/value scopes; unmet checks block and unknown/scopeless checks remain unconfirmed; no automatic top-up |
| Price comparison | Exact reported USD price / approved base units per price unit; compare exact ratios only with matching approved listing-unit projection and stated tax basis |

Keep source quantities and normalized quantities side by side. Do not multiply
historical profit/cost by a suggested quantity, distribute Unitemized or combine
independent vendor offers into one reservation/order. Verify large quantities
from exact server evidence; the browser labels unsafe integers unavailable.

## Inbound reconciliation and change checks

Copy the inbound tables in the [blank record](milestone-2-acceptance-record.md)
for each policy/pool. Record **native observed**, **manager-declared complete**
and **server-credited usable** quantities separately, with source and base units
side by side. FBA must reconcile all three native components; missing components
cannot be replaced by a guessed manual total. FBM's reviewed manual position does
not turn native inbound into an ingested value.

Only after position validity, source identity, current approval/head, completeness
and no-overlap checks pass, independently assess arrivals in ETA order. Let `D`
be sold base units, `W` the approved window days, `H` source on-hand base units,
`C` the inventory capture time and `S` the units credited from earlier arrivals
(initially zero for this reconciliation, not a default stock input). For each ETA:

1. Keep the full UTC timestamp precision and compute elapsed microseconds from `C`.
2. Require the ETA to be after the evaluation time and no later than `C` plus
   target + lead + safety days (each day is exactly 86,400 seconds).
3. With positive `D`, prove `(H + S) * W * 86,400,000,000 > D * elapsed_microseconds`.
   Equality is too late. With verified zero demand there is no depletion deadline;
   coverage and all evidence checks still apply.
4. Credit the arrival's whole base units only if both timing checks pass, then add
   that credit to `S`. A known late or after-coverage arrival contributes zero;
   it cannot bridge an earlier stockout. Compare the final `S` with server usable
   inbound and retain every excluded arrival and reason.

Do not round to displayed days-of-supply or lose microseconds while checking a
boundary. This method uses observed stock at capture; it does not project on-hand
stock to the current instant. If any required stock/demand/position evidence is
unknown, stop the independent credit calculation and record unknown, not zero.

| Evidence or change | Expected behavior to verify |
| --- | --- |
| No reviewed revision selected | Native path: only proven FBA zero is usable; positive FBA and un-ingested FBM stay unknown |
| Valid complete position, future ETA strictly before depletion and within coverage | APPLIED with full arrival credit; retain any other purchasing blockers |
| Valid complete position, future ETA at/after depletion or beyond coverage | APPLIED with known zero credit for that arrival; no receipt is inferred |
| Any outstanding ETA reached/missed, expiry reached, or source context changed | Selected position BLOCKED, usable/arrival credits unknown; reconcile and review again |
| Selected head revoked/replaced or approval changed | Refuse stale selection and choose/review the current evidence explicitly; no automatic fallback |
| Revocation after a saved evaluation | New previews cannot use the revoked head; the saved historical snapshot remains unchanged |
| Uncertain confirmation/revocation/evaluation save result | Retry the exact original request UUID/body; preserve the eventual original result |

Perform change/revocation/retry exercises only on records and actions explicitly
in scope for the chosen test environment. Do not revoke valid client evidence,
change source records, advance clocks or trigger shipments merely to manufacture
a case. Where real evidence cannot exercise a condition, mark NOT EXERCISED and
retain its existing synthetic CI coverage as separate engineering evidence.

## Completion rule

Each case records EXPECTED, OBSERVED, evidence and PASS / FAIL / BLOCKED / NOT RUN /
NOT EXERCISED. PASS requires an observed match; an expected blocker may be a
passing behavior check while operational readiness remains BLOCKED. Keep those
two decisions separate. No blank result means pass. Every failure/gap has an
owner, next action and retest reference. Only the client can provide final
operational sign-off after the agreed scope and real-data checks are satisfied.
