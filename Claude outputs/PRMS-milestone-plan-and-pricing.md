# Purchasing & Replenishment Management System
## Milestone Plan, Timeline and Pricing

Prepared for: cbfriedman
Prepared by: Osama Khan
Date: 16 September 2026
Repository: github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory (ownership transfers to you on payment, per section 9)

---

## 1. Summary

You asked for revised milestone pricing across seven phases, with cost, timeline and deliverables for each, and for two clarifications: whether I personally implement the Amazon integration, and what the Amazon proof of concept will demonstrate before the larger system proceeds.

**Amazon.** I implement the Amazon SP-API integration personally. There is no subcontractor or specialist, and nothing outside this proposal is required to deliver it. The read-only ingestion your proof of concept asks for is already built in the repository — LWA authentication, the orders report feeding 7/14/30-day velocity, FBA inventory including the three inbound states plus FBM quantity, listings mapped to your Catalog Item # / UPC through the matching rules, PostgreSQL storage, scheduled ingestion, and structured error handling and logging — and is waiting only for credentials to your seller account to be validated live. That validation is the first paid gate in Phase 2 and can be demonstrated within five working days of receiving credentials, in parallel with the remainder of Phase 1.

**Where the project stands.** Phase 1 is substantially built: the database foundation, vendor database, versioned import profiles, CSV/XLSX import with byte-identical file retention and a coded import report, the deterministic product-matching engine, the exception queue, out-of-stock watchlist with availability detection, audit trail, and a working administration interface with an automated end-to-end test. What remains in Phase 1 is the Nineyard catalog synchronisation (which needs two answers from Nineyard, listed in section 7), validation against your real vendor files, deployment to your environment, and a documentation pass.

**Totals.** Seven phases, **USD 9,500** fixed price, **about 26 weeks** end to end (17 September 2026 to 19 March 2027), delivered sequentially with the Amazon proof of concept brought forward. Every phase ends with a live demonstration on your data, automated tests green in continuous integration, updated documentation, and a written acceptance checklist mapped to your brief.

| Phase | Scope | Duration | Target dates | Fixed price (USD) |
|---|---|---|---|---|
| 1 | Database + Nineyard + Vendor Inventory + OOS | 3.5 weeks (remaining) | 17 Sep – 9 Oct 2026 | 1,500 |
| 2 | Amazon + ConnectBooks + Replenishment | 4 weeks | 12 Oct – 6 Nov 2026 | 1,700 |
| 3 | Purchasing Budget Optimization | 3 weeks | 9 Nov – 27 Nov 2026 | 1,100 |
| 4 | Vendor RFQ / Quotes / POs (incl. vendor email automation) | 5 weeks + holiday buffer | 30 Nov 2026 – 8 Jan 2027 | 1,900 |
| 5 | Analyzer.tools / New Products | 3 weeks | 11 Jan – 29 Jan 2027 | 1,000 |
| 6 | Power BI / Advanced Reporting (incl. vendor performance, AI assistance) | 4 weeks | 1 Feb – 26 Feb 2027 | 1,300 |
| 7 | Walmart Integration | 3 weeks | 1 Mar – 19 Mar 2027 | 1,000 |
| | **Total** | **~26 weeks** | | **9,500** |

Prices are fixed per phase and include design, development, automated tests, documentation, deployment to your environment, a demonstration session, and 30 days of defect fixes after acceptance. The total reflects the work already completed in Phase 1 and an efficient, test-driven delivery approach; it is a fixed commitment for the full scope of your brief, not an estimate. Out-of-scope change requests are billed at USD 35 per hour with prior written approval.

---

## 2. How each phase is accepted

Every phase uses the same definition of done, so there is never a question of whether a milestone is complete:

