# Security

How configuration, secrets, authentication, and auditing work today, and what
changes when Microsoft Entra ID is introduced.

Status: **Implemented in source** — security foundation and Entra sign-in.
Live Entra verification remains pending deployment and app registration.
Last updated: 2026-10-07

---

## 1. Configuration and secrets

All configuration is read from environment variables through one typed object,
[`app/core/config.py`](../backend/app/core/config.py). Nothing reads
`os.environ` directly, so there is a single place to audit.

### Credentials are typed, not stringly

Every credential is a Pydantic `SecretStr`:

| Setting | Holds |
|---|---|
| `DATABASE_URL` | A DSN, which embeds a password |
| `AUTH_JWT_SECRET` | HS256 signing key for development tokens |
| `NINEYARD_API_KEY` | Unset until blocking question B1 is answered |

`SecretStr` renders as `**********` in reprs, `str()`, and JSON dumps, so the
common accident — logging the settings at startup, or a credential surfacing in
an exception repr — cannot leak. Reading the real value requires an explicit
`.get_secret_value()`, which makes every such use greppable. There are currently
three, all at genuine boundaries (engine creation, Alembic, JWT signing).

Tests assert this: `test_settings_secrets.py` checks `repr`, `str`,
`model_dump_json`, `safe_dump`, and f-string interpolation, and includes a
tripwire that fails if a credential is ever added as a plain `str`.

### Production refuses to start on development defaults

Three misconfigurations are invisible at runtime until they are exploited, so
the application refuses to boot with any of them when `APP_ENV` is
`production`:

- `AUTH_JWT_SECRET` is still the shipped placeholder (recognised by value)
- `DEV_AUTH_ENABLED` is true
- `EXPOSE_ERROR_DETAILS` is true

An HS256 signing key shorter than 32 characters is rejected in every
environment (RFC 7518 §3.2).

### What must never be committed

[`.gitignore`](../.gitignore) blocks, verified by an actual `git check-ignore`
run rather than by inspection:

| Category | Patterns |
|---|---|
| Environment files | `.env`, `.env.*` (except `.env.example`) |
| Keys and certificates | `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa*`, `id_ed25519*` |
| Token caches | `*.token`, `token_cache*.json`, `.msal_cache*`, `.azure/`, `.aws/`, `.netrc` |
| Credential files | `credentials.json`, `service-account*.json`, `secrets/` |
| Imported client data | `*.csv`, `*.xlsx`, `*.xls`, `*.xlsm`, `*.pbix`, `data/`, `exports/`, `extracts/` |
| Retained import files | `storage/**` except the `.gitkeep` markers |

Client data is blocked **by extension** so a vendor file dropped into the repo
to "try something" cannot be committed by accident. Test fixtures under
`**/fixtures/**` are allowed back in explicitly.

`.env.example` contains placeholders only. The development signing key in it is
deliberately the value the application recognises and refuses in production.

---

## 2. Log redaction

Credentials reach logs by accident, not by design: a request-header dump, an
exception repr containing a DSN, a third-party library logging a connection
string. No amount of discipline at call sites catches all of those, so redaction
happens at the **sink** — [`app/core/redaction.py`](../backend/app/core/redaction.py),
applied to every log event before rendering.

Two layers:

**Key-based.** A mapping key that looks sensitive has its value replaced with
`***REDACTED***`, recursively through nested dicts and lists. Matched
case-insensitively on substrings including `authorization`, `password`,
`secret`, `token`, `api_key`, `credential`, `cookie`, `database_url`, `dsn`.
A short allowlist keeps genuinely useful fields readable — `token_type`,
`expires_in`.

**Value-based.** Strings are scanned for credential *shapes* regardless of the
key they arrived under:

| Pattern | Example | Result |
|---|---|---|
| Bearer / Basic / Digest | `Authorization: Bearer abc123…` | scheme kept, credential masked |
| Bare JWT | `eyJhbGci….eyJzdWI….sig` | masked |
| URL credentials | `postgresql://prms:pw@db:5432/prms` | password masked, host kept legible |
| Inline assignment | `api_key=sk-live-…` | value masked |

Redaction is applied on **both** logging paths: the structlog processor chain,
and the stdlib `foreign_pre_chain` used by third-party libraries that never
touch structlog. A driver logging its own DSN is exactly the leak that only the
second path catches.

The same functions redact audit payloads, so a secret cannot land in
`audit_events.before` / `after` either.

