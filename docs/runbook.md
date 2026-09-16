# Runbook — Milestone 1

Operational procedures for the Purchasing & Replenishment Management System.
Each procedure was rehearsed on 2026-09-16 against the development stack
unless it says otherwise; the numbers quoted come from that rehearsal.

Conventions: `$DATABASE_URL` is the connection string the API reads
(`DATABASE_URL` in `.env`). Never paste it into a ticket or a chat. PostgreSQL
client tools are the ones that ship with PostgreSQL 16 (`pg_dump`, `pg_restore`,
`psql`); on the development machine they are in
`C:\Program Files\PostgreSQL\16\bin`.

---

## 1. Backup

The database is the system of record for everything except the retained
vendor files, which live under `STORAGE_RAW_DIR` (ADR 0004). A backup is
therefore **two** things: a `pg_dump` and a copy of the raw-file directory.
Restore one without the other and either `import_files.storage_uri` points
at nothing, or files exist that no row knows about.

```bash
# 1. Database, custom format (compressed, restorable table by table).
pg_dump -h $HOST -p 5432 -U $USER -Fc -f prms-$(date +%Y%m%d-%H%M).dump prms

# 2. Retained files: the directory is content-addressed and append-only, so an
#    incremental copy is exact.
rsync -a --ignore-existing $STORAGE_RAW_DIR/ backups/raw/
```

Rehearsal: 133 MB dump of the development database (1.9 million rows across
the import tables after the 50,000-row performance runs) in **17.6 s**.

What is *not* in the dump: nothing that matters. Secrets are environment
variables, not rows. Development tokens are stateless JWTs.

## 2. Restore

Restore into a **fresh** database and verify before pointing anything at it.

```bash
createdb -h $HOST -U $USER prms_restored
pg_restore -h $HOST -U $USER -d prms_restored --no-owner prms-<stamp>.dump

# The schema must be exactly the revision the code expects:
DATABASE_URL=postgresql+psycopg://$USER:...@$HOST:5432/prms_restored alembic current
DATABASE_URL=postgresql+psycopg://$USER:...@$HOST:5432/prms_restored alembic check
#   → "52e787f49770 (head)" and "No new upgrade operations detected."

# Then the retained files:
rsync -a backups/raw/ $STORAGE_RAW_DIR/
```

Rehearsal (2026-09-16): restore took **78.3 s**; row counts of all fourteen
operational tables were identical between source and restored copies;
`alembic current` reported `52e787f49770 (head)`; `alembic check` reported no
drift; the integration suite run with `DATABASE_URL` pointed at the restored
server (it provisions its own `prms_restored_test` from the migrations) passed
**408 of 408** in three runs of four — the first run had one failure that was
not captured and did not recur; it is recorded in
[phase1-status.md §4](phase1-status.md). To go live on the restored copy,
change `DATABASE_URL` and restart the API; nothing else refers to the database
name.

Point-in-time recovery (WAL archiving) is **not** configured. A restore returns
to the moment of the last dump. Decide the backup cadence against that:
imports are idempotent by file checksum, so re-uploading the files received
since the last dump recreates their jobs.

## 3. Rotating the JWT signing secret

`AUTH_JWT_SECRET` signs the development tokens (`DEV_AUTH_ENABLED`). Rotation:

1. Generate a new value of at least 32 characters
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
2. Set `AUTH_JWT_SECRET` in the environment of every API process.
3. Restart the API. Every token issued under the old secret is now invalid;
   users sign in again. There is no grace period and no token store to purge.

Production refuses to start with the shipped development secret
(`app/core/config.py`, `production_problems`). The production identity
provider (B2) will make this section obsolete.

## 4. Rotating Amazon SP-API credentials

The credential triple is `AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`,
`AMAZON_LWA_REFRESH_TOKEN` (plus `AMAZON_SELLER_ID`, `AMAZON_MARKETPLACE_ID`).
They are read once at start-up into the typed settings; nothing caches a
token across processes.

1. In Seller Central / the LWA console, create the new secret or refresh
   token. Do **not** revoke the old one yet.
2. Set the new values in the environment; restart the API (and the worker,
   when one exists — today the scheduler runs inside the API process).
3. Verify without touching data:
   `python -m app.cli.amazon_poc auth` prints the new token's expiry and
   nothing else; a 401 here means the values are wrong.
4. Revoke the old credential upstream.

