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
| [0016](0016-entra-sign-in.md) | Entra sign-in with explicit enrollment and local roles | Accepted (live Entra verification pending) |
| [0017](0017-nineyard-product-export-identity.md) | Nineyard Products export identity and shared barcodes | Accepted |
| [0018](0018-vendor-offer-and-worksheet-import-identity.md) | Vendor offers and worksheet import identity | Accepted |
| [0019](0019-vendor-inventory-email-intake.md) | Bounded vendor inventory email intake | Accepted |
| [0020](0020-gmail-vendor-inventory-intake.md) | Gmail vendor inventory intake | Accepted |
| [0021](0021-public-gmail-app-information.md) | Public application information for Gmail consent | Accepted |
| [0022](0022-start-milestone-2-replenishment-preview.md) | Start Milestone 2 with an isolated replenishment preview | Accepted |
| [0023](0023-preview-profitability-from-explicit-csv-mappings.md) | Preview profitability from explicitly mapped CSV period totals | Accepted |
| [0024](0024-preview-connectbooks-xlsx-signed-profit-and-loss.md) | Preview the supplied ConnectBooks XLSX profit-and-loss layout | Accepted |
| [0025](0025-record-immutable-financial-imports.md) | Record immutable, tenant-scoped financial imports | Accepted |
| [0026](0026-staff-financial-import-review.md) | Staff financial upload and review screen | Accepted |
| [0027](0027-revise-financial-sku-links.md) | Revise financial SKU links without rewriting source records | Accepted |
| [0028](0028-replace-financial-source-reports.md) | Reviewed replacement of a financial source report | Accepted |
| [0029](0029-amazon-data-readiness.md) | Read-only Amazon data readiness and stock-pool normalization | Accepted |
| [0030](0030-reviewed-purchasing-policies.md) | Versioned purchasing policies with explicit manager review | Accepted |
| [0031](0031-staff-purchasing-policy-editor.md) | Staff purchasing-policy editor and review screen | Accepted |
| [0032](0032-saved-replenishment-evaluations.md) | Saved replenishment evaluations from approved policies | Accepted |
| [0033](0033-supplier-evidence-for-replenishment.md) | Supplier evidence for saved replenishment evaluations | Accepted |
| [0034](0034-financial-context-and-recommendation-review.md) | Historical financial context and staff recommendation review | Accepted |
| [0035](0035-commercial-checks-and-shared-supplier-stock.md) | Commercial checks and shared supplier stock | Accepted |
| [0036](0036-vendor-comparison-and-replenishment-review.md) | Vendor comparison and saved replenishment review | Accepted |
| [0037](0037-current-replenishment-check.md) | On-demand current replenishment checks | Accepted |
| [0038](0038-reviewed-inbound-arrivals.md) | Reviewed inbound positions and arrival qualification | Accepted |
| [0039](0039-select-inventory-email-link-by-label.md) | Select an inventory email link by text or button image | Accepted |
| [0040](0040-require-priced-derived-offers.md) | Require a valid price when deriving selected supplier offers | Accepted |
| [0041](0041-require-authenticated-vendor-email-senders.md) | Hold vendor email whose sender did not authenticate | Accepted |

There are no ADRs 0014 or 0015. 0039 and 0040 were first published as
second copies of 0028 and 0029 and renumbered on 26 November 2026.

Statuses: `Proposed` · `Accepted` · `Superseded by NNNN` · `Deprecated`