---

## 3. Correlation IDs and error handling

Every request gets an id — taken from an inbound `X-Request-ID` header or
generated — bound to a context variable and to the log context, echoed in the
response header, and written onto every audit row the request produces. One id
ties a support question to its log lines and its audit trail.

Errors all use one envelope
([`app/core/errors.py`](../backend/app/core/errors.py)):

```json
{
  "error": {
    "code": "unauthenticated",
    "message": "Authentication is required.",
    "request_id": "8f0e…",
    "details": { }
  }
}
```

Application errors, Starlette's own 404s and 405s, validation failures, and
unhandled exceptions all produce this shape, so a client that can parse one
failure can parse all of them. `details` passes through redaction.

### Stack traces stop at the boundary

An unhandled exception is logged server-side **with** its traceback and the
correlation id. The client receives the id, a generic message, and nothing else.
A leaked traceback discloses file paths, library versions, and sometimes query
fragments containing customer data.

`EXPOSE_ERROR_DETAILS=true` adds the exception type and message to the response
for local debugging. It is refused in production by the settings validator, and
the handler checks `is_production` again independently.

Tests assert the production response contains no traceback, no exception class
name, and no `.py` path.

---

## 4. Local development authentication

Local development can use the existing email-only token endpoint. Staging and
production use Microsoft Entra (section 7); PRMS never stores login passwords.

`POST /api/v1/auth/dev-token` issues a short-lived HS256 token for an existing
active user, **without a password**. It is protected by two independent locks:

1. `DEV_AUTH_ENABLED` must be true — off by default.
2. The environment must be local/dev/development/test/CI, and AUTH_BACKEND must
   be dev. Staging and production refuse DEV_AUTH_ENABLED at startup.

Disabled, it answers **404**, not 403: an endpoint that should not exist here
should not advertise that it exists elsewhere. An unknown or inactive email gets
the same 404, because whether an address is registered is not public
information.

### Using it locally

