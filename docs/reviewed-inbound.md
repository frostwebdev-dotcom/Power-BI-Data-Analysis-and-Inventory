# Reviewed inbound arrival evidence

Draft Milestone 2 implementation, ADR 0038. Real-data operational acceptance
with client-approved stock pools, units, policies and shipments remains open.

Open **Inbound arrivals**, choose an approved policy and pool, and load its
current stock source. A purchasing manager/admin can enter the complete
outstanding position in whole product base units, an evidence reference,
observation and expiry (within 31 days), and referenced arrivals with quantities
and timezone-aware ETAs. No quantity is prefilled as confirmed. Explicit zero
requires total 0 with no arrivals; unknown quantities or dates remain unconfirmed.
Positive arrivals must sum exactly to the complete declared position. The screen
rejects integers beyond JavaScript's exact display range instead of rounding.

Attest completeness and no overlap with on-hand stock or other pools. Preview,
review blockers/source evidence, enter a reason and acknowledge before confirming.
Confirmation creates immutable audited history. Reasoned revocation appends a new
revision. An ambiguous network result offers an identical-request retry. Changed
source, approval, head or preview requires fresh review. Other staff read roles
can inspect sources/history but cannot confirm or revoke.

| Evidence | Treatment |
|---|---|
| FBA | Declared position equals raw/stored working + shipped + receiving, converted once with the approved source listing factor. Unknown native components block confirmation. |
| FBM | Native inbound remains un-ingested. A manager explicitly confirms the entire manual position, including zero. |
| Changed inventory snapshot, mapping, pack/pool context or approval | Review again; no automatic carry-forward. |
| Expired evidence or any reached/missed ETA | Position becomes unknown pending reconciliation. Passing an ETA never adds on-hand stock. |
| Arrival strictly before depletion and within coverage | Credit whole units in ETA order; an earlier qualified arrival can bridge a later one. |
| Arrival exactly at/after depletion or beyond coverage | Known zero credit for this evaluation. |

Timing is anchored to the inventory capture, using approved shipped-demand
velocity and target + lead + safety coverage. Integer microseconds preserve exact
depletion boundaries. One expired or missed arrival invalidates the whole position.
This increment does not project observed on-hand stock to the current instant.

Recommendations, Current replenishment and Vendor comparison offer optional
reviewed inbound choices per policy. Open the section and explicitly select exact
current revisions. None is selected automatically. The server rechecks ownership,
approval, ledger head, source identity, expiry and timing on every preview/save.
Unselected confirmations leave native readiness behavior intact.

Reviewed snapshots use `replenishment-evaluation-v5` and `amazon-readiness-v2`,
retaining original inbound issues, native observations, complete positions,
provenance and per-arrival credit/exclusion. Existing v1–v4 saved schemas and
successful retry fingerprints remain compatible. Later revocation does not rewrite
saved historical evaluations.

This is evidence review, not receiving, shipment tracking, provider refresh,
reservation, purchasing, RFQ or PO creation. Supplier/commercial/historical
financial checks remain separate. Purchasing eligibility stays false. No merge,
deployment, client input upload or mailbox change is included.

Acceptance must exercise timely/late FBA shipments, explicit positive/zero FBM
positions, missed/expired evidence and changed sources with original client
evidence. Use the three-real-SKU walkthrough after client approval of pack/pool
identity, policy values, source age tolerances and evidence ownership.
