# ADR 0020 — Gmail vendor inventory intake

Status: accepted for implementation, 2026-11-09; mailbox consent pending.

The client supplied a consumer Gmail receiving address, an MTZ price-list
download email/link/workbook and a HAZ attachment workbook. Extend ADR 0019's
bounded, read-only inventory intake to Gmail. This is the same inventory workflow;
RFQs, outgoing email, portal logins and other mailbox use remain outside scope.

Keep Microsoft 365 as the default provider so existing settings and checkpoints
remain compatible. Select one provider per deployment. Gmail uses an app-owned
OAuth client, owner-authorized offline refresh token and only gmail.readonly.
Credentials remain private runtime environment variables. A connected ChatGPT
mailbox does not supply product credentials or authorize another mailbox.
Verify the Gmail profile email against the configured mailbox before reading
messages. Never ask for the mailbox password or expose tokens in public APIs.

The Gmail reader implements the existing MailReader contract. Capture the mailbox
history ID before the initial bounded message listing; preserve that anchor
across listing pages and then consume messageAdded history. Advance a history
anchor only after its last page; persist routed deliveries before each
checkpoint. A lost history cursor restarts the durable initial listing rather
than silently skipping email. Exclude sent, draft, spam and trash messages and
optionally restrict receipt to a configured Gmail label (INBOX by default).

Bound MIME nesting, message/attachment size, response pagination, retries and
in-memory caches. Decode base64url data and distinguish inline attachment data
from external attachment IDs. Use the Gmail internal receipt timestamp, not the
untrusted Date header. Initially load sender metadata only; only routed vendors
load bodies or attachments. Hold malformed known-vendor messages for review and
continue subsequent mail. Reuse the existing delivery/profile, raw-byte retention,
idempotency, ordering, tenant isolation and public-download protections.

A Gmail account requires its owner's OAuth consent; Microsoft Graph credentials
and Workspace service-account delegation do not authorize consumer Gmail.
Google's external Testing refresh tokens with Gmail scopes expire after seven
days. Confirm an appropriate publishing/verification configuration before
claiming a durable unattended connection. Revoked/expired grants fail visibly.

Provide a one-time local setup command for the confidential web OAuth client.
Its registered redirect is `http://127.0.0.1:8765/oauth/callback`; Google permits
loopback redirects for this flow. The operator runs it on their own computer,
using existing typed environment settings. Bind IPv4 loopback only, validate
state and PKCE, and verify the exact mailbox and read-only scope before retaining
the refresh token. Never print credentials or callback codes. Write a new private
JSON mapping of runtime environment variables outside any Git checkout, with
polling explicitly false; do not overwrite existing files or change Azure secrets
automatically. The helper cannot run its callback in a separate cloud browser.
This closes the manual bootstrap gap without adding public callback routes or
database token storage. A Google Cloud access failure does not justify using
another person's account or guessing OAuth credentials.

Acceptance requires real authorized emails from verified vendor senders: one
attachment and one approved public download, duplicate replay and observed
refresh without further login. No live import is inferred from a workbook sample
or a screenshot. Exact sender headers and the final download host still need
verification. Activation remains disabled until consent and acceptance pass.

References:
- https://developers.google.com/workspace/gmail/api/guides/sync
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/identity/protocols/oauth2/#expiration
