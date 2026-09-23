# Milestone 1 — What to require from the client

Two parts: the internal view (what blocks what, so you know which ask to chase), and a paste-ready message.

---

## Part 1 — Internal: the asks, by consequence

### A. Hard blockers — Phase 1 cannot be completed without these

| # | Ask | Why it blocks | Chase by |
|---|---|---|---|
| 1 | **Nineyard API login** — email, password, company id for an integration user with read access to Items, Skus, Vendors | Nothing can pull the catalog. AC-3 (catalog synchronisation) is entirely unbuilt and is the largest open item in Phase 1. | Wed 23 Sep |
| 2 | **Four Nineyard field answers** — (a) which field is the Catalog Item Number and is it stable across syncs, (b) how Items relate to Skus, (c) is there an "updated since" filter, (d) how do deleted items appear (absent, or a flag) | The whole product-identity model rests on (a). Getting (d) wrong would silently deactivate live products. No probe run can answer these; a person at Nineyard or on their team must. | Wed 23 Sep |
| 3 | **3–5 real vendor inventory files** (CSV and XLSX, anonymised if needed, including the one they consider messiest) | Exit criterion 4 of Milestone 1 is "a CSV and an XLSX vendor file each import end to end". Fixtures do not satisfy it, and real files always reveal profile rules that synthetic ones do not. | Wed 23 Sep |
| 4 | **Vendor master data** — for each vendor to be set up: name, contact name(s) and email address(es), minimum order quantity/value, typical lead time, any purchasing terms, and which identifier that vendor uses (UPC or their own SKU) | The vendor records and import profiles are created from this. Without it the demo uses invented vendors. | Wed 23 Sep |
| 5 | **Hosting decision** — Azure (recommended) or stay on Railway, plus who owns the subscription | Exit criterion 1 is a working deployed stack. Cannot deploy to an environment that does not exist. | Fri 2 Oct |

### B. Decisions that shape what gets built (not blockers, but cheap now and expensive later)

| # | Ask | Default if they do not answer |
|---|---|---|
| 6 | **Pack-size policy** — store vendor quantities exactly as the vendor states them plus pack size (recommended), or convert to your catalog unit at import | Store as stated. Milestone 1 makes no purchasing decisions, so conversion can wait and a wrong conversion is hard to unwind. |
| 7 | **Identity** — do users sign in with Microsoft 365 / Entra ID? | Assume yes. If no, a local password login must be added (about two days) and should be priced as a change request. |
| 8 | **Users and roles** — names and email addresses, each mapped to Admin, Purchasing Manager, Data Operator or Viewer | Create one Admin for them and one for you; add the rest at acceptance. |
| 9 | **Raw file retention** — where retained vendor files should live (local disk, Azure Blob, S3) and for how long; any compliance requirement | Local disk in Phase 1, backend-agnostic, so a move later needs no schema change. |
| 10 | **Any vendor who does NOT send CSV or XLSX** — PDF, fixed-width text, a table pasted into the email body, a Google Sheets link | Important: Milestone 1 covers CSV and XLSX only. If a PDF-only vendor exists, say now that it is Phase 4 work, in writing, before it becomes an assumed inclusion. |

### C. Needed for acceptance and payment

| # | Ask |
|---|---|
| 11 | **Named purchasing manager** who will resolve the exception queue and sign the acceptance checklist, with about one hour a week reserved |
| 12 | **A 45-minute acceptance session** booked in advance for the week of 6 October |
| 13 | **Phase 1 funded in escrow** before work resumes |
| 14 | **Scale confirmation** — roughly 450 active products, and how many vendors? Typical and maximum rows per vendor file? |
| 15 | **A short list of products they currently want but cannot get** — so the out-of-stock watchlist demonstration uses real buying intent, not invented rows |

### D. Say nothing about

Do not raise the Amazon proof of concept in the same message as Milestone 1 unless they ask — it has its own gate and its own credential request, and mixing them invites them to bundle the two approvals into one delay. Send the SP-API request separately (it is item 4 in the client to-do list).

---

## Part 2 — Paste-ready message

> Subject: Milestone 1 — what I need from you to finish and hand over
>
> Hi,
>
> Milestone 1 is substantially built. The database, vendor records, import profiles, CSV/XLSX import with the validation report, the product-matching engine, the exception queue, the out-of-stock watchlist with "now available" detection, the audit trail and the admin screens are all working, with an automated test that drives the whole flow end to end.
>
> To finish it and demonstrate it on your data rather than on test data, I need the following. The first four are what actually hold the work up, and I would like them by **Wednesday 23 September**.
>
> **1. Nineyard access.** An API login (email, password and company id) for an integration user with read access to Items, Skus and Vendors. Please send it through a password manager share rather than email.
>
> **2. Four answers about Nineyard's data.** These decide how your catalog is modelled, and no amount of testing on my side can establish them — I need someone at Nineyard or on your team to confirm:
> - Which field is the Catalog Item Number, and is it stable across syncs?
> - How do Items relate to Skus — one Item to many Skus, or something else?
> - Is there an "updated since" filter, or must each sync pull the full catalog?
> - How does a deleted item appear — does it simply vanish from the results, or is there a status flag? (Getting this wrong would deactivate live products, so I will not guess.)
>
> **3. Three to five real vendor inventory files.** CSV and XLSX, anonymised if you prefer, and please include the one you consider messiest — that one is the most useful. With each, roughly how often that vendor sends it and how many rows a typical and a large file contain.
>
> **4. Vendor details** for the vendors you want set up first: name, contact name and email address(es), minimum order quantity or value, typical lead time, any purchasing terms, and whether that vendor identifies products by UPC or by their own SKU.
>
> Then, by **Friday 2 October**:
>
> **5. Where the system should run.** I recommend Microsoft Azure, since you are already on Microsoft 365 and Power BI — sign-in and email integration then come with what you have. If you would rather I keep it on the current hosting for now, that is fine too.
>
> **6. Sign-in.** Do your users sign in with Microsoft 365 / Entra ID? If so I will wire that up; if not, tell me and I will add a password login.
>
> **7. Users and roles.** Names and email addresses, each as Admin, Purchasing Manager, Data Operator or Viewer.
>
> **8. Two small policy decisions:**
> - Should vendor quantities be stored exactly as the vendor states them, with the pack size recorded alongside (my recommendation), or converted to your catalog unit at import?
> - Where should the original vendor files be kept, and for how long? Every file is retained unchanged for audit; I just need to know where they should live.
>
> **9. One thing worth flagging now.** Milestone 1 handles CSV and XLSX files. If any vendor sends inventory as a PDF, a fixed-width text file, or a table pasted into the email body, tell me which ones — that is Phase 4 work, and I would rather we both know now than discover it during acceptance.
>
> Finally, for the handover itself:
>
> **10.** Who is the purchasing manager who will approve product mappings and sign off the milestone? I would like to book a 45-minute session with them in the week of 6 October to walk through the system on your data.
>
> **11.** So I can size things correctly: roughly how many active products and how many vendors?
>
> **12.** A short list of products you currently want but cannot get from any vendor — I will load those into the watchlist so the "now available" alert demonstrates real buying intent rather than an example.
>
> Once items 1 to 4 are in, the remaining Milestone 1 work is about three weeks: the Nineyard catalog sync and product search, validation against your real files, deployment, and the documentation handover.
>
> Thanks,
> Osama
