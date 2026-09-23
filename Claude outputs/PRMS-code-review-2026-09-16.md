# PRMS — Code Review of Commits 05d5d1a…28d25d4

Repository: `frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory`, branch `main`, HEAD `28d25d4` (2026-09-15). Reviewed 2026-09-16 by reading the code, the migrations, the tests and the docs; three independent review passes (Amazon integration, schema/migrations, docs/CI/tests) plus direct verification of every finding rated MAJOR or above. Dependencies could not be installed in the review environment (PyPI blocked), so no test was executed here; "740 passed" is the developer's claim, and the reviewers' AST count of collected tests (532 unit + 208 integration = 740) makes it plausible.

---

## 1. Verdict

**Good work, one day from being demo-ready — do not run the live validation (Prompt 14) until the fixes in section 3 are in.**

Thirteen prompts were executed in order, one commit each, with the closing gates run each time. The code follows the repository's binding conventions (UUID keys, TIMESTAMPTZ, tenant scoping, audit in-transaction, secrets as `SecretStr`, no writes to Amazon), the new tests are substantive (exact counts, compiled SQL, database-level constraint errors, a real logging pipeline checked for leaked tokens), and the migrations are hand-checked and round-trip tested. The README is UTF-8, CI exists with a real compose smoke job, and the scope change went through an ADR as the repo's rules require.

Against that, the review found one defect that will fail the first call to Amazon, four places where the velocity or mapping behaviour differs from what was specified or from the binding matching rule, one migration downgrade that will fail on real data, two ADR-versus-code contradictions, and several stale or unsubstantiated statements in the status document. None of these is hard to fix; all of them should be fixed before the client sees a demo.

| Area | Grade | One line |
|---|---|---|
| Conventions and security | A | No leak path found; typed secrets; redaction covers LWA tokens at the sink; read-only client by construction. |
| Test quality | A- | Concrete assertions everywhere sampled; 11 of the 12 tests the plan demanded are present; window-boundary edge test missing. |
| Correctness of the Amazon pipeline | C | Datetime-in-JSON blocker; velocity anchored to wall clock; partial cancellations counted; PENDING listings included in product roll-up. |
| Schema and migrations | B+ | Clean, well-constrained, fully covered by invariant tests; one downgrade ordering bug. |
| Fidelity to the prompts / ADRs | B- | Prompt 12's SQL view and HTTP endpoints not built; scheduler placed where ADR 0011 says it must not be; matching order recorded in a docstring instead of an ADR. |
| Documentation honesty | B | Mostly candid, but "deployed to Railway", "verified in CI at HEAD" and several counts are unsubstantiated or stale. |

---

## 2. Findings, ranked

### BLOCKER

**B1 — Every report request will fail against the real API.** `backend/app/integrations/amazon/client.py:254-259` puts raw `datetime` objects into the `createReport` body (`"dataStartTime": data_start`). `python-amazon-sp-api` JSON-encodes the body with `json.dumps` and no datetime handler, so the call raises `TypeError: Object of type datetime is not JSON serializable` — which is not a `SellingApiException`, so it also escapes the client's error translation untranslated. Orders, inventory (listings report) and listings mapping all go through this path. The unit tests inject a fake `Reports` class, which is why 740 tests pass and the bug survives. Fix: serialise as RFC 3339 UTC strings (`data_start.isoformat().replace("+00:00", "Z")`) and add a test that asserts the body handed to the library contains only JSON-native types (`json.dumps(body)` must succeed).

### MAJOR

**M1 — Velocity `as_of` is the wall clock, not the last completed orders run.** `backend/app/services/amazon_velocity.py:90` (`at = now()`). The prompt specified anchoring to the latest COMPLETED orders run so a failed or stale sync cannot silently shrink the 7-day window. With a daily orders job, the 7-day bucket can be up to a day short and the client will see numbers that disagree with Seller Central for no visible reason.

**M2 — Partial cancellations are counted as demand.** `amazon_velocity.py:147` filters on `order_status` only; `item_status` (stored at `amazon_orders.py:284`) is never checked, so a cancelled line inside a non-cancelled order counts.

