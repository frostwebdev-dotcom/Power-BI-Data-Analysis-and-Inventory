# Milestone 1 — Client Demonstration

Status: **Validated locally through Docker**

Last updated: **2026-10-01**

This script demonstrates the delivered result without exposing Amazon,
Nineyard, database, or application credentials. The target length is 5–8
minutes.

## Before recording

1. Close `.env`, password managers, Seller Central, and unrelated browser tabs.
2. Turn off desktop notifications.
3. Use a terminal whose scrollback does not contain credentials.
4. Confirm the dashboard does not expose information the client did not approve
   for the recording.
5. Never run `docker compose config` without `--quiet` while recording.

## 1. Source and container health

Show the repository and current revision:

```powershell
git status --short --branch
git log -3 --oneline
```

Show the stack without printing configuration:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml ps
```

Expected: `postgres`, `api`, and `web` are running; PostgreSQL and API are
healthy. Open the API readiness endpoint and show `postgresql: ok`.

## 2. Dashboard and completed integrations

Open the web interface. Show:

- Amazon orders: completed
- Amazon listings: completed
- Amazon FBA inventory: completed
- Nineyard catalog: completed
- System status: operational

Explain that scheduled and manual runs use the same services and are retained
in PostgreSQL with their status, counters, timestamps, and error details.

## 3. Authentication proof

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc auth
```

Expected: `token obtained` and an expiry. The command does not print the access
token, refresh token, client secret, or seller credential.

## 4. Final inventory evidence

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec postgres psql -U prms -d prms -c "SELECT id, status, trigger_type, rows_seen, rows_created, rows_updated, rows_failed, started_at, completed_at, error_message FROM amazon_sync_runs WHERE job_type = 'FBA_INVENTORY' ORDER BY started_at DESC LIMIT 5;"
```

Highlight the recorded completed manual run:

- Run: `782e4230-e093-4ffb-af47-7a006211fe24`
- Status: `COMPLETED`
- Rows seen: `8,656`
- Rows created: `730`
- Rows updated: `0`
- Rows failed: `0`

Also point out the completed scheduled run and the retained failed transport
run. Keeping the failure is intentional audit history, not an active defect.

## 5. Velocity and mapping

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc velocity --level product --top 25
```

Show catalog item number/UPC, units over 7/14/30 days, FBA fulfillable, FBM,
inbound, average daily velocity, days of supply, and the mapping method. Then
open the exception queue and explain that uncertain matches are held for human
review rather than guessed.

## 6. Scheduling, logs, and recovery

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=200 api
```

Show scheduler startup and a completed scheduled job. Explain:

- only one run per organization/job type can be active;
- abandoned runs are recovered after the configured timeout;
- Amazon and Nineyard transient failures use bounded retries;
- Nineyard exact-SKU calls are proactively paced;
- failed runs remain visible in the database.

Do not scroll through unreviewed logs during the recording. Prepare a safe,
redacted section first.

## 7. GitHub and documentation

Show the `main` branch, the final passing CI run, and these documents:

- [Local Docker setup](local-docker-setup.md)
- [Configuration guide](configuration-guide.md)
- [Operations runbook](runbook.md)
- [Troubleshooting](troubleshooting.md)
- [Milestone 1 handoff](milestone-1-handoff.md)

Conclude by stating that Milestone 1 is accepted as a locally validated Docker
delivery. Hosted deployment and hosted integration verification are a separate
future phase.

## Delivery attachments

- Demonstration video
- Dashboard screenshot
- Final database synchronization screenshot
- Passing CI screenshot or run link
- Repository and documentation links
- [Acceptance checklist](milestone-1-handoff.md#acceptance-checklist)