1. A live demonstration on your real data (not fixtures) of every deliverable listed for the phase.
2. All automated tests green in the GitHub Actions pipeline for the delivered commit, including the full Docker stack starting from a clean checkout.
3. Documentation updated in the repository: architecture, database schema, runbook, and an acceptance-criteria table where each criterion names the test or command that proves it.
4. Deployed to your environment (section 6) with the previous phase still working.
5. A short written acceptance checklist, mapped to the numbered sections of your brief, which you sign off. Payment for the phase is released on that sign-off.

Deterministic rules stay deterministic throughout: sales velocity, profitability, purchase quantities, budget allocation and vendor comparison are formulas and solvers over structured data. AI is used only where your brief invites it — reading unfamiliar files, extracting quotes from emails and PDFs, explaining a recommendation — and always with a person confirming the result.

---

## 3. Phase-by-phase deliverables

### Phase 1 — Database + Nineyard + Vendor Inventory + OOS
*Brief sections 2, 3, 4, 5, 26, 31, 33, 34. Price USD 1,500. 17 September – 9 October 2026.*

Already delivered and demonstrable today:

- PostgreSQL schema (25 tables) with immutable UUID keys, Catalog Item # as the business key, one-to-many Amazon SKUs per product, UTC timestamps throughout, and reversible migrations.
- Vendor database with contacts, minimum order quantity/value and purchasing terms; versioned per-vendor import profiles that describe each vendor's file layout (column map, normalisation, availability and price rules), with a "validate against a sample file" preview.
- File upload with byte-identical raw retention and de-duplication; CSV and XLSX parsing that tolerates encodings, delimiters, header rows and sheets; per-row validation with coded outcomes (invalid UPC, invalid quantity, invalid price, duplicate in file, unknown product) and the import report your brief describes ("1,245 rows imported, 7 unmatched, 3 invalid quantities…").
- Deterministic product matching in the exact order you specified — UPC → Catalog Item # → approved vendor-SKU mapping → approved Amazon-SKU mapping → human-confirmed suggestion — with the deciding rule recorded on every row, ambiguity always routed to the exception queue, and product descriptions never used as the sole basis for an automatic match.
- Exception queue where a purchasing manager approves, rejects or defers a mapping; an approval becomes a permanent mapping used automatically on the next import; changing an approved mapping requires an explicit, audited supersession.
- Append-only vendor inventory history, availability events on every transition, and the OOS watchlist that flags "Product ABC is now available from Vendor B" the moment a file shows it, with desired quantity, maximum unit cost and priority per watched item.
- Audit trail (append-only at the database level) of every vendor, profile, import, mapping and watchlist change; role-based access (Admin, Purchasing Manager, Data Operator, Viewer).
- Administration interface: sign-in, vendors, import profiles, imports with report and row browser, exception queue, watchlist, availability feed, audit log, dashboard; an automated browser test drives the entire flow.
- 50,000-row vendor files import in under a minute.

Remaining in this phase:

