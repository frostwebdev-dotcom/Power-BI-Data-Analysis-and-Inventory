# ADR 0028: Select an inventory email link by its exact label

Status: Accepted for implementation; live rollout and vendor acceptance pending.

## Problem

A confirmed MTZ email contains both an inventory-download link and an ordering
link. They share an HTTPS tracking hostname and path, so the existing host/path
source rule correctly refuses the ambiguous candidates. Neither the private
tracking URLs nor the client workbook belongs in this repository.

## Decision

Add an optional exact download-link label to organization-scoped email sources.
When configured, only HTML anchors whose text matches after whitespace collapse
and case folding are candidates. Missing labels do not fall back to other links;
multiple different URLs with the same label still require review. Nested text
and HTML character references are supported. Sources without a label retain
their current HTML-anchor and plain-URL behavior.

The label only selects candidates. Exact sender, subject, initial host/path,
all redirect hosts, public-address validation, TLS verification, size limits,
file-content checks, pinned import profile and duplicate protections still apply.
No portal access, credentials, purchases or changes to mailbox grants are added.

## Delivery and acceptance

1. Add a nullable, bounded label column and matching API/editor field. Existing
   sources default to null and retain their behavior and audit history.
2. Test the two-link tracking example, whitespace/nested markup, missing and
   duplicate labels, unchanged unfiltered behavior, source persistence, tenant
   boundaries and end-to-end ingestion using synthetic emails only.
3. Run repository checks, review the additive migration, apply it before deploying
   code that selects the new column, and verify existing mailbox sources.
4. Configure MTZ from the inspected original email and sample-validated profile.
   Verify the next genuine email; do not fabricate a delivery for an old sample.
