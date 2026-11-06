# Automatic vendor inventory imports

Implementation, 2026-11-06. Live mailbox connection and acceptance remain pending.

Verified code: `1fca44d27bb202abf3755466e47a0df3ab7d81a2` passed all 1,381
backend tests and all 20 browser tests, including the real PostgreSQL pipeline,
migration/schema agreement and Compose stack. Backend/frontend lint, strict
types, production builds and Windows storage checks also passed in
[CI](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/42274097348).
This is test verification; the live Azure migration, deployment, mailbox
connection and vendor sample acceptance are still pending. No source addresses
or mailbox permissions have been guessed or created.

1. Organization-scoped sources, mailbox checkpoints and delivery history.
2. Read-only Graph authentication, incremental messages and raw attachment
   downloads, bounded retries and origin-checked pagination.
3. Approved public HTTPS downloads and attachment/link routing.
4. Reuses the existing import pipeline; serialize runs, persist retries and
   source ordering, and show received/applied dates without clearing old stock.
5. Audited source management, history/retry API and an administration page.
6. Tests cover real PostgreSQL routing/idempotency/isolation, parser failures, unsafe
   links, duplicates, ambiguous files, retries, ordering and irregular delivery.
7. Backend/frontend/Compose checks gate deployment. Remains disabled until real mailbox
   access, sender rules and vendor download examples can be verified.

Acceptance: a new authorized attachment or approved direct-link file creates
one normal vendor import per configured worksheet. A repeated message or file
does not duplicate stock; a retry recovers without losing its profile version.
No older retry overwrites newer inventory. Failures and unconfigured/ambiguous
links remain visible; unknown messages do not route to an arbitrary vendor.
Read-only users cannot change sources. No mail is sent, modified or deleted.

Required live inputs: exact Microsoft 365 mailbox and folder; administrator
approved/scoped Mail.Read access for a separate unattended integration; sample
vendor sender addresses, attachment names and download-link emails. Do not
send passwords/client secrets in chat. Portal-login flows require inspection
and explicit configuration and are not guessed from an email link.

## Operator setup

1. Apply migration `a61106202601` before deploying/using the new routes. No
   existing vendor import, mapping, inventory or user row is changed by it.
2. Ask the Microsoft 365 administrator to configure a separate unattended app
   with Mail.Read restricted to the intended inventory mailbox using Exchange
   Application RBAC (or an already managed equivalent). Tenant-wide Mail.Read
   consent alone is broad; Exchange RBAC restrictions and broad Entra grants
   can be additive. Verify allowed-mailbox success and other-mailbox denial.
   This app does not create permissions or grant consent automatically.
3. Set VENDOR_MAIL_TENANT_ID, VENDOR_MAIL_CLIENT_ID, VENDOR_MAIL_CLIENT_SECRET,
   VENDOR_MAIL_MAILBOX and VENDOR_MAIL_ORGANIZATION_SLUG privately in runtime
   configuration. Set VENDOR_MAIL_FOLDER_ID if using a dedicated folder rather
   than Inbox. Microsoft personal accounts and IMAP/Gmail are not this adapter.
4. In **Automatic imports**, add a source per vendor file layout/worksheet.
   Select a sample-validated, header-pinned active import profile. Configure
   exact sender addresses and a narrow attachment filename pattern; use a
   subject filter when that sender also sends newsletters or other documents.
   Sender matching routes files; Exchange spam/DMARC controls still govern mail
   authenticity. Do not treat a From address as cryptographic authentication.
5. For a direct link, configure exact approved hostnames, an explicit initial
   path prefix, and the expected downloaded filename. Include redirect hosts
   only after checking real vendor samples. Never paste signed URLs in source
   configuration. Login, SharePoint consent, expiring portal sessions, shortened
   links and changing/unapproved redirects require review or a specific adapter.
6. Enable sources and VENDOR_MAIL_ENABLED, then use **Check mailbox now** as an
   administrator or `python -m app.cli.vendor_mail`. The scheduled job checks
   every five minutes by default; file arrival can be daily or irregular.
7. Send a real authorized sample for each layout; inspect its delivery, normal
   import report, profile version, retained hash and stock. Repeat the same
   email/file and confirm DUPLICATE rather than additional snapshots. Spectrum
   uses two source rules for its two profiles and one retained workbook.

New sources start at their creation time; they do not silently backfill old
mail. Unknown senders are ignored. The cursor survives restarts. Page retries
do not lose already recorded deliveries. Connection errors appear on the page;
transient delivery failures retry with backoff up to five attempts, then require
review. Explicit retry keeps the original profile unless the operator chooses
the source's current profile, an audited action. A retired profile blocks auto
processing instead of silently switching versions.

Paused-source deliveries stay in history but are excluded before the active
queue limit, so they cannot block other vendors. Retrying reappeared historical
content repeats the latest-file check, including when a completed import ID is
already linked; it cannot falsely advance the source's successful-update date.

Invalid source rows hold an automatic import before matching/snapshotting,
retaining the file and report without applying partial stock. Warnings such as
unknown exact quantities do not become errors. An interrupted snapshot must be
recovered before a newer file for that source can apply. Delayed older deliveries
are superseded, and previously seen content reappearing after a newer different
file requires review. Missing mail never clears inventory. The UI separately
shows file receipt and successful processing, using email receipt timestamps.

No email bodies, subjects, raw signed links, OAuth tokens or Graph cursor URLs
are exposed through the history API or audit trail. Mail is read only: this
feature never sends, marks read, moves or deletes messages. The mailbox-scoped
PostgreSQL advisory lock prevents concurrent polls by replicas. Recommended
operation remains one scheduler owner; other integration schedulers retain
their existing single-process constraints.
