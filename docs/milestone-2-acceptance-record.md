# Milestone 2 acceptance record — blank template

**NOT RUN. No real SKU, expected result, reviewer decision or client sign-off has
been entered.** Copy this template to an approved private project location for
completion. Keep client financial values/files and the completed record out of git.
Use [the walkthrough](milestone-2-acceptance.md) and
[the confirmations](milestone-2-client-confirmations.md).

## Session and source evidence

| Field | Recorded value |
| --- | --- |
| Environment / exact application commit / applicable CI run | |
| Data actions in scope / reviewer authority reference | |
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
| Explicit inbound selection: none or exact current revision IDs / pool IDs | | | |
| Inbound source digest / original evidence / observed time / expiry | | | |
| Fresh preview time / evidence SHA-256 / full snapshot reference | | | |
| Evaluation / readiness rule versions (v5/v2 for selected inbound) | | | |
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

## Reviewed inbound evidence — repeat per policy/pool

Leave quantities and identities blank until taken from real evidence. Keep
native FBA totals, complete declarations and usable arrival credit distinct.
For FBM, identify the complete position as manual and retain native inbound as
unknown. If no reviewed revision is selected, record that explicitly; do not
fill this table with a guessed confirmation. Use the capture-anchored method in
the [walkthrough](milestone-2-acceptance.md#inbound-reconciliation-and-change-checks).

| Position evidence | Recorded value / source |
| --- | --- |
| Case / policy / approved revision / pool / FBA or FBM | |
| Inventory run / authoritative listing / snapshot / capture time (UTC) | |
| Source context SHA-256 / exact current ledger head / selected revision IDs | |
| Native working / shipped / receiving quantities and source units (FBA) | |
| Approved base units per native listing unit / native total in base units | |
| Manager-declared complete total in whole base units | |
| Original evidence reference / observation time / expiry (UTC) | |
| Manager / confirmation reason / complete-position and no-overlap attestations | Unconfirmed |
| Confirmation request / audit references | |
| Evaluation time / approved demand window / sold base units / on-hand base units | |
| Target + lead + safety coverage / resulting UTC coverage end | |
| Independently expected usable inbound / observed server usable inbound | |
| Assessment / original native issues / current blockers | NOT RUN |

For confirmed zero, retain the declaration of 0, empty arrival schedule and
source/manager evidence. An empty table is not a confirmed zero. For positive
positions, add one row per arrival in ETA order; use whole base units and exact
UTC times. Do not manufacture a row when no real arrival exists.

| Shipment reference / source | Qty and source-to-base conversion | ETA (UTC) / elapsed microseconds from capture | Earlier credited units / independent timing result | Expected credit / observed credit / disposition | Evidence / check result / deviation |
| --- | --- | --- | --- | --- | --- |
| | | | | | NOT RUN |

The arrival quantities must sum to the complete declared position, including
arrivals excluded from credit. Retain exact source digests/selection and compare
fresh current-check, comparison and saved-evaluation evidence at their respective
observation times. Changed inputs require a new preview, not an assumption that
all observations have equal quantities. Native/declared/usable must not be summed.

## Inbound condition coverage

Each line needs a real case/evidence reference or an explicit NOT EXERCISED gap.
A synthetic CI test alone cannot populate the observed real-data result.

| Condition | Real case / evidence / observation time | Expected behavior | Observed result / reference | Check result / gap owner |
| --- | --- | --- | --- | --- |
| Native FBA zero / unselected native path | | Only source-proven zero usable; no inferred confirmation | | NOT RUN |
| Complete timely FBA position / native reconciliation | | Declared matches converted native total; only qualified arrivals credited | | NOT RUN |
| Complete manual positive FBM position | | Manual provenance explicit; native inbound still unknown | | NOT RUN |
| Complete explicit zero FBM position | | Total 0 / empty schedule / manager evidence; no defaulted zero | | NOT RUN |
| Future late / at-depletion / after-coverage arrival | | Known zero credit for excluded arrival | | NOT RUN |
| Expired evidence / reached or missed ETA | | Whole selected position blocked; usable credits unknown; no assumed receipt | | NOT RUN |
| Inventory source / mapping or pack/pool context changed | | Old position cannot silently carry forward | | NOT RUN |
| Selected head revoked/replaced / approval changed | | Stale selection refused; saved history retained | | NOT RUN |
| No-overlap / duplicate reference across pools in one policy position | | No duplicate arrival credit; same physical stock never added twice | | NOT RUN |
| Confirmation/revocation exact retry / audit and role checks | | Original result preserved; only manager/admin mutates | | NOT RUN |

Only exercise mutations/retries on authorized test records. Do not change valid
client evidence to obtain a negative result. Each selected confirmation must
belong to its policy. Separate vendor-offer scenarios may describe the same
physical inbound stock; they do not reserve inventory or justify summing offers
into a purchase.

## Required checks

| Check | A | B | C | Evidence / deviation reference |
| --- | --- | --- | --- | --- |
| Original bytes / report profile / signed full-report controls | NOT RUN | NOT RUN | NOT RUN | |
| Exact mappings / complete known listing set / unit conversions | NOT RUN | NOT RUN | NOT RUN | |
| Complete shipped-only order-date demand / freshness | NOT RUN | NOT RUN | NOT RUN | |
| Independent physical stock pools / source stock / usable inbound | NOT RUN | NOT RUN | NOT RUN | |
| Explicit inbound selection / current approval, head, inventory source and digest | NOT RUN | NOT RUN | NOT RUN | |
| Complete FBA/manual FBM position / expiry / no-overlap / native reconciliation | NOT RUN | NOT RUN | NOT RUN | |
| Per-arrival exact timing / excluded credit / v5-v2 evidence and historical replay | NOT RUN | NOT RUN | NOT RUN | |
| Latest supplier profile/file/row/snapshot / price / quantity evidence | NOT RUN | NOT RUN | NOT RUN | |
| Manager-approved coverage / minimums / multiple / price units / priority | NOT RUN | NOT RUN | NOT RUN | |
| Required units / shortfall / rounding / caps / combined allocation | NOT RUN | NOT RUN | NOT RUN | |
| Current goods value / scoped supplier minima / terms / unknown costs | NOT RUN | NOT RUN | NOT RUN | |
| Historical financial row amounts / warnings / complete-set totals | NOT RUN | NOT RUN | NOT RUN | |
| Same-product vendor comparison / tax and unit compatibility | NOT RUN | NOT RUN | NOT RUN | |
| Fresh preview / reviewed save / immutable history / audit / retry | NOT RUN | NOT RUN | NOT RUN | |
| Queue search / filters / approval relation / historical dashboard counts | NOT RUN | NOT RUN | NOT RUN | |
| Fresh selected-policy urgency / independent supplier review / full evidence | NOT RUN | NOT RUN | NOT RUN | |
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
| Reviewed FBA/manual FBM positions and arrival timing | Implemented on draft; real positions and condition coverage NOT RUN | | |
| Missing real shipment / zero-position / expired or changed-source coverage | NOT EXERCISED until supported by actual scoped evidence | | |
| On-demand current selected-policy urgency/opportunity acceptance | Implemented on draft; real cases NOT RUN; historical counts are separate | | |
| Landed-cost inputs / cost completeness | PENDING | | |
| Required case or second-supplier coverage not exercised | PENDING until cases are selected and run | | |
| Client operational sign-off | PENDING | | |

Overall acceptance remains pending until required scope and deviations are
resolved and the client records a decision. Automated CI success is supporting
engineering evidence, not this real-data acceptance record. Milestone 1 and
paused Gmail setup retain their separate status.