- Nineyard catalog synchronisation into products, identifiers and Amazon SKU relationships, with raw payloads retained, scheduled nightly, and the products screen (search by Catalog Item #, UPC, Amazon SKU, name). Needs the Nineyard answers in section 7.
- Validation against three to five of your real vendor files, including the messiest one, and any profile-rule additions they reveal.
- Fail-safe handling for every stage of an import, so a failed import can always be cancelled and re-run.
- Deployment to your environment and the documentation pass.

Acceptance: a vendor and its profile created through the interface; your CSV and XLSX files imported end to end with the report; a matched row, an ambiguous row and an unmatched row shown resolving through the queue; an approved mapping reused automatically on re-import; a watched product flipping to available; the Nineyard sync populating your catalog.

### Phase 2 — Amazon + ConnectBooks + Replenishment
*Brief sections 6, 7, 8, 9, 21 (first three dashboard sections), 22, 32. Price USD 1,700. 12 October – 6 November 2026.*

**2a — Amazon proof of concept (your seven points), first gate.** Already built; validated live within five working days of credentials, and I will bring it forward to run alongside Phase 1 if credentials arrive first.

- SP-API / LWA authentication with credentials held only in server-side environment variables, never in the interface, and redacted from every log line.
- Sales data for 7/14/30-day velocity from the all-orders report, stored per order line and SKU, cancellations excluded, re-runnable without duplicates.
- FBA inventory (fulfillable, reserved, unfulfillable, and inbound working/shipped/receiving) plus FBM quantity from the listings report.
- Amazon seller SKUs mapped to your Catalog Item # / UPC through the same matching rules as vendor files, with anything uncertain in the exception queue.
- PostgreSQL storage, scheduled ingestion in a dedicated worker process, run history with counts and errors, alerting on failure, and a `/api/v1/amazon/status` freshness endpoint.
- Sign-off document with the evidence for each of your seven points, including a side-by-side of our 30-day units against Seller Central for three SKUs.

**2b — ConnectBooks profitability.** CSV import through the same profile mechanism as vendor files, associating selling price, product cost, Amazon fees, shipping, advertising, net profit, margin and ROI with the right Amazon SKU / UPC / Catalog Item #; period-aware so the latest figures are used; designed so QuickBooks, Xero or another source can replace the CSV later without schema change.

**2c — Replenishment engine.** Per product and per SKU: units per day and per 7/14/30 days, trend, on hand (FBA + FBM), inbound, days of supply; required inventory = average daily sales × target days; suggested replenishment = required − available − inbound, adjusted for lead time, vendor availability and minimum order quantities. Every parameter (target days, which velocity window drives the decision, lead time per vendor, safety days) is configurable, not hard-coded. Each recommendation shows the vendors that currently have stock, quantity available and cost, and the expected cost, revenue, profit and ROI.

**Dashboard and search.** Urgent Replenishment, Best Replenishment Opportunities, Newly Available OOS Products; search by Catalog Item #, UPC, Amazon SKU, product name, vendor, vendor SKU; filters for stock status, vendor availability, velocity, profitability, ROI, days of supply.

Acceptance: the seven proof-of-concept points demonstrated on your account; your ConnectBooks export imported and profitability shown per SKU; the worked example from section 36 of your brief (20 units/day, 50 on hand, 20 inbound, 14-day target, three vendors) reproduced with the expected numbers.

### Phase 3 — Purchasing Budget Optimization
*Brief sections 10, 28, 29, 30, 23. Price USD 1,100. 9 – 27 November 2026.*

- Configurable purchase score: demand, profitability, ROI, stock urgency, vendor availability, price and budget efficiency with weights you set in the interface (the 25/25/20/15/10/5 example as the default).
- Budget optimiser: enter an available budget and the system returns the combination of purchases that maximises expected profit within it — a true allocation (Google OR-Tools), not a ranked list — respecting vendor stock, recommended quantities, minimum order quantities and values, and any per-vendor or per-product limits you set. The example in section 38 of your brief (USD 175,000 of opportunities, USD 50,000 budget) returns recommended spend, expected revenue, profit and ROI.
- Approval workflow: the purchasing manager reviews, edits quantity, vendor, cost, priority and purchase/no-purchase per line, then approves; nothing is ordered automatically. Every recommendation, edit and decision is recorded with the velocity and profitability at the time, so the engine's decisions can be evaluated later.
- Best Use of Available Budget dashboard section.

Acceptance: a budget entered and an allocation returned in seconds on your full catalog; changing a weight changes the ranking; editing a line and approving produces an audited decision record.

### Phase 4 — Vendor RFQ / Quotes / Purchase Orders
*Brief sections 4 (email intake), 11, 12, 13, 14, 15, 24 (basic), 32 (email). Price USD 1,900. 30 November 2026 – 8 January 2027 (includes the holiday period). Paid as two milestones: 4a USD 950 on RFQ and email intake; 4b USD 950 on quotes, vendor selection and POs.*

- Email integration (Microsoft 365 / Graph, or SMTP/IMAP if you prefer) for sending and receiving.
- Vendor inventory files arriving by email are recognised by sender and layout and imported automatically, with the manual upload still available — the "preferred workflow" of section 4 of your brief.
- Price requests: from any recommendation, select vendors that have the product and send a templated request (description, UPC, quantity) with a full record of what was sent, when, to whom.
- Vendor responses: email text, Excel, CSV and PDF attachments processed into structured quotes (product, UPC/vendor SKU, quantity available, unit cost, minimum quantity, terms), with AI-assisted extraction that a person confirms before it is used.
- Quote comparison and vendor selection recommendation weighing unit cost, quantity available, expected profit, ROI, lead time, minimum order and vendor reliability, with configurable factors.
- Purchase orders: generated on approval with PO number, date, vendor, Catalog Item #, UPC, description, vendor SKU, quantity, unit cost and totals; downloadable as PDF, emailed to the vendor, kept as history with status (created, sent, acknowledged, completed).
- Basic vendor performance record: quoted vs actual price, response time, fill rate, purchasing volume.
- Dashboard sections: Vendor Price Requests, Vendor Quotes, Purchase Orders.

Acceptance: scenario 1 from section 36 of your brief run end to end — recommendation → price requests to three vendors → a quote reply parsed → recommendation updated → approved → PO generated and emailed.

### Phase 5 — Analyzer.tools / New Products
*Brief sections 16–20. Price USD 1,000. 11 – 29 January 2027.*

- Analyzer.tools API integration retrieving product research data (estimated sales, revenue, competition, sellers, price, profitability metrics).
- Opportunity engine comparing candidates against your Nineyard catalog and Amazon listings to identify products you do not sell, scoring them on configurable criteria and ranking them (the section 18 table).
- Vendor search by UPC (and other identifiers) across your vendor inventory to find who carries each opportunity.
- The new-product purchasing workflow reusing Phase 2–4 machinery: profitability at each vendor's cost → recommended quantity → price request → quotes → vendor recommendation → PO.
- New Product Opportunities dashboard section and the new-product / replenishment filter.

Acceptance: scenario in section 39 of your brief demonstrated with live Analyzer.tools data.

### Phase 6 — Power BI / Advanced Reporting
*Brief sections 23, 24, 27, 44 (Power BI). Price USD 1,300. 1 – 26 February 2027.*

- Reporting layer in PostgreSQL (clean, documented views) feeding Power BI: sales and velocity, profitability, inventory and days of supply, purchasing and POs, vendor performance, recommendations versus outcomes, historical trends.
- Power BI dataset and reports with DAX measures for true net margin per item, scheduled refresh, row-level access aligned to your roles.
- Vendor performance scoring incorporated into vendor recommendations.
- AI assistance where it earns its place: plain-language explanation of why an item is recommended, summaries of purchasing opportunities, anomaly flags on unusual data, natural-language search over the purchasing database — never replacing the deterministic calculations.
- Historical analysis: previous vendor cost, quantity, vendor, quotes and POs per product; decision quality over time.

Acceptance: the Power BI report opened by a purchasing manager answering the six success-criteria questions in section 43 of your brief.

### Phase 7 — Walmart Integration
*Price USD 1,000. 1 – 19 March 2027.*

- Walmart Marketplace API integration reusing the marketplace abstraction already in the database (listings, sales, inventory keyed by marketplace).
- Walmart SKUs mapped to Catalog Item # / UPC through the same matching rules; Walmart demand and inventory included in velocity, replenishment and budget allocation; marketplace filter across the interface and reports.

Acceptance: a product sold on both marketplaces showing combined velocity and a single replenishment recommendation.

---

## 4. Timeline

Sequential delivery, one phase at a time, with the Amazon proof of concept pulled forward. Dates assume the dependencies in section 7 arrive by the dates shown there; each week of delay on a dependency moves the phases that need it by a week.

| Milestone | Start | Demonstration / acceptance |
|---|---|---|
| Phase 1 (remaining) | Thu 17 Sep 2026 | Fri 9 Oct 2026 |
| Phase 2a — Amazon proof of concept | within 5 working days of SP-API credentials | as early as Fri 2 Oct 2026 |
| Phase 2b–2c — ConnectBooks + replenishment | Mon 12 Oct 2026 | Fri 6 Nov 2026 |
| Phase 3 — budget optimisation | Mon 9 Nov 2026 | Fri 27 Nov 2026 |
| Phase 4a — RFQ and email intake | Mon 30 Nov 2026 | Fri 18 Dec 2026 |
| Phase 4b — quotes, vendor selection, POs | Mon 21 Dec 2026 | Fri 8 Jan 2027 |
| Phase 5 — Analyzer.tools / new products | Mon 11 Jan 2027 | Fri 29 Jan 2027 |
| Phase 6 — Power BI / advanced reporting | Mon 1 Feb 2027 | Fri 26 Feb 2027 |
| Phase 7 — Walmart | Mon 1 Mar 2027 | Fri 19 Mar 2027 |

Working cadence: a short written progress note every Friday with a link to the demonstrable build, and a 15-minute screen-share whenever a deliverable is ready rather than only at phase end. Phases 5 and 7 can overlap earlier phases if you want to compress the calendar; the price does not change.

---

## 5. Pricing and payment terms

| Item | Terms |
|---|---|
| Pricing model | Fixed price per phase, funded in escrow at phase start, released on written acceptance. Phase 4 is split into two equal milestones. |
| Included in each price | Design, development, automated tests, documentation, deployment to your environment, demonstration, acceptance checklist, and 30 days of defect fixes after acceptance at no charge. |
| Change requests | Scope additions beyond the brief are estimated in writing first and billed at USD 35 per hour, or quoted as a fixed addition to the phase. |
| Post-launch support (optional) | USD 250 per month for up to 10 hours: monitoring, vendor-file format changes, API changes (Amazon, Nineyard, Walmart), small enhancements. Unused hours do not roll over. Cancel any month. |
| Validity | Prices and dates valid for 30 days from this document. |

---

## 6. Hosting, running costs and third-party components

**Recommended platform: Microsoft Azure**, because your operation already runs on Microsoft 365, Power BI and Power Automate; Entra ID sign-in and Microsoft Graph email then come with what you have. The application runs today on Railway for development and can stay there if you prefer a simpler setup; the code does not depend on either.

Estimated monthly running costs (US East pricing, September 2026, excluding tax):

| Component | Estimate (USD / month) | Notes |
|---|---|---|
| Azure Database for PostgreSQL Flexible Server (Burstable B1ms, 32 GB, 7-day backup) | 15 – 25 | Compute about USD 12; storage and backup extra. Move to B2s (about USD 30) if reporting load grows. |
| Azure Container Apps — API, worker, web (one small replica each) | 20 – 40 | Roughly USD 7 per idle replica per month after the free grant; more with traffic. |
| Azure Blob Storage for retained vendor files and PO PDFs | under 5 | |
| OpenAI API (Phase 4 onward: quote extraction, explanations) | 5 – 30 | Small models cost USD 0.05 – 0.60 per million tokens; a few hundred vendor emails a month is a few dollars. |
| Power BI Pro (Phase 6) | 14 per user | Already included if your users have Microsoft 365 E5. |
| Domain and TLS | about 1 | Managed certificate is free. |
| Amazon SP-API, Walmart Marketplace API, Nineyard API | 0 | No API fees. |
| Analyzer.tools | your existing subscription | API access may require a specific plan tier — to confirm with them before Phase 5. |
| **Total, excluding Power BI seats and existing subscriptions** | **about 45 – 100** | |

Third-party components and licences (per section 46 of your brief): every library used is open source with a permissive licence — FastAPI, SQLAlchemy, Alembic, Pydantic, Next.js, React, APScheduler, openpyxl (MIT); Google OR-Tools, Playwright (Apache 2.0); python-amazon-sp-api (MIT); PostgreSQL (PostgreSQL licence). Paid services are usage-based (OpenAI, Azure) or per-seat (Power BI). No proprietary component, no licence fee owed to me, and no ongoing dependency on my accounts: everything runs in your Azure subscription, your GitHub organisation and your Microsoft 365 tenant.

---

## 7. What I need from you, and when

| Item | Needed by | Used in |
|---|---|---|
| Amazon SP-API: a private app in Seller Central (Apps & Services → Develop Apps) with roles Inventory and Order Tracking, Product Listing, Selling Partner Insights; the LWA client id, client secret and refresh token shared through a password manager or your server's environment (never chat or email); Seller ID and marketplace | Fri 25 Sep 2026 | Phase 2a |
| Nineyard: an API login (email, password, company id) for an integration user with read access to Items and Skus; and a contact at Nineyard or on your team who can confirm two things — which field is the Catalog Item Number, and how Items relate to Skus (plus whether an "updated since" filter exists and how deleted items appear) | Wed 23 Sep 2026 | Phase 1 |
| Three to five real vendor inventory files (CSV and XLSX; anonymised if needed; include the messiest one), with typical and maximum row counts and each vendor's sending cadence | Wed 23 Sep 2026 | Phase 1 |
| One ConnectBooks CSV export | Fri 9 Oct 2026 | Phase 2b |
| Azure subscription (or confirmation to stay on Railway) and confirmation that users sign in with Microsoft 365 / Entra ID | Fri 2 Oct 2026 | Phase 1 deployment |
| The mailbox vendors send files to, and Microsoft 365 admin consent for Graph mail access | Fri 20 Nov 2026 | Phase 4 |
| Analyzer.tools API documentation and key | Mon 4 Jan 2027 | Phase 5 |
| Walmart Marketplace API credentials | Mon 22 Feb 2027 | Phase 7 |
| A purchasing manager available for about one hour a week for review and acceptance sessions | ongoing | all phases |

---

## 8. Risks and how they are handled

- **Nineyard field semantics.** The whole identity model rests on which Nineyard field is the Catalog Item Number. I will not guess; the sync waits for the answer, and the read-only diagnostic already in the repository shows exactly what the API returns so the conversation with Nineyard is short.
- **Vendor files differ from expectations.** Profiles are data, not code, so a new layout is a new profile, not a release. The "validate against a sample" preview lets you check a profile before the first import.
- **Amazon report data quality.** Velocity is anchored to the last successful sync rather than the clock, cancellations are excluded, and the proof-of-concept sign-off compares our numbers with Seller Central before anything depends on them.
- **API changes** (Amazon, Nineyard, Walmart, Analyzer.tools). Each integration sits behind its own boundary layer with its own tests, so a change is contained to one module; the optional support retainer covers keeping them current.
- **Scope growth.** Anything beyond the brief goes through the change-request step in section 5 before work starts.

---

## 9. Ownership and handover

All application source code, database schema, migrations, configuration, documentation, test suites, Power BI files and project-specific work product belong to you on payment for the phase that produced them, subject to our development agreement. The repository already sits in a GitHub account that transfers to your organisation at your request; credentials for Azure, Microsoft 365 and third-party services are yours from the start and are never stored outside your environment. At the end of each phase you receive the updated schema documentation, deployment instructions and runbook, so the system can be operated or extended by anyone you choose.

---

## 10. Next step

If this plan works for you, the three items due 23–25 September in section 7 are what unblock the Amazon proof of concept and the Nineyard sync. Once the SP-API credentials arrive, I will schedule the proof-of-concept demonstration within five working days, and we can fund Phase 1 and Phase 2a in parallel.
