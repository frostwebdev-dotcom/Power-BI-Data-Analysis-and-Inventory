# 0013. Import-profile rule columns have fixed, validated shapes

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Implementation lead
- **Supersedes / Superseded by:** none. Refines
  [ADR 0005](0005-import-profiles-as-versioned-data.md).
- **Relates to:** [ADR 0004](0004-retain-raw-import-files-unchanged.md),
  [ADR 0006](0006-transactional-audit-logging.md)

## Context

ADR 0005 made a vendor import profile *data*: a row in
`vendor_import_profiles` with six JSONB columns (`column_map`,
`normalization_rules`, `availability_rules`, `quantity_semantics`,
`price_semantics`, `pack_size_handling`), versioned so an import job can
always point at the exact rules it ran under. It deliberately did not say
what goes *inside* those columns. Phase 4 has to, because:

1. A parser that reads an unshaped JSON blob has to guess. "Is `source` a
   header name or a column index? Is a missing `trim` true or false?" Each
   guess is a place where two vendors' files silently mean different things.
2. The admin UI (phase 9) needs to build a form for each rule set. It cannot
   render what is not described.
3. The rule sets refer to each other. A quantity rule that says "case pack
   comes from the pack-size column" is meaningless if the column map has no
   pack-size column. That is only checkable if both are typed.
4. The brief's acceptance criteria (AC-6) name a specific set of targets
   and a specific set of decisions (how availability is read, whether cost
   is per each or per case, what currency) that must be answerable from
   the profile alone.

The alternative — a free-form JSON column validated only by the parser at
import time — pushes every mistake to the moment a real file is being
processed, in front of the operator, instead of the moment the profile is
saved.

## Decision

Each of the six JSONB columns has exactly one Pydantic model that defines
its shape, in `backend/app/imports/profile_rules.py`. The models are
`extra="forbid"`: an unknown key is a validation error, not an ignored
field. Every write path (create, version) validates all six together
through `ProfileRules`, which also carries the cross-rule checks. The same
models produce the JSON Schema the API serves at
`GET /api/v1/import-profiles/rule-schemas`, so the UI and the validator
cannot disagree.

The shapes:

| Column | Shape | Rules enforced on save |
|---|---|---|
| `column_map` | `{"columns": [{"target", "source", "required"}]}`; `target` ∈ `vendor_sku, upc, description, quantity_available, unit_cost, pack_size, unit_of_measure, ignore`; `source` is a header text (matched case- and whitespace-insensitively) or a 0-based index | at least one of `upc` / `vendor_sku`; `quantity_available` always present; each non-`ignore` target at most once; each source at most once |
| `normalization_rules` | `trim`, `upc_strip_non_digits`, `upc_pad_to_12` (booleans, default true); `decimal_separator` ∈ `.` `,`; `thousands_separator` ∈ `""` `,` `.`; `currency_symbols_to_strip` (list, default `["$"]`) | the two separators differ; a symbol is never a digit or a separator |
| `availability_rules` | `available_when` ∈ `quantity_gt_zero` (default) / `status_column`; `status_column`; `available_values`; `unavailable_values` | status mode needs the column and at least one value list; a value cannot be in both lists; quantity mode carries no status fields |
| `quantity_semantics` | `unit` ∈ `each` / `case`; `case_pack_from` ∈ `pack_size_column` / `fixed`; `fixed_pack_size` | `fixed` needs a positive `fixed_pack_size`; `pack_size_column` mode carries none |
| `price_semantics` | `currency` (ISO 4217, upper-cased), `includes_tax`, `per` ∈ `each` / `case` | currency is three letters |
| `pack_size_handling` | `mode` ∈ `store_as_stated` — the only mode | — |

Cross-rule checks on the aggregate: `unit = case` with
`case_pack_from = pack_size_column` requires a `pack_size` mapping;
`per = case` requires a `unit_cost` mapping.

`pack_size_handling` has one mode on purpose. Converting case quantities to
eaches needs a policy (round? refuse fractional? which price follows?) that
has not been decided (phase1-status assumption B3). A single-valued enum
records that the question exists without answering it prematurely; adding
`normalize_to_each` later is an additive change to the shape and a new ADR.

Two adjacent decisions travel with this one:

- **Header signature.** `header_signature` is `sha256` of the header cells
  after normalisation (trim, collapse whitespace, case-fold), joined by a
  unit separator (`\x1f`). It is computed by the validate endpoint and
  pinned on the profile by the operator. A file whose header signature does
  not match is reported before any row is mapped (AC-6.4). Renaming,
  reordering, adding or removing a column all change it; capitalisation and
  spacing do not.
- **Status column by header text, not by mapping.** `availability_rules.
  status_column` names a header. The mapper finds it in the file's header
  row directly, so a vendor's "Status" column need not be given a mapping
  target to be read. An unrecognised status value is `UNKNOWN`, never
  available (AC-11.5).

## Consequences

- A profile that would not parse cleanly cannot be saved. Mistakes surface
  to the person editing the profile, with the field named, instead of to
  the person running an import.
- The stored JSONB is re-validated on read (`rules_of`). A row that stops
  validating — after a shape change, say — is reported as
  `import_profile_invalid` rather than silently parsed with defaults. Any
  future change to a shape therefore needs a data migration for existing
  rows, or must be strictly additive with defaults.
- The set of targets is closed. A vendor column that is none of the eight
  is mapped to `ignore` or not mapped at all; there is no "extra field"
  escape hatch. Adding a target (a MAP price, say) is a code change with a
  test, not a configuration.
- Versioning (ADR 0005) is now enforced end to end: `PATCH` never updates a
  row. It writes version n+1 with the merged rules — re-validated as a
  whole, so a change to one column cannot leave the aggregate inconsistent
  — and deactivates n, with two audit rows in one transaction. Inactive
  versions refuse edits. An import job's `vendor_import_profile_id` keeps
  pointing at the version that ran.
- The validate endpoint reads at most the first 20 data rows and stores
  nothing — not the file, not the profile. The raw-file retention rule
  (ADR 0004) starts at import, not at preview.
