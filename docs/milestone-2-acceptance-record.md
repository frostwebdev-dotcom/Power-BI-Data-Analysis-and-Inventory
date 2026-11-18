# Milestone 2 acceptance record — blank template

**NOT RUN. No real SKU, expected result, reviewer decision or client sign-off has
been entered.** Copy this template to an approved private project location for
completion. Keep client financial values/files and the completed record out of git.
Use [the walkthrough](milestone-2-acceptance.md) and
[the confirmations](milestone-2-client-confirmations.md).

## Session and source evidence

| Field | Recorded value |
| --- | --- |
| Environment / exact application commit | |
| Organization UUID / display label | |
| Reviewer identity / role | |
| Client reviewer | |
| Session and observation times (UTC) | |
| Reporting month / confirmed year / timezone / UTC bounds | October / Unconfirmed / Unconfirmed / |
| Currency / canonical store label | USD / Brand Name Deals-Amazon |
| Exact source Store label / original report filename / SHA-256 | |
| Financial import UUID / current binding revision UUID (or evidenced original) | |
| Product cost / advertising coverage and omissions | Unconfirmed |
| Source/mapping/policy confirmation references | |
| Overall operational decision | NOT RUN |
| Client sign-off / date / scope and unresolved items | PENDING |

## Real case identities

| Evidence | Case A | Case B | Case C |
| --- | --- | --- | --- |
| Exact Amazon seller SKU / listing UUID / ASIN | | | |
| Product UUID / Nineyard Catalog Item Number | | | |
| Vendor UUID / vendor-product UUID / exact supplier SKU | | | |
| Complete known listing set / mapping approval references | | | |
| Confirmed units / pool membership / authoritative stock listing | | | |
| Policy UUID / approved revision UUID / manager reference | | | |
| Order baseline UUIDs / exact demand interval / cutoff | | | |
| Inventory run / snapshot UUIDs and times | | | |
| Supplier file digest / receipt / profile version / job / row / snapshot UUIDs | | | |
| Financial source row and exact SKU binding evidence | | | |
| Fresh preview time / evidence SHA-256 / full snapshot reference | | | |
| Saved evaluation / request / audit UUIDs, if recorded | | | |

Duplicate products or shared pools across cases must be identified explicitly.
Use separate per-pool reconciliation rows below; do not sum aliases or vendor offers.

## Reconciliation rows

Copy a row for each pool or financial/source control. Leave the values blank
until independently calculated and observed from real evidence.

| Case / pool / check | Inputs and units / source reference | Independently expected | Observed server result / evidence | Check result | Deviation / owner / retest |
| --- | --- | --- | --- | --- | --- |
| A | | | | NOT RUN | |
| B | | | | NOT RUN | |
| C | | | | NOT RUN | |

## Required checks

| Check | A | B | C | Evidence / deviation reference |
| --- | --- | --- | --- | --- |
| Original bytes / report profile / signed full-report controls | NOT RUN | NOT RUN | NOT RUN | |
| Exact mappings / complete known listing set / unit conversions | NOT RUN | NOT RUN | NOT RUN | |
| Complete shipped-only order-date demand / freshness | NOT RUN | NOT RUN | NOT RUN | |
| Independent physical stock pools / source stock / usable inbound | NOT RUN | NOT RUN | NOT RUN | |
| Latest supplier profile/file/row/snapshot / price / quantity evidence | NOT RUN | NOT RUN | NOT RUN | |
| Manager-approved coverage / minimums / multiple / price units / priority | NOT RUN | NOT RUN | NOT RUN | |
| Required units / shortfall / rounding / caps / combined allocation | NOT RUN | NOT RUN | NOT RUN | |
| Current goods value / scoped supplier minima / terms / unknown costs | NOT RUN | NOT RUN | NOT RUN | |
| Historical financial row amounts / warnings / complete-set totals | NOT RUN | NOT RUN | NOT RUN | |
| Same-product vendor comparison / tax and unit compatibility | NOT RUN | NOT RUN | NOT RUN | |
| Fresh preview / reviewed save / immutable history / audit / retry | NOT RUN | NOT RUN | NOT RUN | |
| Queue search / filters / approval relation / historical dashboard counts | NOT RUN | NOT RUN | NOT RUN | |
| Applicable original availability event evidence | NOT RUN | NOT RUN | NOT RUN | |
| Expected unknown/stale/inbound blocker behavior | NOT RUN | NOT RUN | NOT RUN | |
| purchasing_eligible=false / no stock reservation or order | NOT RUN | NOT RUN | NOT RUN | |
| Operational readiness for this case | NOT RUN | NOT RUN | NOT RUN | |

Allowed check results: PASS, FAIL, BLOCKED, NOT RUN, NOT EXERCISED. Each PASS needs
an observed result and evidence. An expected block can pass its behavior check
while operational readiness stays BLOCKED. Do not mark unexercised features PASS.

## Scope gaps and client disposition

| Gap / requirement | Current disposition | Owner / next action | Retest / client decision |
| --- | --- | --- | --- |
| Reporting year/timezone and real-file coverage review | PENDING | | |
| Positive FBA arrival timing / unknown FBM inbound | Supported blocker; operational handling PENDING where required | | |
| Current urgency/opportunity view versus historical saved counts | PENDING scope review/implementation and real acceptance | | |
| Landed-cost inputs / cost completeness | PENDING | | |
| Required case or second-supplier coverage not exercised | PENDING until cases are selected and run | | |
| Client operational sign-off | PENDING | | |

Overall acceptance remains pending until required scope and deviations are
resolved and the client records a decision. Automated CI success is supporting
engineering evidence, not this real-data acceptance record. Milestone 1 and
paused Gmail setup retain their separate status.
