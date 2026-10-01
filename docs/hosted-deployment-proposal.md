# Hosted Deployment Phase Proposal

Status: **Proposed separate phase — not part of Milestone 1 local acceptance**

This phase takes the Docker-validated application from a developer machine to
a client-owned hosted environment. Work begins only after the client approves
the scope and supplies the required accounts. The repository remains the
single source of truth; no deployment may depend on files held only on a
developer computer.

## Recommended target

Use a client-owned managed platform that supports long-running Docker services,
a managed PostgreSQL 16 database, private service networking, secret storage,
scheduled/background processes, health checks, logs, backups, and custom HTTPS
domains. Railway, Render, or an equivalent container platform can satisfy the
staging topology; the final provider should be selected with the client before
implementation.

The safe initial staging topology is:

| Component | Hosted responsibility |
|---|---|
| Web | Next.js container, public HTTPS URL |
| API | FastAPI container, public HTTPS API, exactly one scheduler owner |
| Database | Managed PostgreSQL 16 on private networking |
| Secrets | Provider secret manager; never Git, images, tickets, or chat |
| Storage | Persistent volume or object storage for retained import files |
| Delivery | GitHub-triggered build/deploy with migration release step |
| Operations | Central logs, uptime checks, database backups, and alerts |

The current scheduler runs inside the API process. Staging must therefore run
one API replica until a dedicated scheduler/worker process and distributed job
lock are implemented. Horizontal API scaling without that change could execute
the same scheduled job more than once.

## Client prerequisites

The client owns and grants least-privilege access to:

- the selected hosting account and billing;
- the GitHub repository and deployment integration;
- a staging domain or subdomains for the web and API;
- Amazon SP-API credentials authorized for the required read-only roles;
- a dedicated Nineyard integration account with the required read access;
- DNS management or an administrator who can make the requested records;
- the production identity-provider tenant when production authentication is in
  scope;
- a secure credential-sharing channel or direct entry into the provider secret
  manager.

Personal passwords should not be reused. Credentials already shared in chat
should be rotated before the hosted phase and replaced with dedicated
integration credentials where the providers support them.

## Delivery sequence

### 1. Confirm architecture and ownership

- Choose the hosting provider and staging region.
- Record service ownership, billing owner, support contacts, and recovery
  responsibilities.
- Confirm data-retention, backup-retention, and expected synchronization
  schedules.
- Confirm whether staging may use live Amazon/Nineyard data.

**Exit:** approved deployment diagram, access matrix, and environment list.

### 2. Provision client-owned infrastructure

- Create managed PostgreSQL 16.
- Create API and web services from the repository Dockerfiles.
- Configure private database networking and persistent import storage.
- Configure separate staging domains and TLS certificates.
- Keep all external integrations disabled initially.

**Exit:** web, API health endpoint, and database are reachable through their
intended network paths; no credentials are stored in Git.

### 3. Configure production-safe application settings

- Set `APP_ENV=production`.
- Set `DEV_AUTH_ENABLED=false` and `EXPOSE_ERROR_DETAILS=false`.
- Generate a unique JWT signing secret in the provider secret manager.
- Configure exact HTTPS CORS and frontend API origins.
- Add Amazon and Nineyard credentials directly to the secret manager.
- Set organization selectors and conservative synchronization intervals.

**Exit:** configuration review passes and deployment logs contain no secrets.

### 4. Automate release and migrations

- Connect the client repository to the hosting platform.
- Build immutable API and web images from a pinned Git revision.
- Run `alembic upgrade head` once as a pre-deploy/release step.
- Add health checks, deployment timeouts, and rollback instructions.
- Require the repository CI workflow to pass before promotion.

**Exit:** a reviewed commit deploys reproducibly, and a failed release can be
rolled back without replacing the database.

### 5. Validate the hosted database and recovery path

- Verify migrations and application connectivity.
- Enable automated backups and record retention.
- Create an on-demand backup.
- Restore that backup into an isolated database and verify row counts.
- Test API restart and redeploy without data loss.

**Exit:** documented backup and restore evidence, not merely an enabled toggle.

### 6. Validate hosted Amazon integration

- Authenticate without printing the token.
- Run orders and listings manually.
- Run one complete inventory synchronization.
- Verify FBA, FBM-only, inbound, and velocity results in PostgreSQL/UI.
- Enable schedules only after manual jobs pass.

**Exit:** a hosted `COMPLETED` run with counters, timestamps, logs, and database
evidence.

### 7. Validate hosted Nineyard integration and matching

- Run the catalog synchronization after active Amazon listings exist.
- Verify rate-limit pacing and bounded retry behavior.
- Confirm catalog item number/UPC persistence and Amazon listing rematching.
- Enable the hosted schedule after the manual run passes.

**Exit:** a hosted `COMPLETED` Nineyard run and sampled identifier mappings.

### 8. Exercise controlled failures

Use reversible, non-destructive tests:

- reject an intentionally invalid credential, then restore it;
- simulate a transient upstream/network failure where the platform permits;
- verify a failed run retains its error and a later run recovers;
- verify stale `RUNNING` recovery and duplicate-run protection;
- verify logs redact credentials and tokens;
- verify alerts fire for unhealthy services and failed scheduled jobs.

**Exit:** expected failure, alert, recovery, and audit evidence for each test.

### 9. Handoff and acceptance

- Deliver hosted URLs, architecture, configuration inventory, runbook, backup
  procedure, rollback procedure, and access matrix.
- Record the deployed Git commit and successful CI/deployment runs.
- Provide a short hosted demonstration recording.
- Transfer administrative ownership to the client.
- Obtain written hosted-phase acceptance.

## Acceptance checklist

| Requirement | Acceptance evidence |
|---|---|
| Client-owned environment | Client administrator can access billing and services |
| HTTPS web and API | Valid certificates and successful health checks |
| Managed database | Private connectivity, migrations at head, persistence verified |
| Secret management | No credentials in Git/images/logs; rotation procedure documented |
| Automated deployment | Passing CI deploys a recorded commit; rollback rehearsed |
| Backups | Automated backup plus successful isolated restore |
| Amazon | Hosted manual and scheduled runs complete successfully |
| Nineyard | Hosted manual and scheduled runs complete successfully |
| Mapping | Sample Amazon SKUs resolve through catalog item number/UPC rules |
| Error handling | Controlled failure and recovery evidence retained |
| Monitoring | Health and failed-job alerts reach the agreed recipient |
| Documentation | Architecture, configuration, operations, and ownership handed over |

## Explicit exclusions unless separately approved

- new business features or redesigned user experience;
- changes to Amazon or Nineyard write operations;
- data cleanup outside the application's deterministic mapping workflow;
- multiple production regions or zero-downtime disaster-recovery architecture;
- 24/7 managed operations and incident response;
- provider, domain, database, email, or monitoring subscription charges;
- production launch before real authentication and authorization are accepted.

## Change control

Any provider limitation, required code change, data-volume increase, additional
integration, or production-security requirement discovered during staging is
recorded with its impact and approved before it expands the phase. Completion
is based on the acceptance checklist above, not merely on a successful first
deployment.
