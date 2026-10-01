# Deployment

Status: **Local Docker stack validated; hosted deployment is a separate phase
and has not been accepted by this document.** The backend and frontend images
build in the Compose CI job, and production start-up guards are covered by the
automated suite. The platform-specific notes below are a deployment reference,
not evidence that the current Milestone 1 revision is running in a client-owned
hosted environment.

Before a hosted environment is accepted, verify the live Amazon and Nineyard
integrations, scheduler, managed PostgreSQL persistence, backups, logging, and
controlled failure recovery on that environment. Record those results in a
separate deployment acceptance report.

The proposed scope, prerequisites, sequence, and acceptance tests are in
[hosted-deployment-proposal.md](hosted-deployment-proposal.md).

Last updated: 2026-10-01

---

## Read this first: production access control is required

Do not expose this application publicly using the local development sign-in.
The settings validator correctly refuses `DEV_AUTH_ENABLED=true` when
`APP_ENV=production`. Milestone 1 now contains operational data and mutation
endpoints, so a hosted environment must have either:

- the accepted production identity-provider integration; or
- private platform/network access controls that restrict the whole staging
  environment to named client and delivery-team users.

Publishing the web UI while leaving the API public is not sufficient. Apply
the protection to both services, permit only the intended health checks, and
complete an authorization test before adding live credentials. See
[security.md §7](security.md) for the Microsoft Entra ID design.

---

## What needs hosting

Three services, not one:

| Service | Shape | Notes |
|---|---|---|
| `backend` | FastAPI, long-running | Needs a real process — not serverless functions; one scheduler owner initially |
| `frontend` | Next.js standalone | Public browser URL only after access control is in place |
| PostgreSQL 16 | Managed | Needs private connectivity, persistence, backups, and restore testing |
| Retained file storage | Persistent volume or object storage | Must survive application redeploys and match database references |

A dedicated scheduler/worker service is recommended before scaling the API
beyond one replica. Until then, run one API process so scheduled jobs are not
registered more than once.

---

## Railway

### The monorepo trap

Railway's auto-detector (Railpack) scans the **repository root**. This repository
has no `package.json`, no `pyproject.toml` and no `Dockerfile` there — the
applications live in `backend/` and `frontend/`. So detection fails before any
build starts, with output listing only the root files it could see:

```
[railway] prepare railpack-v0.39.0
├── .env.example
├── .gitignore
├── CLAUDE.md
├── Makefile
├── README.md
└── tasks.ps1
Failed to build an image.
```

**The fix is to set a Root Directory per service.** Each becomes its own service
pointed at its own subdirectory, where the `Dockerfile` and `railway.json` are.

### Services to create

Create **three** services in one Railway project.

**1. PostgreSQL** — add the managed Postgres plugin. Nothing to configure.

**2. `backend`**

| Setting | Value |
|---|---|
| Source | This repository |
| **Root Directory** | `backend` |
| Builder | Dockerfile (picked up from `backend/railway.json`) |
| Health check path | `/health` |
| Pre-deploy command | `alembic upgrade head` |

**3. `frontend`**

| Setting | Value |
|---|---|
| Source | This repository |
| **Root Directory** | `frontend` |
| Builder | Dockerfile (picked up from `frontend/railway.json`) |
| Health check path | `/` |

Generate a public domain for both under Settings → Networking.

### Environment variables

**backend**

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
LOG_FORMAT=json

# Reference Railway's Postgres service. The scheme is normalised by the
# application, so the plugin's own variable can be used as-is.
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Generate one per environment:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
AUTH_JWT_SECRET=<generated, at least 32 characters>

# Both refused in production by the settings validator. Listed so nobody
# "helpfully" turns them on.
DEV_AUTH_ENABLED=false
EXPOSE_ERROR_DETAILS=false

# Must be the frontend's public origin, or every browser call fails CORS.
CORS_ALLOW_ORIGINS=https://<frontend-domain>
```

**frontend**

```dotenv
NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>
```

Do not set `PORT` on either service. Railway injects it, and both images honour
it.

---

## Five things that break a first deploy

Each of these was a real defect in this repository, found by building the images
locally and fixed on 2026-09-08.

### 1. `NEXT_PUBLIC_API_BASE_URL` is baked at build time

`NEXT_PUBLIC_*` values are inlined into the browser bundle when Next.js builds,
not read at runtime. Setting it only as a runtime variable produces a frontend
that calls `http://localhost:8000` from your client's browser.

`frontend/Dockerfile` declares it as an `ARG`. Railway passes service variables
as build arguments, so setting it as a service variable is enough — but if a
platform does not, it must be passed explicitly:

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com frontend
```

The symptom is a frontend that loads perfectly and shows "API unreachable".

### 2. The container must listen on `$PORT`

Managed platforms assign a port and route to it. `CMD` in exec form does **not**
expand environment variables, so a hardcoded port means the platform routes to a
port nothing is bound to and the service never becomes healthy.

`backend/Dockerfile` therefore uses shell form:

```dockerfile
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

The frontend needs no equivalent: the Next standalone server reads
`process.env.PORT` itself.

### 3. `DATABASE_URL` arrives without a driver

Railway, Render and Heroku all inject `postgres://…` or `postgresql://…`.
SQLAlchemy picks its driver from the scheme, and without `+psycopg` it reaches
for psycopg2 — which is not installed — so the app dies at first connection with
a `ModuleNotFoundError` that says nothing about configuration.

`app/core/config.py` normalises the scheme, so the platform's variable can be
used as-is:

