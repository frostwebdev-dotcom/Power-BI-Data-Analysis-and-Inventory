# ADR 0016 — Entra sign-in with explicit enrollment and local roles

Status: Implemented in the sign-in change; live Entra verification pending.
Date: 2026-10-07

## Context

Azure staging contains business data but the only existing login issues a
passwordless development token. The intended identity provider is Microsoft
Entra ID. Existing routes and audit attribution use a database-backed Principal.

## Decision

Entra authenticates; PRMS remains authoritative for roles. An operator explicitly
enrolls a tenant/object ID pair into one organization's user account. No email
claim, automatic registration, JIT role grant, or SCIM endpoint grants access.
One Entra identity belongs to one PRMS account in this release. Supporting an
organization chooser for multiple memberships is a separate change.

The API accepts only single-tenant v2 RS256 access tokens for its own application
ID, from the configured browser application, with the access_as_user delegated
scope. ID tokens and application-only tokens are rejected. Microsoft keys are
cached with bounded lifetime and a network timeout. Active user, organization,
and organization-scoped roles are checked on each request.

The browser uses MSAL authorization code/PKCE and sessionStorage. API requests
acquire access tokens silently; interactions occur only on an explicit sign-in
click. Development login remains available for local and CI testing, and is
refused in staging and production. Public auth configuration is served by the
API, so no browser secret or additional Docker build argument is required.

## Consequences and acceptance

Enrollment/role grants are audited and require operator access to the database.
No user is provisioned merely by authenticating at Microsoft. PRMS account or
organization deactivation takes effect on the next API request. Entra-side
revocation can remain subject to access-token lifetime; this implementation does
not claim immediate Microsoft session revocation or Continuous Access Evaluation.

Verify negative token cases, unapproved identities, cross-tenant roles, disabled
accounts/organizations, enrollment conflicts, migration drift, browser login UI,
and the existing local walkthrough. Live Microsoft login requires two Entra app
registrations, consent, an explicitly enrolled user, and deployment of both images.
