# Milestone 1 Handoff and Acceptance Record

Status: **Completed and validated locally through Docker**

Validation date: **2026-09-30**

Hosted deployment: **Not included in this acceptance record; separate phase**

## Delivered scope

- PostgreSQL data foundation and migrations
- Vendor database, CSV/XLSX imports, versioned profiles, validation, and reports
- Deterministic product matching and human exception workflow
- Watchlist, availability transitions, audit history, and administration UI
- Read-only Amazon authentication, orders, listings, FBA/FBM/inbound inventory,
  7/14/30-day velocity, scheduling, retry/backoff, and stale-run recovery
- Live Nineyard catalog synchronization bounded to active Amazon listings
- Amazon SKU mapping to Nineyard catalog item numbers and UPC identifiers
- Docker Compose environment and automated CI quality gates
- Reproducibility, configuration, operations, and troubleshooting documentation

## Recorded live evidence

| Evidence | Recorded result |
|---|---|
| Amazon LWA authentication | Access token obtained; only expiry printed |
| Amazon orders | Completed live ingestion; 5,495 order rows recorded in the initial proof |
| Amazon listings | Completed live report and active-listing refresh |
| Nineyard catalog | Completed live synchronization and displayed as `completed` on the dashboard |
| Final manual inventory run | Run `782e4230-e093-4ffb-af47-7a006211fe24` — `COMPLETED` |
| Final inventory counters | 8,656 seen; 730 created; 0 updated; 0 failed |
| Fulfillment coverage | FBA inventory, FBM-only quantities, and inbound quantities processed |
| Scheduling | Completed scheduled inventory runs recorded; failed transport runs preserved and later runs recovered |
| Git revision before handoff docs | `349e9f9` on `main` |
| CI | Final main-branch CI run `36783109641` reported passing after the Compose fix |

The screenshots and demonstration recording are delivery attachments rather
than committed repository content because they contain account-specific data.
No credential screenshot is part of the evidence package.

## Acceptance checklist

| Requirement | Status | Reproduction/evidence |
|---|---|---|
| Clean Docker build and three-service startup | Passed | [local-docker-setup.md](local-docker-setup.md) and CI `compose-smoke` |
| Amazon SP-API/LWA authentication | Passed | `amazon_poc auth` |
| Sales data for 7/14/30-day velocity | Passed | Orders sync and `amazon_poc velocity` |
| FBA, FBM, and inbound inventory | Passed | Final completed inventory run |
| Amazon listings ingestion | Passed | Completed listings run |
| Live Nineyard connection and catalog sync | Passed | `nineyard_sync` and dashboard status |
| Amazon SKU to Catalog Item Number/UPC mapping | Passed | Nineyard identifiers and listing rematch |
| PostgreSQL persistence and run history | Passed | Data survived container restart; completed/failed history retained |
| Scheduled ingestion | Passed locally | Scheduled run rows and scheduler logs |
| Retry, rate-limit, and error handling | Passed | Preserved failed runs, bounded retries, pacing, and later successful recovery |
| Source pushed to GitHub | Passed | Main branch at/after `349e9f9` |
| Automated quality/CI validation | Passed | Main-branch run `36783109641` |
| Reproducible configuration and operating instructions | Passed | Setup, configuration, runbook, and troubleshooting documents |

## Handoff documents

- [Local Docker setup](local-docker-setup.md)
- [Configuration guide](configuration-guide.md)
- [Operations runbook](runbook.md)
- [Troubleshooting](troubleshooting.md)
- [Client demonstration](milestone-1-client-demo.md)
- [Amazon validation record](amazon-poc-signoff.md)
- [Deployment boundary and reference](deployment.md)
- [Separate hosted-deployment proposal](hosted-deployment-proposal.md)

## Client attachments

- [ ] Demonstration video link supplied
- [ ] Dashboard screenshot supplied
- [ ] Final synchronization/database screenshot supplied
- [ ] Passing CI screenshot or run link supplied
- [ ] Repository and documentation links supplied

## Sign-off

| Field | Value |
|---|---|
| Client decision | Pending client confirmation |
| Accepted by |  |
| Acceptance date |  |
| Notes | Local Docker acceptance only; hosted verification remains separate |
