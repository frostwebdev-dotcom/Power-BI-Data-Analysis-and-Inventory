# Nineyard Integration

Status: **Read-only diagnostic only.** No synchronisation service exists yet,
and none should be written until this document's open questions are answered
from observation rather than assumption (ADR 0007).
Last updated: 2026-09-08

---

## 1. Why a probe before a client

Blocking question **B1** asked for the Nineyard API specification. What arrived
is the authentication endpoint and four read-only endpoint groups — enough to
*ask* the API what it returns, not enough to *assume* it.

Everything else a synchronisation service needs is still unknown: the response
envelope, the field names, whether paging exists and what it is called, whether
the integration account can read all four groups. Guessing at those and writing
a sync service would produce code that compiles, passes its own tests, and is
wrong in a way nobody discovers until real data flows through it.

So the order is: probe, record, then build. The probe's findings go into
[nineyard-field-mapping.md](nineyard-field-mapping.md), and the anti-corruption
layer is written only once that document has no `UNVERIFIED` placeholders left
in the fields the sync depends on.

---

## 2. What is actually known

Only this. Everything else in this repository treats Nineyard as unknown.

### Authentication

```
POST https://backyard.nineyard.com/api/OAuth/UsernameToken
Content-Type: application/json

{
  "email": "<email>",
  "password": "<password>",
  "companyId": <integer>
}
```

Response fields expected: `accessToken`, `expiresIn`, `expires`.

Even these are treated as *expected rather than confirmed*: the client accepts a
response missing `expiresIn` or `expires` and reports them as absent, because
"documented" and "observed" are different things and only the probe can close
that gap.

Protected requests use `Authorization: Bearer <accessToken>`.

### Read-only endpoint groups under investigation

| Group | Path |
|---|---|
| Items | `GET /api/Items` |
| Skus | `GET /api/Skus` |
| Vendors | `GET /api/Vendors` |
| PurchaseOrders | `GET /api/PurchaseOrders` |

### The published OpenAPI specification

**Discovered 2026-09-08:** the full spec is publicly readable, no credentials
required, at

```
https://backyard.nineyard.com/swagger/v1/swagger.json
```

`Nineyard.Rest.Api` v2.0, OpenAPI 3.0.1 — 65 paths and 117 schemas. A copy is
saved at `storage/diagnostics/nineyard/swagger-v1.json` (git-ignored).

Note that `https://backyard.nineyard.com/` is the **API and its documentation**,
not the Nineyard application UI. There is no login there; account credentials
come from the Nineyard application the business actually uses.

The spec answers most of what was previously unknown — envelope shapes, field
names, types, nullability, and paging. Those findings are recorded in
[nineyard-field-mapping.md](nineyard-field-mapping.md), marked `DOCUMENTED`
rather than `CONFIRMED`, because a spec describes a contract and only a probe run
describes behaviour.

### What the specification does not answer

Only `200` responses are documented, so **every error shape is unknown**. Nor can
it say which endpoints *this account* may read, what the real `Content-Type`
headers are, the default page size, or whether `GET /api/Items` returns the whole
catalog or only QuickBooks-relevant rows.

Three questions need a person at Nineyard, not a probe:

1. **There is no `catalogItemNumber` field anywhere in the API.** The only
   candidate for the primary business reference CLAUDE.md §5 requires is
   `itemId`, an internal surrogate key. This is now the single most important
   open question.
2. The Items↔Skus relationship is **many-to-many with a quantity** — bundles and
   multi-packs — which `marketplace_listings` cannot represent as designed.
3. Four inventory quantities exist (`qtyOnHand`, `localstock`, `inboundStock`,
   `totalStock`) with no documented definitions. Availability detection depends
   on choosing correctly.

### A safety finding from the specification

The API has **45 write endpoints** against 27 reads, several sharing a path
prefix with the ones we read:

```
POST /api/Items/UpdateInventory      POST /api/Items/IncreaseInventory
POST /api/Items/DecreaseInventory    POST /api/Items/LocalStockRecalc
POST /api/Skus                       POST /api/Shipping/RemoveShipmentByCompanyId
POST /api/ShipmentBoxes/DeleteBoxFromShipment
```

`GET /api/Items` and `POST /api/Items/UpdateInventory` differ by a path suffix.
The read-only-by-construction design in §3 is therefore load-bearing rather than
decorative: the client has no method capable of issuing any of these.

---

## 3. Safety design

This tool reads. It cannot write, and that is a structural property rather than
a policy.

### Mutation is impossible, not merely discouraged

