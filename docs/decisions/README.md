# Architecture Decision Records

Every architecturally significant decision gets a record here: something that is
expensive to reverse, constrains future work, or that a future contributor would
otherwise be tempted to "fix" without knowing why it is the way it is.

## How to add one

1. Copy [0000-template.md](0000-template.md) to `NNNN-short-kebab-title.md`,
   taking the next free number.
2. Fill it in. Keep it short — one page is usually right.
3. Set the status to `Proposed`; change it to `Accepted` on sign-off.
4. Never edit an accepted ADR's decision in place. Write a new ADR that
   supersedes it, and mark the old one `Superseded by NNNN`.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-product-identity-uuid-and-catalog-item-number.md) | Product identity: internal UUID with Catalog Item Number as business key | Accepted |
| [0003](0003-deterministic-match-priority-chain.md) | Deterministic match priority chain with a mandatory exception queue | Accepted |
| [0004](0004-retain-raw-import-files-unchanged.md) | Retain raw import files unchanged | Accepted |
| [0005](0005-import-profiles-as-versioned-data.md) | Vendor import profiles are versioned data, not code | Accepted |
| [0006](0006-transactional-audit-logging.md) | Audit entries commit with the change they describe | Accepted |
| [0007](0007-nineyard-anti-corruption-layer.md) | Isolate Nineyard behind an anti-corruption layer | Accepted |
| [0008](0008-synchronous-sqlalchemy.md) | Synchronous SQLAlchemy, and no pandas in the import path | Accepted |
| [0009](0009-organization-scoped-multi-tenancy.md) | Organization-scoped multi-tenancy from the first migration | Accepted |
| [0010](0010-identifier-model-and-mapping-placement.md) | Identifier model, and where approved mappings live | Accepted |
| [0011](0011-amazon-sp-api-proof-of-concept-in-milestone-1.md) | Admit a read-only Amazon SP-API ingestion into Milestone 1 | Accepted |
| [0012](0012-tenant-scoping-enforced-in-repository-layer.md) | Enforce tenant scoping in the repository layer until row-level security | Accepted |
| [0013](0013-import-profile-rule-shapes.md) | Import-profile rule columns have fixed, validated shapes | Accepted |

Statuses: `Proposed` · `Accepted` · `Superseded by NNNN` · `Deprecated`