Never put a value in a log, an issue, or a commit. The redaction layer masks
the known names in logs, but that is a backstop, not a licence.

## 5. Rotating Nineyard credentials

`NINEYARD_EMAIL`, `NINEYARD_PASSWORD`, `NINEYARD_COMPANY_ID`. Same procedure
as §4; verify with `python -m app.cli.nineyard_probe` (read-only). The
catalogue sync itself does not exist yet (phase 3, blocked on B1), so today a
rotation affects only the probe.

## 6. Re-running a failed sync

### Amazon

Each run is a row in `amazon_sync_runs` with `status`, `error_message` and
counters. A `FAILED` run frees its job-type slot; simply run again:

```bash
python -m app.cli.amazon_poc sync-orders      # trailing window, idempotent upsert
python -m app.cli.amazon_poc sync-inventory
python -m app.cli.amazon_poc sync-listings
python -m app.cli.amazon_poc run              # all three, in order
```

Every ingestion is an idempotent upsert keyed on `(order id, SKU)` /
`(run, SKU)`; re-running after a partial failure creates no duplicates. The
scheduled jobs (`app/jobs/amazon.py`) do the same thing on their cadence.

### Vendor imports

An import job is `FAILED` when the file could not be read as its profile
describes, a required column was missing, or the header signature did not
match (`import_jobs.error_details.code` says which). The file itself is
retained. Fix the cause — usually the profile — and upload the file again:
the same bytes are accepted because a `FAILED` job does not hold the file's
slot (`uq_import_jobs_active_import_file`). The rows the failed job wrote, if
any, stay as evidence.

## 7. Clearing a stuck RUNNING run

### Amazon sync run

The partial unique index `uq_amazon_sync_runs_running_job` allows one
`RUNNING` row per job type per organization. A process that dies mid-run
leaves that row and blocks every later run of the type. **This is handled
automatically**: the next scheduled or manual run closes any `RUNNING` row
older than `AMAZON_RUN_TIMEOUT_MINUTES` as `FAILED ("timed out")` before it
starts (`recover_stale_runs`, logged as `amazon.job.recovered_stale_runs`).
To force it sooner, lower the timeout for one run, or:

```sql
update amazon_sync_runs
   set status = 'FAILED', completed_at = now(),
       error_message = 'closed by operator: process died'
 where status = 'RUNNING' and started_at < now() - interval '2 hours';
```

### Import job

Parsing, matching and snapshotting each commit per 1,000 rows, so a killed
process leaves a job `RUNNING` at some `current_stage` with partial progress.
Re-running the stage is safe by design — parsing skips nothing but a
re-parse is refused (the job is not PENDING), matching re-evaluates rows
deterministically, snapshotting never writes a second snapshot for a line the
job already covered — but there is no CLI to resume a stage yet (AC-5.7,
open). Today: close the job and re-upload the file.

```sql
update import_jobs
   set status = 'FAILED', current_stage = null, completed_at = now(),
       started_at = coalesce(started_at, now()),
       error_message = 'closed by operator: process died during ' || coalesce(current_stage::text, 'start')
 where id = '<job id>' and status = 'RUNNING';
```

`FAILED` frees the file's slot; the next upload of the same bytes creates a
fresh `PENDING` job on the same retained file. Vendor lines and queue items
the partial run created are valid and are reused, not duplicated.

## 8. Checking that the system is healthy

- `GET /api/v1/health` — 200 with `postgresql: ok`; 503 names the dependency
  that is down.
- Dashboard (`/`) — open exceptions, imports running, last import per
  vendor, last sync per source.
- `python -m app.cli.explain_queries` — plans for the interface's queries with
  large sequential scans flagged; run it after a schema change or when a
  screen gets slow.
- `python -m app.cli.perf_import --rows 50000 --format csv` — the import
  lifecycle timed stage by stage against the current database (development
  only; creates a `PERF…` vendor).

## 9. Seeding an environment

- `python -m app.cli.seed_dev` — an organization, four users (one per role)
  and one demo product. Required before signing in to a fresh environment.
- `python -m app.cli.seed_demo` — the above plus two vendors with materially
  different files (CSV and XLSX), their profiles, three imports run through
  the whole lifecycle, and a watch that the third import flips to in stock.
  For client demos and CI. Both refuse to run unless `DEV_AUTH_ENABLED=true`
  and never in production.