**M3 — Product roll-up includes unapproved suggestions.** A UPC hit sets `product_id` on a PENDING listing (`amazon_listings.py:496-497`); the velocity roll-up reads `product_id` regardless of `mapping_status` (`amazon_velocity.py:226-256`). Product-level velocity therefore includes links no human has approved, contradicting the spec ("via APPROVED marketplace_listings only").

**M4 — Matching order contradicts the binding rule and ADR 0011, and the contradiction is unrecorded.** `CLAUDE.md §5.1` row 1 says an exact normalised UPC match is automatic; ADR 0011 says listings are mapped "automatically at priority 1 (normalized UPC)". The code (`amazon_listings.py:19-31, 196-278`) evaluates priority 4 first, auto-APPROVES on a Nineyard-sourced `AMAZON_SKU` identifier with the system user as approver, and demotes a UPC hit to a PENDING suggestion. *This behaviour is what my Prompt 11 asked for* — it was written cautiously and is defensible — but it now disagrees with two binding documents, and `CLAUDE.md §7` says decisions get an ADR. A decision is needed (section 4), then an ADR amendment either way.

**M5 — Migration `3de5c4e5def0` downgrade fails on real data.** `downgrade()` calls `_drop_checks()` (which re-creates the *old* `ck_product_identifiers_context_matches_identifier_type`) *before* `_remove_rows_the_old_schema_cannot_hold()` (lines 195-197). PostgreSQL validates existing rows when adding a CHECK, so any unlinked `AMAZON_SKU` identifier — the exact rows this migration exists to allow — makes the downgrade raise before the deletion runs. The round-trip test passes only because the scratch database is empty. Swap the two calls and add a test that seeds one such row first.

**M6 — Scheduler placement contradicts ADR 0011, and the ordering claim is wrong.** ADR 0011 says APScheduler must run in one dedicated worker process, never inside each uvicorn replica, and that the compose file gains a worker service. The code starts it in the API lifespan (`backend/app/main.py:40-46`); `infra/docker-compose.yml` has no worker. Separately, `jobs/amazon.py:144-150` claims the listings job runs after inventory on a shared tick because it is *registered* later — APScheduler's `BackgroundScheduler` runs due jobs concurrently, and the 60-minute and 1440-minute intervals coincide once a day, so both jobs request the same listings report and both call `map_listings` on the same tenant at once (duplicate-report cancellation by Amazon, or a unique-index collision on `marketplace_listings`).

**M7 — Prompt 12 was only partly delivered.** No SQL view (velocity is Python), no `GET /api/v1/amazon/velocity`, no `GET /api/v1/amazon/status`, `router.py` unchanged, `test_api_contract.py` untouched. ADR 0011 lists the endpoint as capability 9. For the POC demo the CLI is enough, but either build the endpoints (small) or amend the ADR.

**M8 — CI can pass with the integration suite silently skipped.** `backend/tests/integration/conftest.py:178-184` skips the whole package when PostgreSQL is unreachable; `.github/workflows/ci.yml:81-82` runs plain `pytest -q`. Today the service container makes it work, but a misconfiguration would turn 208 tests into skips and CI would stay green. Fail instead of skipping when a `CI` environment variable is set, and add `-rs` to the pytest step.

### MINOR

