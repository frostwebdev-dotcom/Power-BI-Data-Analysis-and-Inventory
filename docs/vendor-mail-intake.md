# Automatic vendor inventory imports

Implementation, 2026-11-06. Live mailbox connection and acceptance remain pending.

## Gmail adaptation — 2026-11-09

The client has confirmed a consumer Gmail mailbox and supplied both MTZ
download-link and HAZ attachment examples. ADR 0020 adds a read-only Gmail
reader behind the existing delivery/import pipeline. Microsoft 365 remains the
default provider; its existing checkpoint identities are preserved. No schema
change is required. Gmail validation/release status is tracked in phase1-status.

The provided workbooks can be inspected; neither has been imported as new stock
as part of email-connection setup. The MTZ tracking hostname could not resolve
from this workspace, so its redirect chain/final file host remain unverified.
The screenshot does not expose original sender headers. Do not invent sender
rules or enable the link source until the original emails can be checked.
No Gmail grant, product OAuth credential or live source has been created.

Verified code: `1fca44d27bb202abf3755466e47a0df3ab7d81a2` passed all 1,381
backend tests and all 20 browser tests, including the real PostgreSQL pipeline,
migration/schema agreement and Compose stack. Backend/frontend lint, strict
types, production builds and Windows storage checks also passed in
[CI](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/42274097348).
Azure staging deployment is verified separately below. The mailbox connection
and vendor sample acceptance remain pending. No source addresses, mailbox
permissions or integration credentials have been guessed or created.

## Staging verification — 2026-11-07

[Release #120](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/42386823969)
succeeded for deployed code `4145feec84f020265046dbc9aad1d5958fb09bad`.
The API and web revisions are `ca-prms-api-staging--gh-42386823969-1` and
`ca-prms-web-staging--gh-42386823969-1`. All release tests, builds, image updates,
API/database health, Entra configuration and CORS checks passed.

Migration `a61106202601` was applied before rollout using its exact tested file
(SHA-256 `fb568fd656f5d9a19484919c7cf4d5ed43b10a5bff7f2e1ffd802f38ccb14a9c`).
The new revision passed a live `alembic check` with no upgrade operations.
At `2026-11-07T15:50:16Z`, the before/after counts matched: 19 vendors,
24 profiles, 25,701 vendor products, 23 import files, 24 import jobs,
30,927 staged rows and 28,909 inventory snapshots. Sources, checkpoints and
email deliveries each contained zero rows. The disabled job returned `DISABLED`.

Microsoft admin sign-in and the
[live Automatic imports page](https://ca-prms-web-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io/automatic-imports)
were verified. The source editor listed all 19 vendors and loaded Universal's
saved v1 profile; the draft was cancelled without saving. The page correctly
shows that the mailbox is not configured, no email sources or deliveries exist,
and **Check mailbox now** is disabled. This verifies deployment and setup controls;
actual email-arrival/import acceptance still requires the client inputs below.

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

Required live inputs: the chosen mailbox/label or folder; owner-authorized Gmail
read-only offline access or administrator-scoped Microsoft 365 Mail.Read;
original vendor sender headers, attachment names and download-link emails. Do not
send passwords/client secrets in chat. Portal-login flows require inspection
and explicit configuration and are not guessed from an email link.

## Microsoft 365 operator setup

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
   than Inbox. Select `VENDOR_MAIL_PROVIDER=microsoft365` (the default).
   Microsoft personal accounts and IMAP are not supported by this reader.
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

## Gmail operator setup

1. In a client-owned Google Cloud project, enable the Gmail API and configure
   the OAuth app/client for an owner-approved server-side authorization flow.
   Request only `https://www.googleapis.com/auth/gmail.readonly`, with
   `access_type=offline`. Use the app's registered redirect URI, state validation
   and Google's supported authorization-code flow. Obtain the refresh token
   through that private flow, never a password, chat message or browser console.
   Follow [Google's server-side guide](https://developers.google.com/identity/protocols/oauth2/web-server).
2. Confirm the app's publishing/verification requirements before unattended use.
   External **Testing** grants with Gmail scopes expire after seven days.
   Do not claim durable daily operation based only on a Testing grant. Consumer
   Gmail is not authorized by Workspace domain-wide delegation or Entra consent.
3. Set these private backend runtime variables while polling is disabled:
   `VENDOR_MAIL_PROVIDER=gmail`, `VENDOR_MAIL_GMAIL_CLIENT_ID`,
   `VENDOR_MAIL_GMAIL_CLIENT_SECRET`, `VENDOR_MAIL_GMAIL_REFRESH_TOKEN`,
   `VENDOR_MAIL_MAILBOX` and `VENDOR_MAIL_ORGANIZATION_SLUG`. No Google credential
   goes in a frontend variable. The reader checks the authorized profile email
   against the configured mailbox before accessing messages.
4. Set `VENDOR_MAIL_GMAIL_LABEL_ID=INBOX` (default) or an exact Gmail label ID.
   Use an empty string to include received mail across labels if vendor mail is
   automatically archived. Sent, draft, spam and trash messages are excluded.
   Microsoft tenant/folder credentials are unnecessary for Gmail.
5. Follow the source, host verification and live acceptance steps 4–7 above.
   Get each vendor's real From address and distinguish stock emails from other
   messages. The client can forward the original emails as `.eml` attachments
   for inspection; forwarding a screenshot or a downloaded workbook alone
   does not preserve the sender and actual download-button destination.
6. Observe one authorized attachment and one approved download importing with
   pinned profiles; replay and refresh the grant without another login. A revoked
   or expired grant displays `GMAIL_AUTHORIZATION_REQUIRED`. A different account
   displays `GMAIL_MAILBOX_MISMATCH`; neither can import stock.

Gmail captures a history anchor before listing initial mail, keeps it across
pages/restarts, and then requests message-added history. A lost history/page
cursor replays from the retained initial lower bound. Gmail labels are filters,
not OAuth permission boundaries: the read-only grant covers that mailbox, while
only configured vendor messages are routed into inventory. Neither provider
loads email images or passes mailbox credentials to vendor downloads.

## Shared operating behavior

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

No email bodies, subjects, raw signed links, OAuth tokens or private cursors
are exposed through the history API or audit trail. Mail is read only: this
feature never sends, marks read, moves or deletes messages. The mailbox-scoped
PostgreSQL advisory lock prevents concurrent polls by replicas. Recommended
operation remains one scheduler owner; other integration schedulers retain
their existing single-process constraints.
