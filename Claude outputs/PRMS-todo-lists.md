# PRMS — To-do lists (as of 16 September 2026)

Repository head `194643e`. Prompt numbers refer to `claude-code-prompt-sequence.md`; review items refer to `code-review-2026-09-16.md`.

---

## A. Osama — in order

### This week (no client input needed)

1. **Send the client the milestone plan** (`milestone-plan-and-pricing.md`) together with the client to-do list below. Ask them to fund Phase 1 and Phase 2a in escrow.
2. **Prompt 25a — make CI green.** Add `DEV_AUTH_ENABLED` (and the other smoke-test variables) to the api service in `infra/docker-compose.yml`, fix the `/storage` bind-mount ownership, add the `docker compose config` gate and the pytest skip guard, push, and watch all three jobs pass for the current commit. Until this is green, do not tell the client the stack is "verified in CI". About 1 hour plus the wait.
3. **Decide the UPC question** (review Part 1, section 4). Recommendation: Option A — an unambiguous UPC match on an Amazon listing approves automatically. Write your choice into the bracket in Prompt 13e (ADR number is 0014).
4. **Stage 2.5 — Amazon corrections, Prompts 13a → 13g.** 13a first: it fixes the datetime-in-JSON bug that will fail the very first live call to Amazon. About one day.
5. **Prompt 25b — matching robustness.** Fail-safe matching/snapshot stages and a cancel endpoint, rejected-suggestion guard, UPC-only vendor lines, catalog-item-number column target. Half a day.
6. **Prompt 25c — frontend hardening.** Token out of localStorage, single ApiError, Vitest with eight component tests, Playwright retries 0 in CI. Half a day.
7. **Prompt 25d — documentation sweep.** README, CLAUDE.md line 58, status doc §3, database-schema, architecture, deployment TAB, CI links to the frostwebdev URL. One hour, one commit.
8. **Railway.** Either deploy the current `main` there (or to Azure once the client decides) and put the URL and a health-check transcript in `docs/phase1-status.md` §6, or delete the "deployed to Railway" claim. Do not leave it unevidenced.
9. **Attach the repository to this Cowork session's GitHub access** in the Claude app if you want me to read CI results or push from here; otherwise I keep working from the public clone.

### As soon as the client delivers (section B)

10. **Prompt 14 — live Amazon validation** within five working days of the SP-API credentials: auth, three syncs, velocity table, Seller Central cross-check on three SKUs, failure drill, 48-hour scheduler run, sign-off document. Then a 15-minute demo to the client. This is the client's stated gate for the whole project.
11. **Prompt 23 — Nineyard sync** once the four B1 answers are in: run the probe with real credentials, commit the sanitised output, build the sync, then the products screen.
12. **Real vendor files (B4):** create a profile per vendor, import each file, fix any parsing or rule gap they reveal, and add the messiest one as a regression fixture.
13. **Phase 1 acceptance:** run the exit-gate walk-through from `docs/acceptance-criteria.md` on the client's data with the client watching, deliver the acceptance checklist mapped to brief sections, and request escrow release.
14. **Phase 2 kick-off:** design the ConnectBooks import profile from the client's CSV sample; then the replenishment engine (configurable target days, lead time, MOQ, inbound).

### Ongoing habits

15. Friday progress note with a link to the demonstrable build; 15-minute screen-share whenever a deliverable is ready.
16. Real commit messages (no more "f"); `docs/phase1-status.md` updated in the same commit as the code it describes; never claim CI, deployment or test counts the repository cannot show.
17. Anything outside the brief goes through the change-request step before work starts.

---

## B. Client (cbfriedman) — what I need from you, and by when

| # | Item | Needed by | Unblocks |
|---|---|---|---|
| 1 | Confirm the milestone plan and fund Phase 1 and Phase 2a in escrow | Fri 18 Sep 2026 | Start |
| 2 | **Nineyard:** an API login (email, password, company id) for an integration user with read access to Items and Skus; and a contact at Nineyard or on your team who can answer four questions — which field is the Catalog Item Number; how Items relate to Skus; whether an "updated since" filter exists; how deleted items appear | Wed 23 Sep 2026 | Phase 1 — catalog sync and products screen |
| 3 | **Vendor files:** three to five real inventory files (CSV and XLSX, anonymised if needed, include the messiest one), each vendor's sending cadence, and typical and maximum row counts | Wed 23 Sep 2026 | Phase 1 — real-data validation and acceptance |
| 4 | **Amazon SP-API:** create a private app in Seller Central (Apps & Services → Develop Apps) with roles Inventory and Order Tracking, Product Listing, Selling Partner Insights; self-authorise it; share the LWA client id, client secret and refresh token through a password manager or your server's environment — never chat or email; plus Seller ID and marketplace | Fri 25 Sep 2026 | Phase 2a — the proof of concept you asked for |
| 5 | Three SKUs whose 30-day units I can cross-check against your Seller Central Business Reports during the proof-of-concept sign-off | Fri 25 Sep 2026 | Phase 2a |
| 6 | Name the purchasing manager who approves product mappings and purchases, and reserve about one hour a week for review and acceptance sessions | Fri 25 Sep 2026 | All phases |
| 7 | Hosting decision: Azure (recommended) or stay on Railway; and confirm whether users sign in with Microsoft 365 / Entra ID | Fri 2 Oct 2026 | Phase 1 deployment |
| 8 | Pack-size policy: should vendor quantities be stored as stated (recommended for now) or converted to your catalog unit at import? | Fri 2 Oct 2026 | Phase 1 import rules |
| 9 | One ConnectBooks CSV export | Fri 9 Oct 2026 | Phase 2b — profitability |
| 10 | The mailbox vendors send files to, and Microsoft 365 admin consent for Graph mail access (or SMTP/IMAP details if not on Microsoft 365) | Fri 20 Nov 2026 | Phase 4 — email intake and RFQs |
| 11 | Analyzer.tools API documentation and key (confirm your plan tier includes API access) | Mon 4 Jan 2027 | Phase 5 |
| 12 | Walmart Marketplace API credentials | Mon 22 Feb 2027 | Phase 7 |

Items 2, 3 and 4 are the ones that matter this week: 2 and 3 finish Phase 1, and 4 lets me demonstrate the Amazon proof of concept within five working days of receiving it.
