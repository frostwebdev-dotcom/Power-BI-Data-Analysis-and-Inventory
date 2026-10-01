# Configuration Guide

All application configuration is read from environment variables through
`backend/app/core/config.py`. The repository contains `.env.example`, which is
safe to commit because it contains placeholders only. A real `.env` is ignored
by Git and must remain private.

## Configuration flow

For local Compose, the root `.env` serves two purposes:

1. `docker compose --env-file .env` interpolates values into the Compose model.
2. The API service receives the integration settings through its `env_file` and
   explicit `environment` entries.

Changing `.env` does not change an already-created container. Recreate the API
after changing credentials or enablement flags:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate api
```

## Local application and database

| Variable | Local value or rule |
|---|---|
| `APP_ENV` | `local` |
| `LOG_LEVEL` | Normally `INFO` |
| `LOG_FORMAT` | `json` for structured evidence; `console` for local reading |
| `POSTGRES_USER` | Local database user; default `prms` |
| `POSTGRES_PASSWORD` | Replace the example before any shared environment |
| `POSTGRES_DB` | Default `prms` |
| `DATABASE_URL` | Inside Compose, host must be `postgres`, not `localhost` |
| `AUTH_JWT_SECRET` | Local placeholder only; generate a unique secret for hosting |
| `DEV_AUTH_ENABLED` | May be `true` locally; must be `false` when hosted |
| `CORS_ALLOW_ORIGINS` | Browser-visible frontend origin |
| `NEXT_PUBLIC_API_BASE_URL` | Browser-visible API URL; baked in when the frontend builds |
| `STORAGE_*_DIR` | Container paths backed by the host `storage/` directory |

`NEXT_PUBLIC_*` values are public by design. Never put a credential in one.

## Amazon SP-API

| Variable | Purpose |
|---|---|
| `AMAZON_LWA_CLIENT_ID` | Login with Amazon application identifier |
| `AMAZON_LWA_CLIENT_SECRET` | Login with Amazon application secret |
| `AMAZON_LWA_REFRESH_TOKEN` | Self-authorization refresh token |
| `AMAZON_SELLER_ID` | Seller/merchant identifier |
| `AMAZON_MARKETPLACE_ID` | `ATVPDKIKX0DER` for amazon.com |
| `AMAZON_REGION` | `NA` for the United States marketplace |
| `AMAZON_ENABLED` | Registers scheduled Amazon jobs when `true` |
| `AMAZON_*_INTERVAL_MINUTES` | Orders, inventory, and listings cadences |
| `AMAZON_ORDERS_WINDOW_DAYS` | Trailing sales window; default 35 |
| `AMAZON_RUN_TIMEOUT_MINUTES` | Age at which an abandoned RUNNING row is recovered |
| `AMAZON_ORGANIZATION_SLUG` | Required when the database has multiple organizations |

The integration is read-only. Validate new credentials with
`python -m app.cli.amazon_poc auth`; it prints the access-token lifetime, not
the token. A new refresh token requires an API-container recreation, not only a
restart.

## Nineyard

| Variable | Purpose |
|---|---|
| `NINEYARD_BASE_URL` | Normally `https://backyard.nineyard.com` |
| `NINEYARD_EMAIL` | Authorized Nineyard account email |
| `NINEYARD_PASSWORD` | Authorized Nineyard password |
| `NINEYARD_COMPANY_ID` | Numeric company identifier |
| `NINEYARD_ACCOUNT` | Exact seller account text returned by Nineyard |
| `NINEYARD_ENABLED` | Registers the catalog schedule when `true` |
| `NINEYARD_SKU_REQUEST_INTERVAL_SECONDS` | Proactive rate-limit pacing; default 1.25 |
| `NINEYARD_SYNC_INTERVAL_MINUTES` | Catalog cadence; default 1440 |
| `NINEYARD_ORGANIZATION_SLUG` | Required when the database has multiple organizations |

The catalog sync intentionally targets active Amazon listing SKUs instead of
scanning all tenant history. Run the Amazon listings sync before the first
Nineyard sync.

## Safe enablement order

1. Copy `.env.example` to `.env`.
2. Set credentials but leave both integrations disabled.
3. Build, migrate, and seed the local stack.
4. Run Amazon authentication manually.
5. Run Amazon orders and listings manually.
6. Run Nineyard manually.
7. Run Amazon inventory and the velocity view manually.
8. Set both `*_ENABLED=true`.
9. Recreate the API container and inspect scheduler logs.

## Hosted-environment requirements

A hosted environment must use its provider's secret manager rather than a
committed `.env`. It must also set `APP_ENV=production`, disable development
authentication and detailed errors, use HTTPS origins, run migrations as a
release step, enable managed database backups, and provide a real production
identity provider. These items belong to the separate hosted deployment phase;
local validation is not evidence that they have been completed.
