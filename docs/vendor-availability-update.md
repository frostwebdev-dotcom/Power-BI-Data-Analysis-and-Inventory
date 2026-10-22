# Vendor onboarding and stock-source visibility

Client request, October 22, 2026: configure the complete vendor roster and show
which vendor made an item available instead of a generic stock-status label.

## Delivery plan and acceptance

- Add CSV bulk vendor setup with a read-only preview and an explicit apply step.
  Reuse vendor validation; reject unknown columns, invalid rows and duplicate
  codes/names. Create missing vendors atomically with per-vendor audit entries.
  Existing vendors are preserved and identified in the preview, not overwritten
  or reactivated. Require the existing DATA_OPERATOR/ADMIN permission.
- Show actual vendor names and codes on availability events and watch history.
  Show all currently available vendors on each watchlist entry, including
  all-vendor watches. Derive this from latest vendor-line snapshots, not the
  watch's configured vendor filter or a stale historical event.
- Show watched restock events grouped by vendor on the dashboard. Preserve the
  existing time window, tenant scope and stock-transition rules.
- Verify invalid/duplicate CSV handling, no preview writes, idempotent apply,
  audit/transaction/role boundaries, multiple suppliers, current snapshots,
  vendor-specific watches, and cross-organization isolation.

## Data needed to finish the client's configuration

No confirmed complete vendor roster was found in the supplied workspace or
the document searches. Earlier notes name NYB International, PCA, French
Perfumes, YM Trading and TJS Group; QKD was subsequently configured. Those are
sample sources, not an approved complete roster. Do not invent missing suppliers,
contacts, terms, stock files, pack semantics or import profiles. The actual
vendor master and sample inventory files are needed to finish onboarding.

Bulk registry setup does not create inventory or validate a vendor import
profile. Each supplier's profile must be checked against its real sample file.
This change is within Milestone 1 vendor management and availability reporting.
