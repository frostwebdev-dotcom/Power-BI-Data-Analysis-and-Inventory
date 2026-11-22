# 0022. Start Milestone 2 with an isolated replenishment preview

- **Status:** Accepted for the bounded first implementation
- **Date:** 2026-11-11
- **Deciders:** Owner instruction to start Milestone 2; implementation by Codex
- **Supersedes / Superseded by:** Does not supersede Milestone 1 acceptance or ADR 0011

## Context

The owner explicitly requested starting Milestone 2 while Gmail authorization
and vendor-source acceptance are paused. The existing scope documents fence
replenishment and profitability out of Milestone 1. The recorded Milestone 2
scope contains ConnectBooks profitability and deterministic replenishment.
There is no verified new commercial agreement or production policy to infer.

## Decision

Document Milestone 2 separately and first implement a pure, typed quantity
calculator with a local JSON-input CLI. No database, new dependency, scheduler,
API route, staff screen, inventory update or purchasing action is added.
Use exact integer arithmetic for required units and quantity rounding. Decimal
daily rates are explanatory only. Require explicit policy and readiness inputs;
missing quantities block suggestions and unknown vendor counts require review.
The [scope](../milestone-2-scope.md) defines formulas and acceptance examples.

The additive lead-time/target/safety interpretation is exposed as a preview
policy that must be confirmed before live integration. This calculation does
not solve inbound arrival timing, listing pack conversion or shared FBM pools.
There are no profit estimates without a verified ConnectBooks cost mapping.

## Alternatives considered

| Option | Why not |
|---|---|
| Wait for Gmail before starting anything | An explicit-input quantity calculator is independent of mailbox credentials. |
| Wire recommendations directly to current velocity output | Shared FBM pools, multipacks, freshness and inbound timing need explicit treatment first. |
| Invent ConnectBooks columns or purchasing defaults | Could silently misstate profit and recommend invalid quantities. |

## Consequences

**Positive** — a reviewable, reproducible first module and regression tests can
be delivered while Milestone 1 operations remain unchanged.

**Negative / cost** — this is not a live recommendation screen; integration and
real-data acceptance remain outstanding.

**Follow-ups** — obtain a real ConnectBooks export, confirm purchasing policy,
build the tenant-scoped live adapter and persisted policy/audit lifecycle, then
ship API/UI recommendations with freshness, normalization and permission tests.
