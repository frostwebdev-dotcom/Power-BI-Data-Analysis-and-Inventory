# Milestone 1 — Client Demonstration

Status: **Code-ready; live Amazon evidence pending**  
Last updated: 2026-09-22

This is the shortest honest path to demonstrate the Amazon portion of
Milestone 1. Do not place credentials or tokens in screenshots, chat, Git, or
this document.

## 1. What is ready now

- read-only LWA authentication and SP-API wrapper;
- orders ingestion for 7/14/30-day sales velocity;
- FBA, FBM, and inbound inventory ingestion;
- listing ingestion and product-mapping workflow;
- PostgreSQL persistence, run history, audit events, retry/backoff, and stale-run recovery;
- scheduled read-only ingestion;
- CLI proof commands;
- authenticated `GET /api/v1/amazon/velocity` endpoint at SKU or product level;
- automated unit verification (704 tests passing on 2026-09-22).

## 2. One-time secure configuration

Create `.env` from `.env.example` on the deployment machine and set:

```dotenv
AMAZON_ENABLED=false
AMAZON_LWA_CLIENT_ID=<LWA client identifier>
AMAZON_LWA_CLIENT_SECRET=<LWA client secret>
AMAZON_LWA_REFRESH_TOKEN=<self-authorization refresh token>
AMAZON_SELLER_ID=<United States merchant token>
AMAZON_MARKETPLACE_ID=ATVPDKIKX0DER
AMAZON_REGION=NA
```

Keep `AMAZON_ENABLED=false` until the manual proof succeeds. The Seller ID is
the United States merchant token; the marketplace ID for amazon.com is the
fixed value `ATVPDKIKX0DER`.

## 3. Start the stack

```bash
cp .env.example .env                 # skip if .env already exists
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
docker compose --env-file .env -f infra/docker-compose.yml ps
```

Confirm the API readiness response at `http://localhost:8000/api/v1/health`
and the interface at `http://localhost:3000`.

## 4. Prove authentication without exposing a secret

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec api \
  python -m app.cli.amazon_poc auth
```

Expected: exit code 0 and a token expiry only. Never screenshot `.env`, LWA
credentials, the refresh token, or the merchant token.

## 5. Run the first read-only sync

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec api \
  python -m app.cli.amazon_poc run
```

Expected: completed order, inventory, and listing runs with counts. A failure
is retained as evidence in `amazon_sync_runs`; correct its cause and rerun.

## 6. Show the client the result

CLI view:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec api \
  python -m app.cli.amazon_poc velocity --level product --top 25
```

API view (use the application's bearer token, not an Amazon credential):

```http
GET /api/v1/amazon/velocity?level=product&top=25
```

Show these fields: Catalog Item Number/UPC mapping, units sold over 7/14/30
days, FBA fulfillable, FBM, inbound, total on-hand, 14-day average daily
velocity, and days of supply. Unmapped or pending SKUs are expected until the
Nineyard catalog and human approvals are available; do not hide them.

## 7. Turn on scheduling only after proof

Set `AMAZON_ENABLED=true` and restart the API. Defaults are inventory hourly,
orders daily, and listings daily. After 24 hours, show scheduled rows from
`amazon_sync_runs` and confirm no stale `RUNNING` row remains.

## 8. Client acceptance evidence

Complete [amazon-poc-signoff.md](amazon-poc-signoff.md) with command output and
three SKU comparisons against Seller Central. The Amazon portion is accepted
only after the live run passes. The wider Milestone 1 is not complete until
the Nineyard catalog synchronization and the remaining exit gates in
[phase1-status.md](phase1-status.md) are closed.