`NineyardClient` exposes exactly three public methods: `authenticate`, `get`,
and `close`. There is no `post`, `put`, `patch` or `delete`, and no method that
takes an HTTP verb as an argument. The one POST the client makes is
authentication, and its path is a module constant that cannot be redirected.

A test asserts the public method set, so adding a mutating verb breaks the build
rather than passing review unnoticed.

```python
def test_get_is_the_only_public_request_method() -> None:
    assert public_methods(NineyardClient) == {"authenticate", "close", "get"}
```

The CLI reinforces this: `--endpoints` accepts only names from the read-only
allowlist, so `--endpoints UpdateInventory` is refused as an unknown endpoint.

### Credentials

Read from environment variables only, via the single typed settings object.
`NINEYARD_PASSWORD` is a `SecretStr`, so it cannot be printed by an f-string, a
repr, or a stack trace.

The email, the password and the token are **never** logged. The only
token-derived value that reaches a log is a 12-character SHA-256 prefix — enough
to tell two runs apart, useless as a credential. It is explicitly allowlisted in
the redaction filter for that reason.

A test runs authentication through the real logging pipeline and asserts that
none of the three appear in the output.

### Diagnostic output carries no data

Sample values are replaced by *descriptions of themselves*:

| Real value | Recorded as |
|---|---|
| `"012345678905"` | `"<str len=12 digits>"` |
| `"Blue Widget 12-pack"` | `"<str len=19 text>"` |
| `"Acme Distribution"` | `"<str len=17 text>"` |
| `998877` | `"<int>"` |
| `true` | `true` |

The descriptor keeps the diagnostic signal — a 12-digit numeric string is
recognisably a UPC — and carries none of the data. Booleans and nulls are kept
literal because they identify nobody, and knowing whether a flag is a real
boolean or a `"Y"`/`"N"` string is exactly what the probe is for.

Field *names* are always preserved verbatim. They are the point of the exercise.

Samples are written under `storage/diagnostics/nineyard/`, which `.gitignore`
excludes wholesale — a second line of defence behind sanitisation.

### Retries are for transient failures only

| Condition | Retried |
|---|---|
| Connection error, timeout | Yes |
| 429, 500, 502, 503, 504 | Yes |
| 401, 403, 404, and every other 4xx | **No** |

Retrying a 401 repeats the same mistake more slowly. Backoff is exponential with
jitter, honours `Retry-After` when the server sends a numeric one, and is capped
at 30 seconds per wait so a diagnostic run cannot hang.

### Status handling

Each status gets its own exception carrying actionable guidance:

| Status | Meaning for the probe |
|---|---|
| 401 | Credentials rejected, or the token expired mid-run. `companyId` being wrong is a plausible cause. |
| 403 | Authenticated but not permitted. **A useful finding** — record which groups the account can read. |
| 404 | The path is wrong. On a collection endpoint this means the path, not empty data. |
| 429 | Rate limited. `Retry-After` is honoured and reported. |
| 5xx | Nineyard's side. Retried, then reported. |
| non-JSON body | Usually a login redirect or a proxy. Reported with the content type. |

A failing endpoint is recorded and the run continues — discovering that the
account reads Items but not PurchaseOrders is precisely the kind of finding this
exists to produce. Only an authentication failure aborts, because four identical
401s are noise rather than data.

---

## 4. Running it

### First, without credentials

`--dry-run` prints the exact request plan and opens no socket. A test asserts
that constructing an HTTP client during a dry run fails, so this is a guarantee
rather than an intention.

```bash
cd backend
.venv/Scripts/python -m app.cli.nineyard_probe --dry-run     # Windows
.venv/bin/python -m app.cli.nineyard_probe --dry-run         # macOS / Linux
```

### Then, after adding credentials

Add to `.env` in the repository root — never to any tracked file:

```dotenv
NINEYARD_BASE_URL=https://backyard.nineyard.com
NINEYARD_EMAIL=you@example.com
NINEYARD_PASSWORD=your-nineyard-password
NINEYARD_COMPANY_ID=1234
# Required by the catalog sync (not by the diagnostic probe):
NINEYARD_ACCOUNT=Your Exact Seller Account
```

**The exact safe command:**

```bash
cd backend
.venv/Scripts/python -m app.cli.nineyard_probe --save-samples
```

That authenticates once, issues four GETs, prints a report, and writes a
sanitised JSON file to `storage/diagnostics/nineyard/`.

### Narrowing the run

