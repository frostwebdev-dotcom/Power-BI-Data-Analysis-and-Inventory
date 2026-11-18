# 0038. Reviewed inbound positions and arrival qualification

- **Status:** Accepted for this authorized Milestone 2 increment
- **Date:** 2026-11-18
- **Extends:** ADRs 0029–0037

## Scope and implementation plan

Add a tenant-scoped append-only inbound evidence ledger and staff review UI.
It records evidence about already-outstanding shipments, not purchase orders.
Managers/admins confirm or revoke a complete position after a no-write source/
proposal preview. All staff may read and preview. Each stream belongs to a policy
and declared stock pool; every record retains UUID, previous revision, current
policy approval, inventory run, authoritative listing/snapshot, safe source
context and digest, observed/expiry times, explicit whole base-unit total,
arrival references/quantities/aware ETAs, evidence reference, actor and reason.
No value starts at zero by default. Unknown positions cannot be confirmed.

Use policy/request locks, expected head/source/preview digests and request UUIDs.
Confirmation/audit are atomic, exact retries preserve the original record, stale
requests conflict, revocation appends without rewriting or deleting evidence.
Revoke remains possible after source/policy changes. Database guards enforce
scope, chain, current approval/source identity, completeness/chronology,
non-overlap attestations and immutability. Downgrade refuses to discard history.
Active arrival references cannot be credited to two pools in the same policy/
inventory position. No source polling, Amazon writes, receiving transaction,
inventory mutation, order creation or stock reservation is introduced.

## Source and evidence rules

Bind confirmations to an exact current clean inventory run and its stock-source
snapshot plus approved pool membership, conversion and current identity context.
A source/context or approval change requires a new confirmation; no older-head
fallback. FBA total must exactly equal the observed working + shipped + receiving
counts converted once to base units. Unknown native FBA components stay unknown.
FBM native inbound remains labelled not ingested; an explicit manager-reviewed
complete outstanding position may supply its manual total, including evidenced
zero. Both paths require complete-position and no-on-hand-overlap acknowledgements.

Arrivals sum exactly to the declared position, have distinct references and
positive whole quantities. Zero requires an empty schedule. Observation is aware,
not future, and at/after source capture; expiry is explicit, after observation
and evaluation, and at most 31 days after observation. All outstanding ETAs must
still be future at review/use. Missed arrivals or expired evidence block until
reconciled against actual current inventory; never assume arrival means receipt.
Other mapping, unit, stock, demand and source-readiness checks remain mandatory.

## Calculation and version compatibility

Inbound use is opt-in via explicit current ledger revision UUIDs on preview/save
and per-policy current-check/comparison choices. No evidence is chosen silently.
Missing evidence retains the existing blocked/known-zero behavior. Selected
foreign/mismatched/replaced/revoked heads are refused; expired/source-mismatched
positions remain explicit blockers, not zero. Preserve legacy request fingerprints
and byte-equivalent v1–v4 snapshot serialization when no inbound is selected.

New v5 evaluations retain v2 reviewed readiness, original inbound issues and full
ledger/source/arrival assessments alongside existing policy, supplier, financial
and commercial evidence. Existing quantity/financial/commercial functions are
reused internally; adjusted readiness is never published as the old contract.
Immutable saved v1–v4 history is not enriched or rewritten. Migration guards
validate scoped v5 ledger references and preserve prior-version protections.

Qualify arrivals in ETA order against projected demand anchored to the actual
inventory snapshot capture time. Before crediting each arrival, exact integer
microsecond arithmetic must prove cumulative stock covers demand strictly until
that ETA. Earlier qualified arrivals can bridge later ones; late arrivals cannot
erase an earlier stockout. Arrivals beyond approved target + lead + safety coverage
are excluded. Zero demand has no stockout deadline. Known late arrivals stay
visible and contribute zero usable units; unknown or missed evidence blocks.
Keep native observed inbound, manually declared total and usable inbound distinct.

Expose the review ledger/confirmation/revocation flow, reusable explicit inbound
selectors, complete assessment evidence in preview/history/current/comparison
views, and safe integer/date entry. Choice changes/stale previews clear review;
uncertain writes retain their exact UUID/body for retry. No browser money or
arrival-qualification arithmetic replaces server evidence.

## Verification and boundaries

Test strict/unknown/zero contracts, exact boundaries and very large integers,
multiple arrival bridging, late/after-horizon/missed/expired cases, FBA total
reconciliation, manual FBM zero/positive evidence, source and approval changes,
cross-pool duplicates, tenant/role isolation, atomic audit/retries, immutability,
concurrency and safe migrations. Exercise real PostgreSQL/API and browser
review/selection/revocation/stale/retry flows and desktop/mobile layouts. Retain
all existing regression checks, legacy serialization and request fingerprints.

No real client policy/file/evidence, source sync, merge, deployment, purchase or
client message is included. Client reporting/purchasing confirmations and real
three-SKU acceptance remain pending; Gmail and Milestone 1 status are unchanged.
