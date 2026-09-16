# Purchasing & Replenishment Management System

[![CI](https://github.com/cbfriedman/Power-BI-Data-Analysis-and-Inventory/actions/workflows/ci.yml/badge.svg)](https://github.com/cbfriedman/Power-BI-Data-Analysis-and-Inventory/actions/workflows/ci.yml)

Milestone 1 — data foundation, Nineyard catalog synchronisation, vendor
inventory ingestion, and deterministic product matching.

**Current state:** the database schema (21 tables, one reversible migration),
the audit and security foundation, and a read-only Nineyard diagnostic exist
and pass every quality gate. No vendor CRUD, file ingestion, or matching logic
exists yet. See [docs/phase1-status.md](docs/phase1-status.md) for exactly what
is built and what comes next.

Rules that govern all work in this repository are in [CLAUDE.md](CLAUDE.md).

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| ORM | SQLAlchemy 2 (synchronous, psycopg 3) |
| Migrations | Alembic |
| Database | PostgreSQL 16 |
| Logging | structlog — JSON, with request correlation ids |
| Quality | Ruff, mypy (strict), pytest · ESLint, tsc |
| Local services | Docker Compose |

---

## Layout

```
backend/     FastAPI service — api, core, db, models, schemas,
             repositories, services, integrations, imports, tests
frontend/    Next.js admin interface
infra/       docker-compose.yml
docs/        Scope, architecture, acceptance criteria, status, ADRs
storage/     Retained import files — raw/, processed/, rejected/
```

---

## Quick start with Docker

Prerequisites: Docker Desktop running.

```bash
cp .env.example .env
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

| Service | URL |
|---|---|
| Admin interface | http://localhost:3000 |
| API — liveness | http://localhost:8000/health |
| API — readiness | http://localhost:8000/api/v1/health |
| API docs | http://localhost:8000/api/v1/docs |
| PostgreSQL | `localhost:5432` (user `prms`, database `prms`) |

Stop with `Ctrl+C`, then:

```bash
docker compose --env-file .env -f infra/docker-compose.yml down
```

The database volume `prms_pgdata` is named and survives `down`. Add `-v` to
delete it. Files under `storage/` are bind-mounted from the host and are never
removed by Compose.

### Windows shortcut

```powershell
.\tasks.ps1 up      # build and start
.\tasks.ps1 ps      # status
.\tasks.ps1 logs    # follow logs
.\tasks.ps1 down    # stop
```

---

## Local development without Docker

You still need PostgreSQL. Either run just that container —

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d postgres
```

— or point `DATABASE_URL` at your own instance. When running the API on the
host rather than in Compose, the database host is `localhost`, not `postgres`:

```
DATABASE_URL=postgresql+psycopg://prms:local_dev_only_change_me@localhost:5432/prms
```

### Backend

```bash
# Windows
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -e ".\backend[dev]"
backend\.venv\Scripts\uvicorn app.main:app --reload --app-dir backend

# macOS / Linux
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e "./backend[dev]"
cd backend && .venv/bin/uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

`NEXT_PUBLIC_API_BASE_URL` must be reachable from the **browser**
(`http://localhost:8000`), not from inside the Docker network.

### Signing in (development)

The interface uses the API's development sign-in: the email of a user that
already exists, no password (`DEV_AUTH_ENABLED=true`; production replaces this
— see `docs/security.md`). Seed an organization, four users and one demo
product first:

```bash
cd backend
python -m app.cli.seed_dev          # admin@ buyer@ operator@ viewer@example.test
```

Then sign in at http://localhost:3000 as `admin@example.test`.

### End-to-end walk-through (Playwright)

`frontend/e2e/milestone-1.spec.ts` drives the Milestone 1 exit gate through
the interface — vendor, profile, upload, report, exception, approve, re-import,
watchlist, availability event, audit — against a running, seeded stack:

```bash
cd frontend
npx playwright install chromium
E2E_BASE_URL=http://localhost:3000 npx playwright test
```

The `compose-smoke` CI job runs it against the Docker Compose stack.

---

## Quality gates

Every gate below is expected to pass before any commit.

| Gate | Windows | make | Direct |
|---|---|---|---|
| Backend format | `.\tasks.ps1 format` | `make format` | `ruff format backend` |
| Backend lint | `.\tasks.ps1 lint` | `make lint` | `ruff check backend` |
| Backend types | `.\tasks.ps1 typecheck` | `make typecheck` | `cd backend && mypy` |
| Backend tests | `.\tasks.ps1 test` | `make test` | `cd backend && pytest` |
| Frontend lint | `.\tasks.ps1 frontend-lint` | `make frontend-lint` | `cd frontend && npm run lint` |
| Frontend types | `.\tasks.ps1 frontend-typecheck` | `make frontend-typecheck` | `cd frontend && npm run typecheck` |
| Frontend build | `.\tasks.ps1 frontend-build` | `make frontend-build` | `cd frontend && npm run build` |
| Compose config | `.\tasks.ps1 compose-config` | `make compose-config` | `docker compose --env-file .env.example -f infra/docker-compose.yml config -q` |

Run them all:

```powershell
.\tasks.ps1 check     # Windows
make check            # make available
```

`make` is not installed on Windows by default — `tasks.ps1` is the equivalent.

---

## Database migrations

One migration exists, `506fd0ecc33a` (the initial 21-table schema, see
[docs/database-schema.md](docs/database-schema.md)). It is reversible:
`alembic downgrade base` drops every table and enum type it created, and
`alembic check` confirms the models and the migration agree. `alembic/env.py`
reads `DATABASE_URL` from the environment, so no connection string is ever
committed.

```bash
# Inside the running stack
docker compose --env-file .env -f infra/docker-compose.yml exec api alembic upgrade head
docker compose --env-file .env -f infra/docker-compose.yml exec api \
  alembic revision --autogenerate -m "add vendor table"

# Or via the task runner
.\tasks.ps1 migrate
.\tasks.ps1 revision "add vendor table"
```

---

## Configuration

All configuration comes from environment variables, read through one typed
settings object ([backend/app/core/config.py](backend/app/core/config.py)).
Copy [.env.example](.env.example) to `.env` and edit. `.env` is git-ignored;
`.env.example` contains development placeholders only and **no real secrets**.

| Variable | Purpose |
|---|---|
| `AMAZON_*` | Read-only SP-API ingestion — see [Running the Amazon POC](#running-the-amazon-poc) and `.env.example` |
| `DATABASE_URL` | SQLAlchemy connection URL |
| `APP_ENV` | Environment name reported by the readiness probe |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FORMAT` | `json` for structured output, `console` for local reading |
| `CORS_ALLOW_ORIGINS` | Comma-separated allowed origins |
| `NEXT_PUBLIC_API_BASE_URL` | API URL baked into the browser bundle at build time |
| `STORAGE_*_DIR` | Retained import file locations (ADR 0004) |

---

## Running the Amazon POC

The Amazon SP-API proof of concept ([ADR 0011](docs/decisions/0011-amazon-sp-api-proof-of-concept-in-milestone-1.md),
[docs/amazon-integration.md](docs/amazon-integration.md)) is read-only: it
pulls orders, FBA/FBM inventory and listings into PostgreSQL, maps seller
SKUs to catalog products through the matching chain, and reports sales
velocity. It never writes to Amazon and never stores buyer data.

1. Put the Login with Amazon credentials in `.env` — `AMAZON_LWA_CLIENT_ID`,
   `AMAZON_LWA_CLIENT_SECRET`, `AMAZON_LWA_REFRESH_TOKEN`, `AMAZON_SELLER_ID`
   (Seller Central > Apps & Services > Develop Apps, self-authorised app).
   They are read from the environment only and are never printed or logged.
2. Confirm the credentials work. This prints the access token's expiry and
   nothing else:

   ```powershell
   cd backend
   .\.venv\Scripts\python -m app.cli.amazon_poc auth
   ```

3. Run the ingestion once, in order — orders (trailing 35 days, in 30-day
   report requests), inventory, listings:

   ```powershell
   .\.venv\Scripts\python -m app.cli.amazon_poc run
   # or one at a time:
   .\.venv\Scripts\python -m app.cli.amazon_poc sync-orders --days 35
   .\.venv\Scripts\python -m app.cli.amazon_poc sync-inventory
   .\.venv\Scripts\python -m app.cli.amazon_poc sync-listings
   ```

   Each command prints one line per run — status and counts — and exits
   non-zero if any run ended `FAILED`. Every run is recorded in
   `amazon_sync_runs` with an audit event.

4. Look at the result:

   ```powershell
   .\.venv\Scripts\python -m app.cli.amazon_poc velocity --top 25
   .\.venv\Scripts\python -m app.cli.amazon_poc velocity --level product
   ```

   Columns: seller SKU, ASIN, Catalog Item #, UPC, units 7/14/30 days,
   average daily (14 d), fulfillable, FBM, inbound, days of supply, mapping
   method. A `-` means the value is not known, never a guessed zero.

**Scheduled ingestion.** With `AMAZON_ENABLED=true` the API process runs the
same three syncs on a schedule (`AMAZON_ORDERS_INTERVAL_MINUTES`,
`AMAZON_INVENTORY_INTERVAL_MINUTES`, `AMAZON_LISTINGS_INTERVAL_MINUTES`;
defaults 24 h / 60 min / 24 h). The scheduler is in-process and must run in
exactly one process. A run left `RUNNING` by a crashed process is closed as
`FAILED` ("timed out") after `AMAZON_RUN_TIMEOUT_MINUTES` before the next
one starts. With more than one organization in the database, set
`AMAZON_ORGANIZATION_SLUG` to say which one the Amazon account belongs to.

Exit codes: `0` ok · `1` a run FAILED · `2` configuration or credentials ·
`3` unexpected error.

## Health endpoints

| Endpoint | Purpose | Touches the database |
|---|---|---|
| `GET /health` | Liveness — "is the process alive" | No |
| `GET /api/v1/health` | Readiness — "can it serve traffic" | Yes; returns 503 when unreachable |

Liveness deliberately ignores the database. If it did not, a database outage
would make Docker kill and restart an otherwise healthy container, which helps
nobody. `/health` is the only route permitted outside `/api/v1`.

---

## Documentation

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Binding rules: scope, architecture, product identity |
| [docs/milestone-1-scope.md](docs/milestone-1-scope.md) | In and out of scope |
| [docs/architecture.md](docs/architecture.md) | Structure, entities, plan, risks |
| [docs/acceptance-criteria.md](docs/acceptance-criteria.md) | Definition of done |
| [docs/phase1-status.md](docs/phase1-status.md) | Live status, assumptions, blocking questions |
| [docs/decisions/](docs/decisions/) | Architecture decision records |
