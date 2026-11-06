# ADR 0019 — Bounded vendor inventory email intake

Status: accepted for implementation by user instruction, 2026-11-06.

The client clarified that vendor inventory arrives as Excel attachments or
email download links, on daily or irregular schedules. The user explicitly
requested implementation within Milestone 1. This narrows and supersedes the
previous email-intake exclusion; RFQs, quotes and outgoing mail stay deferred.

Use a dedicated read-only Microsoft Graph client with separate server-side
credentials and one explicitly configured organization/mailbox/folder. Use
folder delta tracking with durable page checkpoints and immutable message IDs.
Persist routed deliveries before advancing a page checkpoint. Resume pending
and retryable deliveries without duplicating an import. Retain no email bodies,
subjects, tokens or signed download URLs in public reports or audit data.

Operators configure exact sender addresses, optional subject matching, filename
patterns and a pinned active vendor profile. Multiple worksheet rules can use
one workbook. Conflicting vendor routing or multiple candidate files/links
require review. No description-based mapping is introduced.

Only HTTPS direct downloads from explicitly approved public hosts are fetched.
Validate redirects and pin the connection to a validated public IP with TLS
hostname verification to prevent private-network requests and DNS rebinding.
Bound file sizes, redirects and timeouts. Do not send Graph credentials to
vendor sites. Login pages and unsupported/ambiguous links require review.

Re-use receive_upload and process_import: raw byte retention, source-sheet
idempotency, matching, snapshots and availability events retain their contracts.
Serialize mailbox runs across replicas with a PostgreSQL advisory lock. Pin
delivery profile versions and refuse older delayed files once a newer file has
successfully applied for that source. A failed/missing/irregular email never
marks inventory out of stock. Show last received and successfully applied dates;
staleness thresholds are optional per source, not a universal daily deadline.

Activation is disabled by default. Creating an application registration or
granting mailbox access is a separate administrator action requiring the real
mailbox and scoped permission. Existing Entra sign-in permissions do not grant
mailbox access. Source configuration is audited and role restricted.

References:
- https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/attachment-get?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/outlook-immutable-id
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow
- https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac
