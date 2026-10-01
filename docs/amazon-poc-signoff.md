# Amazon SP-API Proof of Concept — Validation and Sign-off

Status: **Passed in the local Docker environment**

Validation completed: **2026-09-30**

Hosted validation: **Separate deployment phase**

This record closes the seven Amazon proof-of-concept requirements. Credentials
and tokens are intentionally absent.

## Recorded outcome

| Requirement | Outcome | Evidence |
|---|---|---|
| SP-API/LWA authentication | Passed | `amazon_poc auth` obtained a short-lived access token and printed expiry only |
| Sales data for 7/14/30-day velocity | Passed | Live orders were stored; the initial proof recorded 5,495 order rows; the velocity CLI/API reads 7/14/30-day windows |
| Inventory and inbound inventory | Passed | Final inventory run completed with 8,656 seen, 730 created, and 0 failed; FBA, FBM-only, and inbound fields were processed |
| SKU mapping to Catalog Item Number/UPC | Passed | Amazon listings were synchronized, Nineyard catalog/identifiers were synchronized, and listings were rematched deterministically |
| PostgreSQL storage | Passed | Run history, order lines, inventory snapshots, listings, identifiers, mapping exceptions, and audit events persisted |
| Scheduled ingestion | Passed locally | Completed scheduled inventory rows were recorded; the API scheduler registers Amazon jobs when enabled |
| Error handling and logging | Passed | Throttling/transport failures were retained as failed runs; bounded retry, pacing, duplicate-run protection, stale-run recovery, and later successful runs were demonstrated |

## Final inventory run

| Field | Value |
|---|---|
| Run ID | `782e4230-e093-4ffb-af47-7a006211fe24` |
| Trigger | `MANUAL` |
| Status | `COMPLETED` |
| Rows seen | 8,656 |
| Rows created | 730 |
| Rows updated | 0 |
| Rows failed | 0 |

The same database history also contained completed scheduled inventory runs.
Older failed runs were not deleted because they document real operational
conditions and prove recovery behavior.

## Reproduction commands

Run from the repository root against the authorized private `.env`:

```powershell
# Credential exchange
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc auth

# Data ingestion
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-orders --days 35
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-listings
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.nineyard_sync
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc sync-inventory

# Client-facing result
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli.amazon_poc velocity --level product --top 25
```

## Database evidence queries

```sql
select job_type, status, trigger_type,
       rows_seen, rows_created, rows_updated, rows_unchanged, rows_failed,
       started_at, completed_at, error_message
from amazon_sync_runs
order by started_at desc
limit 20;
```

```sql
select mapping_status, mapping_method, count(*)
from marketplace_listings
group by 1, 2
order by 1, 2;
```

```sql
select count(*) as skus,
       sum(fulfillable) as fulfillable,
       sum(inbound_working + inbound_shipped + inbound_receiving) as inbound,
       count(fbm_quantity) as skus_with_fbm,
       max(captured_at) as captured_at
from amazon_inventory_snapshots s
where sync_run_id = (
    select id
    from amazon_sync_runs
    where job_type = 'FBA_INVENTORY' and status = 'COMPLETED'
    order by started_at desc
    limit 1
);
```

## Security confirmation

- The integration is read-only.
- Buyer names, addresses, and other buyer PII are not stored.
- LWA client secrets, refresh tokens, and access tokens are not committed.
- Authentication logs show token lifetime, never token value.
- Evidence screenshots must not include `.env` or expanded container
  configuration.

## Sign-off boundary

This sign-off proves the integration on the authorized account in the local
Docker environment. A hosted environment must repeat authentication, ingestion,
scheduler, database persistence, logging, and controlled recovery checks before
hosted deployment is accepted.
