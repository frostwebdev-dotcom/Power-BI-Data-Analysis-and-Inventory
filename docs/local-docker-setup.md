# Local Docker Setup — Reproducible Milestone 1 Environment

This guide reproduces the locally validated Milestone 1 stack from a clean
checkout. It does not depend on files from the original development machine.
Hosted staging and production deployment are separate from this procedure.

## 1. Prerequisites

- Git
- Docker Desktop with Linux containers and Docker Compose v2
- At least 8 GB RAM available to Docker
- Authorized Amazon SP-API credentials
- Authorized Nineyard credentials and the exact seller account name

The application itself does not require host Python, Node.js, or PostgreSQL
when run through Compose.

## 2. Clone and create local configuration

```powershell
git clone https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory.git
cd Power-BI-Data-Analysis-and-Inventory
Copy-Item .env.example .env
```

On macOS or Linux, use `cp .env.example .env` for the final command.

Edit `.env` locally. Do not send it in chat, attach it to a ticket, include it
in a screenshot, or commit it. The required integration variables and safe
enablement order are in [configuration-guide.md](configuration-guide.md).

## 3. Validate Compose before starting

```powershell
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
```

No output and exit code 0 means the Compose model is valid. To see only the
service names without printing the expanded environment:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml config --services
```

Avoid saving the full output of `docker compose config`; expanded service
environment values may contain credentials.

## 4. Build and start the stack

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml ps
```

The expected services are `postgres`, `api`, and `web`. With the default ports:

| Component | Address |
|---|---|
| Web interface | http://localhost:3000 |
| API liveness | http://localhost:8000/health |
| API readiness | http://localhost:8000/api/v1/health |
| API documentation | http://localhost:8000/api/v1/docs |
| PostgreSQL | localhost:5432 |

If `.env` overrides `WEB_HOST_PORT`, `API_HOST_PORT`, or
`POSTGRES_HOST_PORT`, use those host ports instead.

## 5. Apply migrations

Migrations are explicit in the local stack:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api alembic upgrade head
docker compose --env-file .env -f infra/docker-compose.yml exec api alembic current
docker compose --env-file .env -f infra/docker-compose.yml exec api alembic check
```

`alembic current` must report the head revision and `alembic check` must report
that no new upgrade operations are detected.

## 6. Seed local sign-in data when starting with an empty database

This is local-development data only:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.seed_dev
```

With `DEV_AUTH_ENABLED=true`, sign in as `admin@example.test`. Never enable
development authentication in a hosted production environment.

## 7. Prove the integrations manually before enabling schedules

Keep `AMAZON_ENABLED=false` and `NINEYARD_ENABLED=false` during the first
manual proof. The CLI still reads the configured credentials.

```powershell
# Amazon credential exchange; prints expiry only.
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc auth

# Sales history and the active Amazon listing set.
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-orders --days 35
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-listings

# Nineyard targets only the active Amazon seller SKUs.
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.nineyard_sync

# FBA, FBM, and inbound inventory after catalog identifiers exist.
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-inventory

# Client-facing 7/14/30-day result.
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc velocity --level product --top 25
```

Every sync must finish `COMPLETED` or, where explicitly reported,
`COMPLETED_WITH_ERRORS`. Investigate a failure before enabling schedules.

## 8. Enable scheduled jobs

After the manual proof succeeds, set these values in `.env`:

```dotenv
AMAZON_ENABLED=true
NINEYARD_ENABLED=true
```

Recreate the API container so it receives the new environment and registers
the jobs:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate api
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=200 api
```

Look for `app.scheduler_started` and the registered Amazon/Nineyard jobs. The
scheduler is in-process; run exactly one API replica in this local topology.

## 9. Verify persistence across a restart

```powershell
docker compose --env-file .env -f infra/docker-compose.yml restart api postgres
docker compose --env-file .env -f infra/docker-compose.yml ps
docker compose --env-file .env -f infra/docker-compose.yml exec postgres psql -U prms -d prms -c "SELECT job_type, status, trigger_type, rows_seen, rows_failed, started_at, completed_at FROM amazon_sync_runs ORDER BY started_at DESC LIMIT 10;"
```

The prior rows must remain. `docker compose down` also preserves the named
database volume. `docker compose down -v` deletes it and must not be used unless
the data is intentionally disposable or a verified backup exists.

## 10. Stop or update

```powershell
# Stop and retain data.
docker compose --env-file .env -f infra/docker-compose.yml down

# Pull a reviewed update, rebuild, migrate, and restart.
git pull --ff-only
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml exec api alembic upgrade head
```

For backups, credential rotation, and failed-run recovery, continue with
[runbook.md](runbook.md). For known failure signatures, use
[troubleshooting.md](troubleshooting.md).