```bash
# .env
DEV_AUTH_ENABLED=true
AUTH_JWT_SECRET=insecure-development-signing-key-change-me

# Obtain a token (the user must already exist)
curl -X POST http://localhost:8000/api/v1/auth/dev-token \
     -H 'Content-Type: application/json' \
     -d '{"email":"buyer@example.test"}'

# Use it
curl http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

### Token validation

`algorithms=["HS256"]` is pinned explicitly. Accepting the token's own `alg`
header is the classic JWT vulnerability — a token claiming `alg: none` would
otherwise validate with no signature at all. `iss`, `aud`, `exp` and `iat` are
required and checked. Every validation failure returns the same message:
distinguishing "bad signature" from "wrong audience" only helps an attacker.

---

## 5. Roles and authorization

Four system roles, seeded per organization and marked `is_system`:

| Role | Intended for |
|---|---|
| `ADMIN` | Full access, including user and role administration |
| `PURCHASING_MANAGER` | Approves product mappings; manages the OOS watchlist |
| `DATA_OPERATOR` | Manages vendors and import profiles; runs imports |
| `VIEWER` | Read-only access to catalog, imports, and reports |

Roles are tenant-scoped (`roles.organization_id`), so an organization can add
its own without affecting anyone else. Only these four are reasoned about by the
application.

Routes state their requirement declaratively:

```python
@router.post(
    "/vendors",
    dependencies=[Depends(require_roles(RoleCode.DATA_OPERATOR))],
)
def create_vendor(...): ...
```

`ADMIN` satisfies every requirement — it exists precisely so an administrator
cannot be locked out of their own system.

### Authorization always reads the database

Roles are loaded fresh on every request, **never** trusted from token claims.
The token carries a `roles` claim for debuggability, but it confers nothing: a
role revoked a minute ago must not stay effective until the access token
expires. An integration test proves the revocation takes effect on the very next
request, and that a deactivated user is rejected despite holding a valid token.

---

## 6. Auditing

[`app/services/audit.py`](../backend/app/services/audit.py) records important
actions per CLAUDE.md §6. Three properties, each enforced rather than assumed:

- **Transactional.** `record()` adds the row to the caller's session and does
  not commit. Wrapping a change and its audit entry in one `transaction()` block
  means they commit or roll back together (ADR 0006). A test proves a rollback
  leaves no audit row behind.
- **Correlated.** The request id is read from the ambient context — and worker
  and CLI code, which has no HTTP request behind it, records rows with a null id
  rather than failing.
- **Clean.** `before` and `after` pass through redaction, and `snapshot()` drops
  sensitive columns entirely rather than masking them, so `password_hash` never
  reaches the trail in any form.

The table itself is append-only, enforced by a database trigger that rejects
`UPDATE` and `DELETE` — including from a direct psql session. See
[database-schema.md §3.8](database-schema.md).

---

## 7. Staging and production authentication: Microsoft Entra ID

[ADR 0016](decisions/0016-entra-sign-in.md) keeps roles in PRMS and requires explicit
operator enrollment. Entra owns credentials and authentication. Two nullable UUID
columns bind an approved local user to an immutable `(tid, oid)` pair. Email
claims are never used to look up or automatically enroll an account. The unique
identity pair allows one PRMS organization per Entra account in this release.

The backend accepts only RS256 v2 delegated access tokens. It checks signature,
issuer, expiry, not-before, issue time, API audience, tenant, browser client, and
`access_as_user` scope. ID tokens and app-only tokens are rejected. A JWKS client
cached per configured tenant retains keys for five minutes and has a five-second
network timeout. No token-controlled URL is fetched. Unknown accounts, inactive
users, and inactive organizations are rejected. Roles are reloaded from the
organization-scoped database assignments on every request.

The browser reads public configuration from `/api/v1/auth/config`, initializes
MSAL, and signs in through a popup on an explicit click. MSAL owns a sessionStorage
cache and silently refreshes access tokens for API calls. Microsoft credentials
are never copied into the application's development localStorage token entry.
Query caches are cleared on sign-in/out to prevent previous-user data reuse.

`AUTH_BACKEND=entra` requires `ENTRA_TENANT_ID`, `ENTRA_API_CLIENT_ID`, and
`ENTRA_SPA_CLIENT_ID`; the API and browser registrations must be different.
`DEV_AUTH_ENABLED` must remain false. The browser requires no client secret.

Provisioning is manual using `python -m app.cli.enroll_entra_user`; enrollment
and its role grant are audited. Login cannot auto-create an account or assign
ADMIN. Operator access to this CLI is privileged access to the application DB.
Entra token claims do not override local grants. Entra account revocation can
remain subject to token lifetime; local account/organization deactivation is
checked immediately on the next request.

See [the staging sign-in runbook](entra-staging-sign-in.md) for registration,
consent, migration, enrollment, and deployment steps. Automated tests use signed
RSA fixture tokens; a successful live Microsoft login is a separate release check.

---

## 8. What is deliberately not built

Honest scope, so nobody assumes protection that is not there:

| Not built | Why |
|---|---|
| Password storage and login | Entra owns credentials; `users.password_hash` is nullable and unused |
| Refresh tokens, revocation lists | Entra's concern |
| Rate limiting / brute-force protection | Belongs at the gateway; no credential endpoint exists to brute-force |
| MFA | Entra's concern |
| Row-level security | Tenant isolation currently rests on repository-layer discipline plus tests — evaluate PostgreSQL RLS before the first real multi-tenant deployment (assumption A19) |
| Field-level encryption | No field yet identified as needing it beyond what `SecretStr` and redaction cover |
| Secret rotation tooling | Environment variables only; rotation is a deployment concern |

---

## 9. Verification

| Guarantee | Test |
|---|---|
| Secrets absent from every settings representation | `tests/unit/test_settings_secrets.py` |
| Production refuses development defaults | `tests/unit/test_settings_secrets.py` |
| Authorization headers redacted, end to end | `tests/unit/test_redaction.py` |
| Third-party log records redacted | `tests/unit/test_redaction.py` |
| Protected endpoints reject unauthenticated requests | `tests/unit/test_auth.py` |
| Forged, expired and `alg: none` tokens rejected | `tests/unit/test_auth.py` |
| Entra token boundaries and staged dev-auth lock | `tests/unit/test_entra_auth.py` |
| Entra enrollment, identity conflicts, and immediate local revocation | `tests/integration/test_entra_auth_flow.py` |
| Untrusted `X-Forwarded-For` cannot reach the database | `tests/unit/test_auth.py` |
| No stack trace in production responses | `tests/unit/test_error_handling.py` |
| Audit records written, correlated, and redacted | `tests/integration/test_audit_service.py` |
| Transactions roll back on failure | `tests/integration/test_transactions.py` |
| Role revocation takes effect immediately | `tests/integration/test_auth_flow.py` |