- `CLAUDE.md:58` still says "calling Amazon is not [in scope]" — contradicts the new item 14 and the narrowed §3 bullet.
- `README.md:8, 152-154` still say "21 tables, one migration".
- `docs/phase1-status.md`: §3 still says 21 tables / one revision (lines 57, 127) against 24 / 3 at line 15-16; §7 opens "Unchanged." although B7 was amended and B8 added; "five public methods" should be six (`check_credentials`); `.\tasks.ps1` is corrupted to a literal TAB at line 412 (also `docs/deployment.md:262`); "Run on 2026-09-14" but counts include 2026-09-15 tests; "8 of 8 in GitHub Actions" is 7 of 8 (compose-config gate and downgrade round-trip are not in CI).
- `docs/database-schema.md:438-441` still says an `AMAZON_SKU` identifier "has a `marketplace_listing_id`", contradicting its own §5 and `catalog.py:288-295`; ER diagram shows two now-optional relationships as mandatory; "152 database-backed tests" vs 208.
- `docs/architecture.md:345, 424` still say 21/22 tables.
- ADR 0012 quotes "60 assertions / 20 tables"; registry now has 23 scoped models.
- `amazon_orders.py:380-389` and `system_user.py:29-31` hand-write the `organization_id` filter instead of using `TenantScope` (ADR 0012 "exactly one place").
- `amazon_sync_runs.error_message/error_details` store raw `str(exc)` with no redaction pass (the audit copy is redacted; the run row is not). No leak path found, but make it symmetric.
- `_ensure_exception` (`amazon_listings.py:569-576`) refreshes reason/candidates in place with no audit row.
- Orders job aborts remaining windows on first failure (`jobs/amazon.py:96-106`) — the most recent chunk is the one skipped.
- `map_listings` runs inside the inventory transaction (`amazon_inventory.py:487`), so a mapping failure also discards the inventory snapshot.
- Header check strips names but rows are read by unstripped keys (`amazon_orders.py:197-198, 256`; `amazon_inventory.py:157, 177`).
- `has_a_subject` on `product_mapping_exceptions` is OR, not XOR (`matching.py:177-180`); doc says "one of two subjects".
- Lifespan guard tested one-sided (only "does not start" cases).
- Window-boundary edge test (order exactly at 7/14/30 days) absent; `days_of_supply is None` asserted only indirectly via the CLI test.
- Commit `28d25d4` is titled "f". Leave history alone (it is pushed), but use real messages from here on.

### Claims in `docs/phase1-status.md` the repository cannot substantiate

"Backend live/deployed on Railway" (no URL, no deployment record, `railway.json` unchanged); "8 of 8 gates in GitHub Actions" and "verified in CI" *for HEAD* (the only run URL cited is from the 302-test state at `b0bf880`, which applied one migration); the CI timings and local gate run details. These may all be true — they are just not evidenced in the repo. Either link the CI run for `28d25d4` and the Railway URL, or soften the wording.

---

## 3. What to do, in order (paste-ready prompts are in `claude-code-prompt-sequence.md`, Stage 2.5)

1. **Fix B1** (report body serialisation) with a JSON-native-body test. Half an hour. Without this, Prompt 14 step 2 fails immediately.
2. **Fix M1, M2, M3** in the velocity service, with the three missing tests (window boundary, item-status cancellation, PENDING excluded from roll-up). Two hours.
3. **Fix M5** (downgrade ordering) with a seeded-row round-trip test. Half an hour.
4. **Fix M6**: move the scheduler to a worker entrypoint (`python -m app.jobs.worker`) with a `worker` service in compose, or amend ADR 0011 to say "in-process until the first multi-replica deployment" — the second is acceptable for the POC if written down. Remove the standalone listings job (inventory already maps) or give the two jobs a shared run lock. One to three hours.
5. **Decide M4** (section 4), then write ADR 0014 and align `CLAUDE.md §5.1`, ADR 0011 and the code. One hour.
6. **M8 + docs sweep**: fail-instead-of-skip in CI, `-rs`, and the stale sentences listed above in one docs commit. One hour.
7. **M7**: build the two read-only endpoints (small) — or amend ADR 0011. Two hours if built.
8. Then run **Prompt 14** (live validation) exactly as written.

Total: roughly one working day before the live validation.

---

## 4. Decision needed from you: how should an exact UPC match on an Amazon listing be treated?

Option A — **automatic approval** (what `CLAUDE.md §5.1`, ADR 0011 and the client's brief §26 say): a single, unambiguous normalised-UPC match links the seller SKU to the product with `mapping_status = APPROVED`, `mapping_method = UPC`, approver = system user; ambiguity or no match still goes to the queue. Pro: consistent with the binding rule; ~450 SKUs map themselves on day one; the demo shows Catalog Item # next to every SKU. Con: a wrong UPC in a listing links silently (auditable and supersedable, but silent).

Option B — **suggestion only** (what the code does today, per my Prompt 11): UPC hits become PENDING with a queue item for a human. Pro: a person confirms every Amazon link once. Con: contradicts `§5.1` row 1 unless the rule is amended; 450 manual approvals before product-level velocity means anything; the demo shows mostly "pending".

My recommendation is **Option A**. The UPC is the client's own #1 identifier, the match is deterministic and attributable, approved mappings are permanent and supersedable through an audited action, and the status quo forces the client to re-approve what their catalog already says. Keep the priority-4 auto-approval from Nineyard `AMAZON_SKU` identifiers (Nineyard is the client's authoritative catalog, which is exactly what "previously approved mapping" means), and record both in ADR 0014.

