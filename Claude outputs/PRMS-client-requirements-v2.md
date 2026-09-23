# What to require from the client — consolidated, 16 September 2026

Supersedes the earlier Milestone 1 requirements list. **Vendor files: received and analysed — no longer needed.** Fifteen items remain, in three tiers.

---

## Tier 1 — Blocking Phase 1. Chase these hardest.

**1. Nineyard API login.** Email, password and company id for an integration user with read access to Items, SKUs and Vendors. Password manager, not email. *Without this the catalog sync cannot be built and Phase 1 cannot close.*

**2. Four answers about Nineyard's data.** No amount of testing on our side can establish these; a person at Nineyard or on their team must confirm:
- Which field is the Catalog Item Number, and is it stable across syncs?
- How do Items relate to SKUs?
- Is there an "updated since" filter, or must every sync pull the full catalog?
- How does a deleted item appear — absent from results, or a status flag?

**3. Vendor details for the five vendors whose files arrived** — NYB International, Perfume Center of America, French Perfumes International, YM Trading, TJS Group. For each: contact name and email address(es), minimum order quantity or value, typical lead time, payment terms, and **which of them send daily versus weekly**, and to which mailbox. The two PDFs gave fragments (French Perfumes: Net 30, customer pickup; PCA: advance wire, LTL) but not enough to create the vendor records.

---

## Tier 2 — Decisions their own files raised. Needed before the import profiles are configured.

**4. What does quantity 999 mean?** It appears in 264 rows across French Perfumes (140), YM Trading (83) and TJS (41). If it means "plenty, we won't run out", we treat it as available-with-unknown-quantity. If taken literally, the replenishment engine will later cap purchase recommendations at 999 units and quietly under-order.

**5. Cells containing several barcodes — two different meanings, one decision needed.**
- TJS, 13 rows: `8028713270017 / 8028713828126` — the same product with two packaging barcodes. We will map both to one product; no decision needed.
- YM Trading, 24 rows: `030772033395(6)030772033418(6)21159000(3)` — a display case containing a mix of different products with counts. **Decision:** treat the display as a single purchasable item, or break it into its components?

**6. Duplicate barcodes within one file.** PCA 91 rows, YM Trading 17, TJS 8, NYB 2 — the same barcode on more than one line, usually a different pack size or a tester. Which line wins: lowest price, highest quantity, or flag them all for review?

**7. PCA lines with no barcode.** 87 rows carry PCA's item number in the barcode column (`I0089006`), a few mistyped (`10083638` for `I0083638`). We will match those on PCA's item number — but worth asking PCA whether a barcode exists for them.

**8. What should happen to products they don't sell?** Their five vendors list **6,246 distinct products**; they sell roughly 450. Most vendor rows will match nothing in the Nineyard catalog. Two options, and this shapes the whole daily experience:
- *Recommended:* record the vendor's stock and price as searchable supply data, mark it as a product not carried, and keep it out of the review queue. It then feeds the Phase 5 new-product workflow.
- *Alternative:* queue every unmatched row for a human. On PCA's file alone that is roughly 3,200 queue items on the first import.

Their own brief already separates "25 new products" from "7 unmatched products", so this is confirming an intent rather than introducing one.

**9. Is YM Trading in scope?** Their list is Air Wick, Colgate, Crest, Febreze, Tide — household and personal care — and it shares **zero** products with the four fragrance vendors. Confirm it belongs in this system rather than having been sent by mistake.

**10. Are these five the complete vendor list, or a sample?** This decides whether five import profiles is the finished configuration or the first tranche.

---

## Tier 3 — Setup and acceptance. Needed by 2 October.

**11. Where the system runs** — Azure (recommended, since they are already on Microsoft 365 and Power BI) or stay on current hosting, and who owns the subscription.

**12. Sign-in** — do users authenticate with Microsoft 365 / Entra ID? If not, a password login must be added and priced as a change request.

**13. Users and roles** — names and email addresses, each as Admin, Purchasing Manager, Data Operator or Viewer.

**14. Two small policies** — should vendor quantities be stored exactly as stated with pack size recorded alongside (recommended), or converted to their catalog unit at import? And where should the retained original files live, and for how long?

**15. Acceptance** — name the purchasing manager who will approve mappings and sign off, book a 45-minute session for the week of 6 October, and fund Phase 1 in escrow.

---

## Two things to tell them, not ask

Say these plainly so they are on record, and so neither becomes a surprise later:

- **NYB and PCA send the older Excel format** (.xls, not .xlsx) — half the data received. We are adding support rather than asking those vendors to change their systems. No cost, no action from them.
- **French Perfumes' file has no item-number column**, only barcodes. We are adjusting how their stock is recorded so their file behaves like the others. No cost, no action from them.

And one thing worth saying because it is good news and builds confidence: **616 products are carried by two or more of their vendors, 96 by three or more.** The vendor-comparison and out-of-stock features will run on real data from the first import.

---

## Suggested sequencing of the ask

Send Tier 1 and Tier 2 together now, as one message — they are all answerable by the same people in one sitting, and Tier 2 shows you have actually read their files, which makes Tier 1 more likely to be answered promptly. Hold Tier 3 until they reply, so the message is not twenty questions long. Keep the Amazon SP-API credential request in its own separate message, as before.
