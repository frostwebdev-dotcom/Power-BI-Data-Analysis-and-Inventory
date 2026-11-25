# ADR 0029: Require a valid price when deriving selected supplier offers

Status: Accepted for implementation; live rollout pending.

French Perfumes has no supplier SKU column. Some valid barcodes are shared by
different conditions/descriptions, and some priced products have no barcode.
The existing opt-in offer keys preserve those lines independently and keep
shared barcodes under human review. However, deriving keys from description
alone would also turn embedded section headings and repeated headers into offers.

Add a per-profile `offer_key_requires_price` normalization flag, default false.
It requires offer-key derivation and a mapped unit-cost column. When enabled,
the mapped source price must parse as a non-negative decimal before an offer key
can be derived. Price is still excluded from the key's identity, so a price
change never creates a different offer. Existing profiles retain their behavior.

Use this flag only in the reviewed French profile. Preserve condition as an
offer variant, USD/each pricing, listed availability, and unknown quantities.
Rows without usable offer identity remain explicit errors; no source workbook
is rewritten, and no description-only catalogue match is approved.

Acceptance covers blank/header/negative/malformed prices, zero and valid prices,
stable keys across price changes, unchanged defaults, rule dependencies and the
normal profile preview/import path. The supplied original remains private.
