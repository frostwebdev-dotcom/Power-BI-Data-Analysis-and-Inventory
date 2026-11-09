# ADR 0021 — Public application information for Gmail consent

Status: accepted for implementation, 2026-11-09.

## Context

The owner completed the local Gmail authorization helper and configured Azure
secrets. The API revision `gmail-setup-1` is running, and the administrator page
recognizes Gmail while polling remains paused with no vendor sources. These are
owner-supplied observations, not evidence of a successful Azure mailbox poll.
The Google OAuth project remains in Testing. Its Publish app control requires
valid branding, a public application homepage and a public privacy-policy URL.
The existing frontend root is the authenticated dashboard.

## Decision and scope

Add two static, publicly accessible information documents at `/app-info.html`
and `/privacy.html` in the existing Next.js public directory. Describe the
implemented inventory application, read-only Gmail permission, sender routing,
stored import data, retention, revocation and administrator-assisted deletion
requests. Link the privacy notice from sign-in and automatic imports. Use the
support email shown in Google's consent screen and the organization's existing
administrator contact rather than inventing or publishing a personal address.

These documents contain no customer records and request no APIs or credentials.
Keep the authenticated application shell and every API authorization rule intact.
No new dependencies, database changes, public OAuth callback, mailbox permission
or polling activation are part of this change. Terms of service are not invented.

Working public URLs do not establish Google verification or ownership of the
Microsoft-hosted domain. Complete Google's authorized-domain and applicable
verification requirements separately. Obtain a fresh grant after moving out of
Testing; do not infer that a Testing-issued token loses its expiry automatically.

## Acceptance

- Both public documents return readable HTML without login or JavaScript.
- The homepage identifies Global Supplies PRMS and links its privacy policy.
- Sign-in and inventory-mailbox settings link the public data-use notice.
- Visiting a public document never grants access to an operational route.
- Frontend lint, type checking, production build and browser regression pass.
- Deployment preserves the existing private Gmail settings and paused polling.
- Record real deployment results; no Google publishing or vendor acceptance is
  claimed by merely shipping these documents.

## References

- https://support.google.com/cloud/answer/15549049
- https://support.google.com/cloud/answer/13464321
- https://developers.google.com/identity/protocols/oauth2#expiration
- https://developers.google.com/terms/api-services-user-data-policy