| Injected | Used |
|---|---|
| `postgres://…` | `postgresql+psycopg://…` |
| `postgresql://…` | `postgresql+psycopg://…` |
| `postgresql+asyncpg://…` | unchanged — an explicit driver is respected |

### 4. `npm ci` needs retries on a flaky network

A dropped connection part-way through the dependency install is the most common
way a container build fails on a busy CI runner. The default is a single attempt,
which turns a transient reset into a hard build failure — observed locally as:

```
npm error code ECONNRESET
npm error network aborted
```

`frontend/Dockerfile` therefore passes explicit retry and timeout flags to
`npm ci`. If a build still fails this way on the platform, it is the network
rather than the image.

### 5. Migrations must run before the app serves

The schema is not created by the application. Set `alembic upgrade head` as the
**pre-deploy command** so it runs once per release, before the new container
takes traffic. Running it as part of the start command instead means every
replica races to migrate.

---

## Verifying a deployment

```bash
# 1. Liveness — the process is up
curl https://<backend-domain>/health

# 2. Readiness — it can reach PostgreSQL
curl https://<backend-domain>/api/v1/health
#    Expect: {"status":"ok","dependencies":[{"name":"postgresql","status":"ok"}]}

# 3. Auth is enforced
curl -i https://<backend-domain>/api/v1/auth/me
#    Expect: 401 with {"error":{"code":"unauthenticated","request_id":"..."}}

# 4. The dev token endpoint is absent in production
curl -i -X POST https://<backend-domain>/api/v1/auth/dev-token \
     -H 'Content-Type: application/json' -d '{"email":"x@y.test"}'
#    Expect: 404
```

Then open the frontend. The dashboard's **System status** card should read "All
systems operational". If it reads "Offline", `NEXT_PUBLIC_API_BASE_URL` was wrong
at build time — see gotcha 1, and note that fixing the variable requires a
**rebuild**, not a restart.

---

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs three
independent jobs on every push and pull request. A newer push to the same
branch cancels the older run.

| Job | Runner | What it proves |
|---|---|---|
| `backend` | Ubuntu, Python 3.12, `postgres:16-alpine` service container | `ruff format --check`, `ruff check`, `mypy` (strict), `pytest -q`. `DATABASE_URL` points at the service container; `tests/integration/conftest.py` derives `prms_test` from it and creates the database itself, so the integration suite runs rather than skips. pip is cached on `backend/pyproject.toml`. |
| `frontend` | Ubuntu, Node 24 | `npm ci`, `eslint`, `tsc --noEmit`, `next build`. npm is cached on `frontend/package-lock.json`. |
| `compose-smoke` | Ubuntu with Docker | Copies `.env.example` to `.env`, runs `docker compose up -d --build` on `infra/docker-compose.yml`, waits up to 120 s for `GET /api/v1/health` to return 200 (which requires PostgreSQL to be reachable from inside the `api` container), runs `alembic upgrade head` then `alembic check` inside the container, and confirms the `web` container answers on port 3000. On failure it dumps `docker compose logs`; it always runs `docker compose down -v`. |

The smoke job matters more than the other two: it is Milestone 1 exit
criterion #1 ([milestone-1-scope.md §4](milestone-1-scope.md)) — "`docker
compose up` yields a working Postgres + API + web stack from a clean checkout
using only `.env` variables." It independently repeats the clean Docker proof
outside the developer workstation and builds both application images.

The `backend` and `frontend` jobs run the same commands as `.	asks.ps1 check`
and `make check`; a green local run should mean a green CI run, and a
difference between the two is a real finding.

---

## Other platforms

| Platform | Fit |
|---|---|
| **Render** | Same shape as Railway. Avoid the free tier — it spins down, giving a ~30s cold start on the first request a client makes. |
| **Fly.io** | Docker-native with volumes; good if the deployment should sit near the client geographically. More operational knowledge required. |
| **Azure Container Apps** | The strongest production case, *if* Microsoft Entra ID is confirmed (assumption A21): Container Apps, Database for PostgreSQL Flexible Server, Blob Storage for B5, and Entra ID in one place with no cross-cloud federation. |
| **A single VPS** | `infra/docker-compose.yml` already works. Cheapest and most controlled; you own patching, backups and TLS. |
| **Vercel / Cloudflare Pages** | Frontend only — they cannot host FastAPI or PostgreSQL. Viable for showing screens, but the status card will read "Offline" unless the API is hosted elsewhere too. |

---

## Local Docker Compose

`infra/docker-compose.yml` runs all three locally:

```bash
cp .env.example .env
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

**Port conflicts are likely.** A natively installed PostgreSQL holds 5432, and
other projects may hold 8000 or 3000. Override in `.env`:

```dotenv
POSTGRES_HOST_PORT=5434
API_HOST_PORT=8010
WEB_HOST_PORT=3000
```

The container reaches PostgreSQL at `postgres:5432` internally regardless of the
published host port.

---

## Pre-deployment checklist

- [ ] `AUTH_JWT_SECRET` generated fresh for this environment, at least 32 characters
- [ ] `APP_ENV=production`
- [ ] `DEV_AUTH_ENABLED` and `EXPOSE_ERROR_DETAILS` both false
- [ ] `CORS_ALLOW_ORIGINS` contains the frontend's public origin
- [ ] `NEXT_PUBLIC_API_BASE_URL` set **at build time** to the backend's public URL
- [ ] Root Directory set per service (`backend`, `frontend`)
- [ ] `alembic upgrade head` configured as the pre-deploy command
- [ ] Database backups enabled on the managed instance
- [ ] Understood that **no authentication mechanism exists in production yet**