```bash
# One group at a time, which is the gentler way to start
.venv/Scripts/python -m app.cli.nineyard_probe --endpoints Items

# Ask for a small page once a paging parameter name is known
.venv/Scripts/python -m app.cli.nineyard_probe --endpoints Items --page-param PerPage=5

# Machine-readable output
.venv/Scripts/python -m app.cli.nineyard_probe --json
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every requested endpoint returned a readable response |
| 1 | Configuration problem — credentials missing |
| 2 | Authentication failed |
| 3 | One or more endpoints failed |

---

## 5. What the probe reports

Per run: token fingerprint, `expiresIn`, `expires`, and every top-level key the
token response actually contained.

Per endpoint: HTTP status · content type · elapsed time · top-level type and
keys · which key held the record array · record count · field names with
per-field presence counts · paging-metadata candidates · a sanitised sample.

Two conventions keep it honest:

- **Candidates are labelled as candidates.** The probe does not decide that
  `totalCount` is the record total. It reports that a key by that name exists
  and holds an integer.
- **Presence counts, not nullability.** A field in 2 of 3 records is reported as
  `(in 2/3 records)`. That is an observation. Concluding the column is nullable
  needs more than one page.

---

## 6. Catalog synchronization

The published OpenAPI contract is implemented by
`NineyardCatalogReader`: `/api/Items` is followed by `Page`/`PerPage` using the
live-verified maximum of 200 records. The tenant-wide `/api/Skus` history is
not traversed: the live account still returned full 100-record pages at page
2,000. Instead, active Amazon listing SKUs already stored locally are resolved
with the exact `Account` + `Sku` filters and joined through
`/api/Skus/GetSkuMappings`. A stale mapping ID that returns 404 is isolated and
skipped without discarding valid IDs from the batch. Repeated pages are
rejected rather than looping.

`run_nineyard_sync` materializes the complete remote snapshot before opening
the catalog-write transaction. It upserts products by the documented `itemId`,
normalizes valid vendor UPCs, stores content-addressed raw payloads, and creates
`AMAZON_SKU` identifiers for one-item SKU mappings. Multi-item bundles are
reported as skipped because the Milestone 1 listing model is one product per
SKU and must not guess.

Set `NINEYARD_ACCOUNT` to the exact seller account value shown by Nineyard.
This is required when `NINEYARD_ENABLED=true`; it is intentionally separate
from the numeric company ID used for authentication.

Run manually inside Docker, after Amazon listings have been imported:

```bash
python -m app.cli.amazon_poc sync-listings
python -m app.cli.nineyard_sync
python -m app.cli.amazon_poc sync-listings
```

The first listings run supplies the bounded set of seller SKUs. The Nineyard
run creates catalog, UPC, and one-item Amazon-SKU identifiers. The final
listings run applies those identifiers through the normal deterministic
matching rules.

Set `NINEYARD_ENABLED=true` to schedule the same service; the default interval
is 1,440 minutes.

Live read-only verification on 2026-09-29 confirmed authentication, Items,
Skus, exact Account+Sku filtering, and GetSkuMappings. The account reported
7,026 catalog items. Database materialization and the final listings rematch
must still be demonstrated in the deployment environment because this
workspace has no PostgreSQL service or Docker daemon.

The probe is not a prototype of the client. It is throwaway instrumentation, and
the real client should be written against evidence rather than by promoting this
code.

---

## 7. Files

| Path | Purpose |
|---|---|
| [`app/integrations/nineyard/client.py`](../backend/app/integrations/nineyard/client.py) | Read-only HTTP client, auth, retries, status mapping |
| [`app/integrations/nineyard/probe.py`](../backend/app/integrations/nineyard/probe.py) | Response description; invents nothing |
| [`app/integrations/nineyard/sanitize.py`](../backend/app/integrations/nineyard/sanitize.py) | Structure-preserving value redaction |
| [`app/integrations/nineyard/errors.py`](../backend/app/integrations/nineyard/errors.py) | Error taxonomy with guidance |
| [`app/cli/nineyard_probe.py`](../backend/app/cli/nineyard_probe.py) | CLI entry point |
| [`app/integrations/nineyard/catalog.py`](../backend/app/integrations/nineyard/catalog.py) | Catalog pagination and SKU relationship reader |
| [`app/services/nineyard_sync.py`](../backend/app/services/nineyard_sync.py) | Atomic product, identifier and payload synchronization |
| [`app/cli/nineyard_sync.py`](../backend/app/cli/nineyard_sync.py) | Manual synchronization entry point |
| `tests/unit/test_nineyard_*.py` | Client, probe, sanitization and catalog-reader tests against mocked transports |
| `tests/integration/test_nineyard_sync.py` | PostgreSQL upsert, idempotency, raw retention and rollback proof |

No test in the suite makes a network request.
