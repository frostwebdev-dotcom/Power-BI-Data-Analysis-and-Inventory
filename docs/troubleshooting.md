# Troubleshooting

Start with read-only checks. Preserve failed run rows and logs because they are
operational evidence.

## Docker Desktop engine is unavailable on Windows

Symptom:

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

Start Docker Desktop, wait until the Linux engine reports running, then verify:

```powershell
docker info
docker compose --env-file .env -f infra/docker-compose.yml ps
```

## A sync says another run is already RUNNING

The database permits only one active run for each organization and job type.
Do not launch another copy. Inspect the current run:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec postgres psql -U prms -d prms -c "SELECT id, job_type, status, trigger_type, started_at, completed_at, error_message FROM amazon_sync_runs WHERE status = 'RUNNING' ORDER BY started_at DESC;"
```

If the API process is still working, wait. If the process died, the next run
recovers rows older than `AMAZON_RUN_TIMEOUT_MINUTES`. The manual operator
procedure for closing a genuinely abandoned row is in [runbook.md](runbook.md).

## Updated credentials are not being used

`docker compose restart` does not replace the container environment. After
editing `.env`, recreate the API:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate api
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc auth
```

Never print the environment to prove a secret changed.

## Amazon refresh token is rejected

Confirm the self-authorized app still has the required read-only roles. Generate
a new refresh token upstream, replace only the private `.env` value, recreate
the API container, and run `amazon_poc auth`. Do not commit or screenshot the
token.

## Amazon NextToken is invalid or expired

Pagination tokens are short-lived and cannot be resumed later. A failed run is
retained; start a new sync, which begins from the first page and upserts data
idempotently. Do not copy a token into configuration or logs.

## Amazon reports “Too many Skus”

Current `main` normalizes comma-delimited listing values and sends batches of
at most 50 seller SKUs. Confirm the deployment is on the current commit,
rebuild the API image, and rerun. If the error persists, capture the request
number and redacted error message—never the SKU payload or credentials.

## Nineyard returns HTTP 429

Leave `NINEYARD_SKU_REQUEST_INTERVAL_SECONDS` at 1.25 or increase it. The client
retries transient failures, but proactive pacing is the normal control. Do not
start simultaneous manual and scheduled catalog syncs.

## Nineyard reports a connection or DNS error

`No address associated with hostname` is a Docker/network/DNS problem, not an
authentication failure. Confirm the host can resolve the Nineyard domain,
restart Docker Desktop if its internal DNS is stale, and retry after connectivity
returns. The scheduler will also try again at the next interval.

## Nineyard reports no active Amazon listings

Run the listings sync first:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-listings
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.nineyard_sync
```

The catalog job deliberately refuses an unbounded historical scan.

## API is healthy but the web interface says Offline

`NEXT_PUBLIC_API_BASE_URL` is compiled into the frontend bundle. Correct it in
`.env` and rebuild `web`; a restart is not enough:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build web
```

Also confirm `CORS_ALLOW_ORIGINS` contains the exact browser origin.

## A host port is already in use

Change only the published host port in `.env`:

```dotenv
POSTGRES_HOST_PORT=5434
API_HOST_PORT=8010
WEB_HOST_PORT=3001
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010
CORS_ALLOW_ORIGINS=http://localhost:3001
```

Then rebuild the web image because its API URL is a build-time setting.

## First-response diagnostic bundle

Capture statuses and recent logs without printing configuration:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml ps
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=200 api
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=100 postgres
```

Review the output for accidental business data before sharing it. Never attach
`.env` or full `docker compose config` output.
