# ADR 0039: Select an inventory email link by text or button image

Status: Accepted for implementation; live rollout and vendor acceptance pending.

Renumbered from 0028 on 26 November 2026: that number was already taken by an earlier ADR.

## Problem

A confirmed MTZ email contains both an inventory-download link and an ordering
link. They share an HTTPS tracking hostname and path, so the existing host/path
source rule correctly refuses the ambiguous candidates. Neither the private
tracking URLs nor the client workbook belongs in this repository.

A confirmed French Perfumes email instead has unlabeled image buttons for Excel
and PDF downloads. Their public button-image addresses differ, while their
tracking hostname and path are the same. An image-only button needs an explicit
selector rather than a guessed link position or a stored campaign URL.

## Decision

Add an optional exact download-link label to organization-scoped email sources.
When configured, only HTML anchors whose text matches after whitespace collapse
and case folding are candidates. Missing labels do not fall back to other links;
multiple different URLs with the same label still require review. Nested text
and HTML character references are supported. Sources without a label retain
their current HTML-anchor and plain-URL behavior.

Add an optional exact button-image address as a second selector. Match its HTTPS
host/path, ignoring image query strings; never fetch the image. Configuration
rejects query strings, fragments, credentials and non-HTTPS addresses. If text
and image are both configured, both must match the same anchor. Missing and
duplicate matches retain the same review behavior as text selection.

The label only selects candidates. Exact sender, subject, initial host/path,
all redirect hosts, public-address validation, TLS verification, size limits,
file-content checks, pinned import profile and duplicate protections still apply.
No portal access, credentials, purchases or changes to mailbox grants are added.

## Delivery and acceptance

1. Add nullable, bounded selector columns and matching API/editor fields. Existing
   sources default to null and retain their behavior and audit history.
2. Test the two-link tracking example, whitespace/nested markup, missing and
   duplicate labels, unchanged unfiltered behavior, source persistence, tenant
   boundaries and end-to-end ingestion using synthetic emails only.
3. Run repository checks, review the additive migration, apply it before deploying
   code that selects the new column, and verify existing mailbox sources.
4. Configure MTZ from the inspected original email and sample-validated profile.
   Verify the next genuine email; do not fabricate a delivery for an old sample.