---
---

# Part 2 — Review of commits ed513c7…194643e (the Milestone 1 build), same day

Nine further commits were pushed on 2026-09-16 (Prompts 15, 17–22, 24, 25; Prompt 16's vendor screen was folded into 24; Prompt 23 Nineyard sync is blocked on the client). 108 files, ~22,000 lines. Reviewed the same way as Part 1: three independent passes (import pipeline backend; migrations/frontend/e2e/CI/hardening; docs and tests) plus direct source verification of every BLOCKER/MAJOR. Dependencies still cannot be installed here, so nothing was executed; the "1,111 passed" figure is the developer's, and an AST expansion of the test files reproduces exactly 702 unit + 409 integration.

## 1. Verdict for the whole project at HEAD 194643e

**Good — well ahead of plan, about two days of fixes from a credible Milestone 1 demo, then blocked on the client for the last 15%.**

Two days ago this repository had four routes and eight placeholder screens. It now has the complete vendor-file lifecycle — upload with byte-identical retention, profile-driven CSV/XLSX parsing with a coded import report, a pure deterministic matching engine that obeys the §5.1 chain, an exception queue with audited supersession, append-only snapshots that raise availability events into the watchlist — behind role-guarded, tenant-scoped, audited endpoints, with working screens for every step, a Playwright walk-through of the entire flow, a runbook, seed data, and a 50,000-row import in under a minute. The engine, the exception workflow and the availability logic are correct on every rule I checked, and the tests that prove the Milestone 1 exit criteria (approve → re-import matches at priority 3 with no new exception; 0 → 40 raises exactly one linked event) exist and assert the right things.

What is not there: the four Amazon defects from Part 1 are all still present; the CI smoke job cannot pass as wired, so "verified in CI" is not true for this head; two robustness gaps in the matching stage can strand an import; the browser token lives in localStorage; the Nineyard sync (AC-3) and the products screen are unbuilt because the client has not answered B1; and the documentation has drifted in a dozen places. None of the code problems is more than a few hours.

| Area | Grade | One line |
|---|---|---|
| Import pipeline correctness (parse → match → exceptions → snapshots) | A- | Chain order, ambiguity handling, description-never-matches, APPROVED-only index, supersession, event de-duplication all verified. Two robustness gaps (§2 M1, M2). |
| Security and conventions | B+ | Role guards on every mutation, tenant scoping via the helper, audit in-transaction, no create_all, no naive datetimes, no secrets. Token in localStorage is the exception. |
| Schema and migrations | A | Four clean, reversible migrations; no ordering hazards this time; invariant and round-trip tests updated. |
| Tests | A- | 364 new test functions, concrete end-state assertions, all 11 required scenarios present. No frontend unit tests. |
| CI and deployability | D | compose-smoke fails before Playwright (DEV_AUTH_ENABLED never reaches the container); bind-mount ownership likely breaks uploads in CI; pytest can still skip silently; Railway "deployment" still has no evidence. |
| Documentation honesty | C+ | phase1-status §11 evidence is real (121 named tests all resolve), but README, CLAUDE.md, §3 of the status doc, database-schema.md and architecture.md contradict the code or each other. |

## 2. Findings, ranked

### BLOCKER

**B2 — The compose-smoke CI job cannot pass as wired.** `infra/docker-compose.yml:49-59` passes an explicit `environment:` block to the `api` container with no `env_file:` and no `DEV_AUTH_ENABLED`; `.env.example:57` sets it, but `--env-file` only drives interpolation, so the container never sees it and `Settings.dev_auth_enabled` stays `False`. `.github/workflows/ci.yml:161` then runs `python -m app.cli.seed_dev`, which exits with "DEV_AUTH_ENABLED must be true" (`seed_dev.py:116`), and the job fails before Playwright starts; even if seeded, `POST /api/v1/auth/dev-token` would answer 404. The compose file has not changed since commit `914fb08`, and the status doc's "Verified in CI" and "8 of 8 in GitHub Actions" statements predate this job. Fix: `DEV_AUTH_ENABLED: ${DEV_AUTH_ENABLED:-false}` in the api environment (and the same for `AUTH_JWT_SECRET` if the smoke job needs a non-default key), then watch one green run. Likely second failure behind it: the `../storage:/storage` bind mount is owned by the runner's uid 1001 while the image runs as `appuser`, so the first upload's `mkdir` may get EACCES (`Dockerfile:34-37`, `storage.py:129`) — not verifiable without running.

### MAJOR

**M1 — The matching and snapshot stages have no failure path; a crash strands the import permanently.** `services/matching.py:151` and `services/snapshots.py:139` call `.run()` bare, unlike `import_processing.py:91-99`, which catches, rolls back and `fail()`s. An exception leaves the job RUNNING at `MATCHING`/`SNAPSHOTTING`; the upload route returns 500 although the file and job exist; because the partial unique index only excludes FAILED/CANCELLED jobs and there is no cancel endpoint, re-uploading the same bytes returns the stuck job and that file can never be imported again.

**M2 — A rejected suggestion is silently re-applied on the next import.** `reject` sets the vendor line to UNMAPPED (`services/exceptions.py:200-205`). On the next import `_record_match` sets `line.product_id`, `mapping_status = PENDING` and `mapping_method` (`matching.py:415-420`) *before* `_open_item` recognises the same evidence was already rejected and returns without a queue item (`:442-460`). Net state: the line points PENDING at the product a human rejected, with nothing in the queue to resolve it. No test covers it (`test_exception_api.py:519` covers only NO_MATCH).

**M3 — UPC-only vendor files never produce snapshots, events or watchlist hits.** `ColumnMap` allows `upc` without `vendor_sku` (`profile_rules.py:92-93`), but a vendor line is created only `if row.vendor_sku` (`matching.py:335, 403-407`) and the snapshot stage selects only rows with `vendor_product_id` (`snapshots.py:181`). Rows match, but the brief's core OOS detection is silently inert for such a vendor. Undocumented, untested.

**M4 — Priority 2 (Catalog Item Number) has no input path from vendor files.** `ColumnTarget` (`profile_rules.py:30-40`) has no catalog-item-number target, so `import_job_rows.catalog_item_number` is always NULL and rule 2 always reports "skipped". The chain is correct; the rule is unreachable.

**M5 — Bearer token persisted in `localStorage`.** `frontend/src/lib/auth-storage.ts:14,22,30`; the comment calls it deliberate for the dev credential, but it is an XSS-exfiltratable 480-minute token and the spec said memory only. Acceptable only until the production IdP lands — then it must be an httpOnly cookie or memory.

**M6 — No frontend unit tests.** `frontend/package.json` has lint/typecheck/build only; no Vitest, no Testing Library, no zod (Prompt 16 asked for both). The single Playwright spec is the only UI test, and it cannot currently run in CI (B2).

**M7 — The whole import lifecycle runs synchronously inside `POST /imports`.** `routes/imports.py:103-121` parses, matches and snapshots in-request when `IMPORT_PROCESS_ON_UPLOAD=true` (the default). A 50k-row file is a 40–60 s HTTP request. The status doc acknowledges AC-5.7 (worker) as open; the Amazon `JobRunner` is the natural home.

**M8 — Documentation contradicts the code in the places a new reader looks first.** `README.md:8-11, 182` still say "21 tables, one migration, no vendor CRUD, file ingestion or matching logic exists yet"; `CLAUDE.md:58` still says "calling Amazon is not [in scope]"; `docs/phase1-status.md:63, 133` still say 21 tables / one revision against 25 / 7 at the top of the same file; `docs/database-schema.md:11` says 22 enums while the status doc says 23 (migrations define 23); `:463` still says an AMAZON_SKU identifier "has a marketplace_listing_id", ER optionality still wrong at `:54-55`, "152 database-backed tests" at `:821`; `docs/deployment.md:262` still has the literal TAB in `.\tasks.ps1`; `docs/architecture.md:468` retains "Count: 22 tables" under the superseded banner; "main has not been pushed since 2026-09-14" is now false; CI links point at the old `cbfriedman` URL; "Backend deployed to Railway" still has no URL, date or transcript. The developer's own §11 evidence table is honest and every one of its 121 named tests resolves — the drift is in the prose around it.

### MINOR

- `VendorUpdate.status` lets PATCH set INACTIVE without the active-profile refusal and without touching `is_active` (`schemas/vendors.py:107`, `services/vendors.py:141-143` vs `157-164`).
- Approving an item for a row already MATCHED at priority 1 overwrites the row's attribution to MANUAL_APPROVAL / priority 5 (`services/exceptions.py:398-400`).
- Bare statements bypassing `TenantScope` (all constrained by a scoped id, no leak): `import_processing.py:300-319, 340, 347-356`; `exceptions.py:419`; `repositories/matching.py:99-117`.
- In-file dedupe keeps the *last* occurrence regardless of status, so a valid row superseded by a later ERROR row drops the SKU from the import (`import_processing.py:247-251`).
- Rows with no vendor SKU that end UNMATCHED/AMBIGUOUS open a new exception on every import (`matching.py:440-441`, keyed by a None line id).
- `upc_strip_non_digits` does not strip arbitrary non-digits (`extract.py:376-381`).
- Profile-validate endpoints read the whole body before the 25 MB check (`routes/import_profiles.py:176, 215`); raw download loads the object fully before chunking (`routes/imports.py:266-270`).
- `docker compose config` is still not a CI gate; `pytest -q` still has no skip guard (Part 1 M8); Playwright `retries: 1` under CI masks flakes.
- Two `ApiError` classes (`lib/api-client.ts:27` legacy, `lib/api.ts:14`); a 401 clears the token but not `AuthProvider` state.
- `ix_product_mapping_exceptions_deferred_until` departs from the `ix_<table>_<cols>` convention (model and migration agree, so `alembic check` is fine).
- `perf_import.py` and `explain_queries.py` have no tests.
- ADR 0013 omits the template's alternatives table; restored-copy runs report 408 vs 409 tests with the gap unexplained (`runbook.md:60`).
- Watchlist "add" takes a raw product UUID because the products screen is still a placeholder (blocked on B1).

## 3. What to do now, in order

1. **Fix B2 and get one green CI run for HEAD** (Prompt 25a). Until then, do not tell the client the stack is verified in CI. One hour including the wait.
2. **Stage 2.5 (Amazon corrections 13a–13g)** — unchanged from Part 1; all still apply. One day.
3. **Matching robustness (Prompt 25b)**: failure path + cancel endpoint, rejected-suggestion guard, UPC-only vendor lines, catalog-item-number column target. Half a day.
4. **Frontend hardening (Prompt 25c)**: token out of localStorage, Vitest + a handful of component tests, single ApiError, 401 → signed-out state. Half a day.
5. **Documentation sweep (Prompt 25d)** — README, CLAUDE.md, status §3, database-schema, architecture, deployment, Railway evidence, CI links. One hour, one commit.
6. **Prompt 14 (live Amazon validation)** once credentials arrive; **Prompt 23 (Nineyard sync)** once B1 is answered; the client walk-through on real vendor files once B4 arrives. These three are the only things between this repository and the Milestone 1 exit gate, and all three wait on the client, so send the access request from the recovery plan today if it has not gone.

## 4. A note on pace

My plan estimated six to seven weeks for Milestone 1; the developer landed the equivalent in about two days with Claude Code. The code quality held up remarkably well under that speed — the matching engine and exception workflow are exactly what the brief and CLAUDE.md ask for. Where the speed shows is in the things that need a running environment rather than a test: the CI job nobody watched go green, the deployment claim with no URL, the docs that describe last week's schema. Those are cheap to fix and expensive to be caught on by the client, which is why they lead the list above.
